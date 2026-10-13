"""Application configuration, loaded from the environment."""

import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")


def _bool_env(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # --- Core -------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    DATABASE = os.environ.get("DATABASE") or os.path.join(INSTANCE_DIR, "blog.db")
    SCHEMA = os.path.join(BASE_DIR, "schema.sql")

    # --- Uploads ----------------------------------------------------------
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(INSTANCE_DIR, "uploads")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "8")) * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "avif"}

    # --- Behaviour --------------------------------------------------------
    POSTS_PER_PAGE = int(os.environ.get("POSTS_PER_PAGE", "8"))
    COMMENTS_PER_PAGE = int(os.environ.get("COMMENTS_PER_PAGE", "25"))
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    SITE_URL = (os.environ.get("SITE_URL") or "http://localhost:5000").rstrip("/")
    DEBUG = _bool_env("FLASK_DEBUG", False)

    # Session hardening. HTTPS-only cookies are enabled automatically when
    # SITE_URL points at an https origin.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = SITE_URL.startswith("https://")
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    # Throttling: max login attempts per IP per window.
    LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "8"))
    LOGIN_ATTEMPT_WINDOW = int(os.environ.get("LOGIN_ATTEMPT_WINDOW", "900"))

    # --- Security headers --------------------------------------------------
    CSP = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self'; "
        "script-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "object-src 'none'"
    )