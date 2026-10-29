import cv2
import numpy as np

from insightface.app import FaceAnalysis
from insightface.app.common import Face


class FaceRecognizer:

    def __init__(self):

        # buffalo_l trae también landmark 3D/2D y edad/género, que aquí no se usan:
        # FaceAnalysis.get() los ejecutaría en cada fotograma. Los puntos clave (kps)
        # los entrega el detector, y el embedding el modelo de reconocimiento.
        self.app = FaceAnalysis(
            name="buffalo_l",
            allowed_modules=["detection", "recognition"]
        )

        try:
            self.app.prepare(
                ctx_id=0,
                det_size=(640, 640)
            )
        except Exception:
            # Fallback for environments without GPU providers.
            self.app.prepare(
                ctx_id=-1,
                det_size=(640, 640)
            )

    def _get_single_face(self, image, with_embedding=True):

        if not with_embedding:
            # Solo detección (bbox + 5 puntos clave): suficiente para validar
            # obstrucciones y liveness sin correr el modelo de reconocimiento.
            bboxes, kpss = self.app.det_model.detect(image, max_num=0, metric="default")

            if bboxes.shape[0] == 0:
                raise ValueError("No se detectó ningún rostro.")

            if bboxes.shape[0] > 1:
                raise ValueError("Se detectaron múltiples rostros.")

            return Face(
                bbox=bboxes[0, 0:4],
                kps=kpss[0] if kpss is not None else None,
                det_score=bboxes[0, 4]
            )

        faces = self.app.get(image)

        if len(faces) == 0:
            raise ValueError("No se detectó ningún rostro.")

        if len(faces) > 1:
            raise ValueError("Se detectaron múltiples rostros.")

        return faces[0]

    @staticmethod
    def _extract_patch(gray_image, x, y, radius):
        height, width = gray_image.shape[:2]

        x0 = max(0, int(x - radius))
        y0 = max(0, int(y - radius))
        x1 = min(width, int(x + radius))
        y1 = min(height, int(y + radius))

        if x1 <= x0 or y1 <= y0:
            return None

        return gray_image[y0:y1, x0:x1]

    @staticmethod
    def _patch_texture_score(gray_patch):
        if gray_patch is None or gray_patch.size == 0:
            return 0.0

        lap = cv2.Laplacian(gray_patch, cv2.CV_64F)
        return float(lap.var())

    def validate_unobstructed_face(self, image):
        face = self._get_single_face(image, with_embedding=False)

        kps = getattr(face, "kps", None)
        if kps is None or len(kps) < 5:
            return False, ["No se pudieron validar los puntos faciales."]

        kps = np.asarray(kps, dtype=np.float32)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        inter_eye_distance = float(np.linalg.norm(kps[0] - kps[1]))
        patch_radius = max(10, int(inter_eye_distance * 0.18))

        left_eye_score = self._patch_texture_score(
            self._extract_patch(gray, kps[0][0], kps[0][1], patch_radius)
        )
        right_eye_score = self._patch_texture_score(
            self._extract_patch(gray, kps[1][0], kps[1][1], patch_radius)
        )
        nose_score = self._patch_texture_score(
            self._extract_patch(gray, kps[2][0], kps[2][1], patch_radius)
        )
        mouth_left_score = self._patch_texture_score(
            self._extract_patch(gray, kps[3][0], kps[3][1], patch_radius)
        )
        mouth_right_score = self._patch_texture_score(
            self._extract_patch(gray, kps[4][0], kps[4][1], patch_radius)
        )

        reasons = []

        eyes_avg = (left_eye_score + right_eye_score) / 2.0
        mouth_avg = (mouth_left_score + mouth_right_score) / 2.0

        # Conservative heuristics: low local texture around eyes usually indicates
        # strong reflections/occlusion; very low texture on lower-face points
        # indicates possible mask coverage.
        if eyes_avg < 38.0:
            reasons.append("Retira gafas o evita reflejos sobre los ojos.")

        if nose_score < 22.0 or mouth_avg < 18.0:
            reasons.append("Retira cubrebocas para que nariz y boca sean visibles.")

        return len(reasons) == 0, reasons

    def get_eye_texture_score(self, image):
        face = self._get_single_face(image, with_embedding=False)

        kps = getattr(face, "kps", None)
        if kps is None or len(kps) < 2:
            raise ValueError("No se pudieron validar los ojos para liveness.")

        kps = np.asarray(kps, dtype=np.float32)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        inter_eye_distance = float(np.linalg.norm(kps[0] - kps[1]))
        patch_radius = max(10, int(inter_eye_distance * 0.18))

        left_eye_score = self._patch_texture_score(
            self._extract_patch(gray, kps[0][0], kps[0][1], patch_radius)
        )
        right_eye_score = self._patch_texture_score(
            self._extract_patch(gray, kps[1][0], kps[1][1], patch_radius)
        )

        return (left_eye_score + right_eye_score) / 2.0

    def get_liveness_metrics(self, image):
        face = self._get_single_face(image, with_embedding=False)

        kps = getattr(face, "kps", None)
        if kps is None or len(kps) < 3:
            raise ValueError("No se pudieron validar metricas de liveness.")

        kps = np.asarray(kps, dtype=np.float32)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        inter_eye_distance = float(np.linalg.norm(kps[0] - kps[1]))
        patch_radius = max(10, int(inter_eye_distance * 0.18))

        left_eye_score = self._patch_texture_score(
            self._extract_patch(gray, kps[0][0], kps[0][1], patch_radius)
        )
        right_eye_score = self._patch_texture_score(
            self._extract_patch(gray, kps[1][0], kps[1][1], patch_radius)
        )

        return {
            "eye_score": (left_eye_score + right_eye_score) / 2.0,
            "nose": np.asarray(kps[2], dtype=np.float32),
            "inter_eye_distance": max(1.0, inter_eye_distance)
        }

    def get_embedding(self, image):

        face = self._get_single_face(image)

        return face.embedding