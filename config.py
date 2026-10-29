import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _build_database_uri():
    explicit_database_url = os.getenv("DATABASE_URL")
    if explicit_database_url:
        return explicit_database_url

    db_engine = os.getenv("DB_ENGINE", "sqlite").lower().strip()

    if db_engine == "postgres":
        db_host = os.getenv("DB_HOST", "localhost")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "ocr")
        db_user = os.getenv("DB_USER", "ocr_user")
        db_password = quote_plus(os.getenv("DB_PASSWORD", "ocr_password"))

        return (
            "postgresql+psycopg2://"
            f"{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        )

    return f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'database.db')}"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-in-production")
    FLASK_DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
    SQLALCHEMY_DATABASE_URI = _build_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True
    }
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(4 * 1024 * 1024)))

    PLUGIN_SECURITY_ENABLED = os.getenv("PLUGIN_SECURITY_ENABLED", "1") == "1"
    PLUGIN_TOKEN_TTL_SECONDS = int(os.getenv("PLUGIN_TOKEN_TTL_SECONDS", "120"))
    PLUGIN_CLIENTS = os.getenv("PLUGIN_CLIENTS", "")
    PLUGIN_ALLOWED_ORIGINS = os.getenv("PLUGIN_ALLOWED_ORIGINS", "")

    SECURITY_HEADERS_ENABLED = os.getenv("SECURITY_HEADERS_ENABLED", "1") == "1"
    REGISTER_API_KEY = os.getenv("REGISTER_API_KEY", "")
    ADMIN_PANEL_API_KEY = os.getenv("ADMIN_PANEL_API_KEY", "")

    RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    RATE_LIMIT_TOKEN_PER_WINDOW = int(os.getenv("RATE_LIMIT_TOKEN_PER_WINDOW", "1500"))
    # Register uses streaming frames and needs a higher default burst allowance.
    RATE_LIMIT_REGISTER_PER_WINDOW = int(os.getenv("RATE_LIMIT_REGISTER_PER_WINDOW", "90"))
    # Tope por IP (red compartida por muchos empleados) y tope por sesión (una persona).
    RATE_LIMIT_AUTH_PER_WINDOW = int(os.getenv("RATE_LIMIT_AUTH_PER_WINDOW", "30000"))
    RATE_LIMIT_AUTH_SESSION_PER_WINDOW = int(os.getenv("RATE_LIMIT_AUTH_SESSION_PER_WINDOW", "150"))