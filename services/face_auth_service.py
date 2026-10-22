import base64
import binascii
import hashlib
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from sqlalchemy.exc import IntegrityError

from database import db
from models import LoginHistory, User
from services.recognizer import FaceRecognizer


class FaceAuthService:

    DOCUMENT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{4,30}$")
    BOGOTA_TZ = ZoneInfo("America/Bogota")

    def __init__(self, similarity_threshold=0.45, duplicate_face_threshold=0.92):
        self.recognizer = FaceRecognizer()
        self.similarity_threshold = similarity_threshold
        self.duplicate_face_threshold = duplicate_face_threshold
        self._liveness_states = {}

    def _check_blink_liveness(self, image, liveness_key):
        if not liveness_key:
            return True

        now = time.time()
        state = self._liveness_states.get(liveness_key)

        if state is None or now - state["updated_at"] > 20.0:
            state = {
                "open_reference": 0.0,
                "phase": "collect_open",
                "frame_count": 0,
                "open_frames": 0,
                "closed_frames": 0,
                "reopen_frames": 0,
                "movement_frames": 0,
                "blink_confirmed": False,
                "movement_confirmed": False,
                "baseline_nose": None,
                "smoothed_eye_score": None,
                "updated_at": now
            }
            self._liveness_states[liveness_key] = state

        metrics = self.recognizer.get_liveness_metrics(image)
        eye_score = float(metrics["eye_score"])
        nose = np.asarray(metrics["nose"], dtype=np.float32)
        inter_eye_distance = float(metrics["inter_eye_distance"])

        previous_smoothed = state["smoothed_eye_score"]
        if previous_smoothed is None:
            smoothed_eye_score = eye_score
        else:
            smoothed_eye_score = (previous_smoothed * 0.65) + (eye_score * 0.35)

        state["smoothed_eye_score"] = smoothed_eye_score
        state["frame_count"] += 1
        state["updated_at"] = now

        min_open_reference = 30.0
        close_ratio = 0.66
        reopen_ratio = 0.84
        movement_ratio_threshold = 0.16
        min_open_frames = 3
        min_closed_frames = 2
        min_reopen_frames = 2
        min_movement_frames = 2
        min_total_frames = 6

        baseline_nose = state["baseline_nose"]
        if baseline_nose is None:
            state["baseline_nose"] = nose
        else:
            state["baseline_nose"] = (baseline_nose * 0.85) + (nose * 0.15)

        if state["phase"] == "collect_open":
            if smoothed_eye_score >= min_open_reference:
                state["open_frames"] += 1
                state["open_reference"] = max(state["open_reference"], smoothed_eye_score)

            if state["open_frames"] >= min_open_frames:
                state["phase"] = "wait_action"
            return False

        open_reference = max(state["open_reference"], min_open_reference)
        close_threshold = open_reference * close_ratio
        reopen_threshold = open_reference * reopen_ratio

        movement_ratio = float(np.linalg.norm(nose - state["baseline_nose"]) / inter_eye_distance)
        if movement_ratio >= movement_ratio_threshold:
            state["movement_frames"] += 1
        else:
            state["movement_frames"] = max(0, state["movement_frames"] - 1)

        if state["movement_frames"] >= min_movement_frames:
            state["movement_confirmed"] = True

        if state["phase"] == "wait_action":
            if smoothed_eye_score <= close_threshold:
                state["closed_frames"] += 1
            else:
                state["closed_frames"] = 0

            if state["closed_frames"] >= min_closed_frames:
                state["phase"] = "wait_reopen"

            if (
                state["movement_confirmed"]
                and state["frame_count"] >= min_total_frames
            ):
                return True

            return False

        if state["phase"] == "wait_reopen":
            if smoothed_eye_score >= reopen_threshold:
                state["reopen_frames"] += 1
            else:
                state["reopen_frames"] = 0

            if state["reopen_frames"] >= min_reopen_frames:
                state["blink_confirmed"] = True

            if (state["blink_confirmed"] or state["movement_confirmed"]) and state["frame_count"] >= min_total_frames:
                return True

            return False

        return False

    def _clear_liveness_state(self, liveness_key):
        if liveness_key:
            self._liveness_states.pop(liveness_key, None)

    @staticmethod
    def decode_frame(data_url):
        if not data_url:
            raise ValueError("No se recibió ningún frame.")

        if "," in data_url:
            _, encoded = data_url.split(",", 1)
        else:
            encoded = data_url

        if len(encoded) > 8_000_000:
            raise ValueError("El frame es demasiado grande.")

        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("El frame no tiene un formato valido.") from error

        buffer = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)

        if image is None:
            raise ValueError("No se pudo decodificar la imagen.")

        return image

    @staticmethod
    def _normalize_embedding(embedding):
        emb = np.asarray(embedding, dtype=np.float32)
        norm = np.linalg.norm(emb)

        if norm == 0:
            raise ValueError("Embedding inválido: norma cero.")

        return emb / norm

    def build_face_hash(self, embedding):
        normalized = self._normalize_embedding(embedding)

        # Keep the hash deterministic while reducing tiny floating point noise.
        rounded = np.round(normalized, 6)
        return hashlib.sha256(rounded.tobytes()).hexdigest()

    def _similarity_scores(self, probe, blobs):
        """Cosine similarity of a normalized probe against stored embeddings, in one matrix product."""
        if not blobs:
            return np.empty(0, dtype=np.float32)

        matrix = np.stack([self.deserialize_embedding(blob) for blob in blobs])
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0

        return (matrix / norms) @ probe

    @staticmethod
    def serialize_embedding(embedding):
        emb = np.asarray(embedding, dtype=np.float32)
        return emb.tobytes()

    @staticmethod
    def deserialize_embedding(blob):
        emb = np.frombuffer(blob, dtype=np.float32)
        return emb

    @classmethod
    def _event_time_payload(cls):
        now_utc = datetime.now(timezone.utc)
        now_bogota = now_utc.astimezone(cls.BOGOTA_TZ)

        return {
            "event_time_bogota": now_bogota.isoformat(timespec="milliseconds"),
            "event_time_utc": now_utc.isoformat(timespec="milliseconds"),
            "event_epoch_ms": int(now_utc.timestamp() * 1000)
        }

    def register_user(self, nombre, documento, frame_data, liveness_key=None):
        if not nombre or not documento:
            raise ValueError("Nombre y documento son obligatorios.")

        nombre = nombre.strip()
        documento = documento.strip()

        if len(nombre) < 2 or len(nombre) > 100:
            raise ValueError("Nombre invalido: debe tener entre 2 y 100 caracteres.")

        if not self.DOCUMENT_PATTERN.match(documento):
            raise ValueError("Identificacion invalida: usa 4-30 caracteres alfanumericos, punto, guion o guion bajo.")

        existing = User.query.filter_by(documento=documento).first()
        if existing:
            raise ValueError("Ya existe un usuario con ese documento.")

        image = self.decode_frame(frame_data)

        if not self._check_blink_liveness(image, liveness_key):
            raise ValueError("Verificacion de vida pendiente: parpadea o mueve ligeramente el rostro frente a la camara.")

        is_valid_face, warnings = self.recognizer.validate_unobstructed_face(image)
        if not is_valid_face:
            details = " ".join(warnings)
            raise ValueError(
                "Advertencia: no se puede registrar el rostro con gafas, cubrebocas o zonas faciales ocultas. "
                f"{details}".strip()
            )

        embedding = self.recognizer.get_embedding(image)
        normalized_probe = self._normalize_embedding(embedding)

        face_hash = self.build_face_hash(embedding)

        if User.query.filter_by(face_hash=face_hash).first():
            raise ValueError("Este rostro ya está registrado en el sistema.")

        # Defend against near-duplicate embeddings caused by small camera/noise changes.
        existing_blobs = [row.embedding for row in User.query.with_entities(User.embedding).all()]
        duplicate_scores = self._similarity_scores(normalized_probe, existing_blobs)
        best_duplicate_score = float(duplicate_scores.max()) if duplicate_scores.size else -1.0

        if best_duplicate_score >= self.duplicate_face_threshold:
            raise ValueError("Este rostro ya está registrado en el sistema.")

        # Additional safeguard: if the face is clearly recognized as existing,
        # block registration even when the strict duplicate threshold is not met.
        soft_duplicate_threshold = max(self.similarity_threshold + 0.15, 0.60)
        if best_duplicate_score >= soft_duplicate_threshold:
            raise ValueError("Este rostro ya está registrado en el sistema.")

        user = User(
            nombre=nombre,
            documento=documento,
            face_hash=face_hash,
            embedding=self.serialize_embedding(embedding)
        )

        db.session.add(user)

        try:
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()

            message = str(getattr(error, "orig", error)).lower()
            if "face_hash" in message or "users_face_hash" in message or "unique" in message:
                raise ValueError("Este rostro ya está registrado en el sistema.") from error

            if "documento" in message:
                raise ValueError("Ya existe un usuario con esa identificacion.") from error

            raise

        self._clear_liveness_state(liveness_key)

        event_time = self._event_time_payload()

        return {
            "id": user.id,
            "nombre": user.nombre,
            "documento": user.documento,
            "face_hash": user.face_hash,
            "registered_at_bogota": event_time["event_time_bogota"],
            "registered_at_utc": event_time["event_time_utc"],
            "registered_at_epoch_ms": event_time["event_epoch_ms"]
        }

    def authenticate(self, frame_data, liveness_key=None):
        image = self.decode_frame(frame_data)

        if not self._check_blink_liveness(image, liveness_key):
            raise ValueError("Verificacion de vida pendiente: parpadea o mueve ligeramente el rostro frente a la camara.")

        probe_embedding = self._normalize_embedding(self.recognizer.get_embedding(image))

        users = User.query.with_entities(
            User.id, User.nombre, User.documento, User.face_hash, User.embedding
        ).all()

        if len(users) == 0:
            self._store_login_attempt(None, False, 0.0)
            return {
                "authenticated": False,
                "message": "No hay usuarios registrados."
            }

        scores = self._similarity_scores(probe_embedding, [user.embedding for user in users])
        best_index = int(np.argmax(scores))
        best_user = users[best_index]
        best_score = float(scores[best_index])

        is_authenticated = best_user is not None and best_score >= self.similarity_threshold
        self._store_login_attempt(best_user.id if is_authenticated else None, is_authenticated, best_score)

        if not is_authenticated:
            return {
                "authenticated": False,
                "score": round(best_score, 4),
                "threshold": self.similarity_threshold,
                "message": "Rostro no reconocido."
            }

        self._clear_liveness_state(liveness_key)

        event_time = self._event_time_payload()

        return {
            "authenticated": True,
            "score": round(best_score, 4),
            "threshold": self.similarity_threshold,
            "authenticated_at_bogota": event_time["event_time_bogota"],
            "authenticated_at_utc": event_time["event_time_utc"],
            "authenticated_at_epoch_ms": event_time["event_epoch_ms"],
            "user": {
                "id": best_user.id,
                "nombre": best_user.nombre,
                "documento": best_user.documento,
                "face_hash": best_user.face_hash,
                "authenticated_at_bogota": event_time["event_time_bogota"],
                "authenticated_at_utc": event_time["event_time_utc"],
                "authenticated_at_epoch_ms": event_time["event_epoch_ms"]
            }
        }

    @staticmethod
    def _store_login_attempt(user_id, resultado, score):
        attempt = LoginHistory(
            user_id=user_id,
            resultado=resultado,
            score=float(score)
        )

        db.session.add(attempt)
        db.session.commit()
