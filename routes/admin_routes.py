import hmac
import os
import re

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

from database import db
from models import LoginHistory, User
from services.api_key_service import ApiKeyService
from services.face_auth_service import FaceAuthService


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
face_auth_service = FaceAuthService(
    duplicate_face_threshold=float(os.getenv("DUPLICATE_FACE_THRESHOLD", "0.92"))
)
api_key_service = ApiKeyService()

_DOCUMENT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{4,30}$")


def _is_admin_authenticated():
    return session.get("admin_authenticated") is True


def _require_admin_session():
    if _is_admin_authenticated():
        return None

    if request.path.startswith("/admin/api/"):
        return jsonify({"ok": False, "error": "No autorizado."}), 401

    return redirect(url_for("admin.login"))


def _error_response(message, status_code):
    return jsonify({"ok": False, "error": message}), status_code


def _remote_ip():
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return forwarded or (request.remote_addr or "unknown")


def _validate_admin_payload(nombre, documento):
    if not nombre or not documento:
        raise ValueError("Nombre y documento son obligatorios.")

    nombre = nombre.strip()
    documento = documento.strip()

    if len(nombre) < 2 or len(nombre) > 100:
        raise ValueError("Nombre invalido: debe tener entre 2 y 100 caracteres.")

    if not _DOCUMENT_PATTERN.match(documento):
        raise ValueError("Identificacion invalida: usa 4-30 caracteres alfanumericos, punto, guion o guion bajo.")


@admin_bp.before_request
def admin_before_request():
    open_paths = {"admin.login", "admin.login_submit"}

    if request.endpoint in open_paths:
        return None

    return _require_admin_session()


@admin_bp.get("/")
def home():
    return redirect(url_for("admin.users_panel"))


@admin_bp.get("/login")
def login():
    if _is_admin_authenticated():
        return redirect(url_for("admin.users_panel"))

    return render_template("admin_login.html", error=None)


@admin_bp.post("/login")
def login_submit():
    provided_key = (request.form.get("api_key") or "").strip()

    if not provided_key:
        return render_template("admin_login.html", error="Debes ingresar una API key."), 400

    validated_key, validation_error = api_key_service.verify_key(
        raw_key=provided_key,
        required_scopes=["admin:login"],
        remote_ip=_remote_ip()
    )

    if validated_key is None:
        expected_key = current_app.config.get("ADMIN_PANEL_API_KEY", "")

        if not expected_key:
            return render_template(
                "admin_login.html",
                error=(
                    "No hay API keys activas para admin. "
                    "Configura ADMIN_PANEL_API_KEY temporalmente o crea una API key desde backend."
                )
            ), 500

        if not hmac.compare_digest(provided_key, expected_key):
            return render_template("admin_login.html", error="API key invalida."), 401

    session["admin_api_key_id"] = validated_key.id if validated_key else None
    session["admin_authenticated"] = True
    return redirect(url_for("admin.users_panel"))


@admin_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@admin_bp.get("/users")
def users_panel():
    return render_template("admin_users.html")


@admin_bp.get("/api/apikeys")
def list_api_keys():
    return jsonify({"ok": True, "data": api_key_service.list_keys()})


@admin_bp.post("/api/apikeys")
def create_api_key():
    if not request.is_json:
        return _error_response("Se requiere Content-Type application/json.", 400)

    payload = request.get_json(silent=True) or {}

    try:
        api_key, raw_key = api_key_service.create_key(
            name=payload.get("name", ""),
            scopes=payload.get("scopes", []),
            expires_in_days=payload.get("expires_in_days"),
            client_id=payload.get("client_id")
        )
    except ValueError as error:
        return _error_response(str(error), 400)
    except Exception:
        current_app.logger.exception("Error interno en /admin/api/apikeys")
        return _error_response("Error interno del servidor.", 500)

    return jsonify({
        "ok": True,
        "data": {
            "id": api_key.id,
            "name": api_key.name,
            "key_prefix": api_key.key_prefix,
            "scopes": api_key.scopes.split(",") if api_key.scopes else [],
            "client_id": api_key.client_id,
            "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
            "api_key": raw_key
        }
    }), 201


@admin_bp.post("/api/apikeys/<int:key_id>/revoke")
def revoke_api_key(key_id):
    try:
        key = api_key_service.revoke_key(key_id)
    except ValueError as error:
        return _error_response(str(error), 404)
    except Exception:
        current_app.logger.exception("Error interno en /admin/api/apikeys/%s/revoke", key_id)
        return _error_response("Error interno del servidor.", 500)

    return jsonify({
        "ok": True,
        "data": {
            "id": key.id,
            "is_active": key.is_active,
            "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None
        }
    })


@admin_bp.get("/api/users")
def list_users():
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 10, type=int), 1), 100)
    term = (request.args.get("q") or "").strip()

    # El embedding no se usa en el listado: se difiere para no leer el BLOB.
    query = User.query.options(db.defer(User.embedding))

    if term:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.filter(
            db.or_(
                User.nombre.ilike(pattern, escape="\\"),
                User.documento.ilike(pattern, escape="\\")
            )
        )

    total = query.count()
    pages = max((total + per_page - 1) // per_page, 1)
    page = min(page, pages)

    users = (
        query.order_by(User.fecha_creacion.desc(), User.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    data = [
        {
            "id": user.id,
            "nombre": user.nombre,
            "documento": user.documento,
            "face_hash": user.face_hash,
            "fecha_creacion": user.fecha_creacion.isoformat() if user.fecha_creacion else None
        }
        for user in users
    ]

    return jsonify({
        "ok": True,
        "data": data,
        "pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages}
    })


@admin_bp.post("/api/users")
def create_user():
    if not request.is_json:
        return _error_response("Se requiere Content-Type application/json.", 400)

    payload = request.get_json(silent=True) or {}

    try:
        result = face_auth_service.register_user(
            nombre=payload.get("nombre", ""),
            documento=payload.get("documento", ""),
            frame_data=payload.get("frame", ""),
            liveness_key=f"admin-register:{_remote_ip()}"
        )
    except ValueError as error:
        return _error_response(str(error), 400)
    except Exception:
        current_app.logger.exception("Error interno en /admin/api/users")
        return _error_response("Error interno del servidor.", 500)

    return jsonify({"ok": True, "data": result}), 201


@admin_bp.put("/api/users/<int:user_id>")
def update_user(user_id):
    if not request.is_json:
        return _error_response("Se requiere Content-Type application/json.", 400)

    user = User.query.get(user_id)
    if not user:
        return _error_response("Usuario no encontrado.", 404)

    payload = request.get_json(silent=True) or {}
    nombre = (payload.get("nombre") or "").strip()
    documento = (payload.get("documento") or "").strip()

    try:
        _validate_admin_payload(nombre, documento)
    except ValueError as error:
        return _error_response(str(error), 400)

    existing_document = User.query.filter(User.documento == documento, User.id != user_id).first()
    if existing_document:
        return _error_response("Ya existe un usuario con esa identificacion.", 400)

    user.nombre = nombre
    user.documento = documento

    db.session.commit()

    return jsonify({
        "ok": True,
        "data": {
            "id": user.id,
            "nombre": user.nombre,
            "documento": user.documento,
            "face_hash": user.face_hash,
            "fecha_creacion": user.fecha_creacion.isoformat() if user.fecha_creacion else None
        }
    })


@admin_bp.delete("/api/users/<int:user_id>")
def delete_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return _error_response("Usuario no encontrado.", 404)

    LoginHistory.query.filter_by(user_id=user_id).update({"user_id": None})
    db.session.delete(user)
    db.session.commit()

    return jsonify({"ok": True})
