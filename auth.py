"""Authentication: password hashing, session handling, CSRF, rate limiting."""

import functools
import hashlib
import hmac
import secrets
import time

from flask import (
    abort,
    current_app,
    flash,
    g,
    redirect,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(raw):
    return generate_password_hash(raw)


def verify_password(password_hash, raw):
    if not password_hash or not raw:
        return False
    try:
        return check_password_hash(password_hash, raw)
    except (ValueError, TypeError):
        return False


def validate_password(raw):
    """Return an error string, or None when the password is acceptable."""
    if len(raw) < 8:
        return "Password must be at least 8 characters."
    if len(raw) > 200:
        return "Password must be 200 characters or fewer."
    if raw.strip() != raw:
        return "Password must not begin or end with whitespace."
    return None


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
        return
    from db import get_db

    g.user = (
        get_db()
        .execute(
            "SELECT id, username, email, is_admin FROM users WHERE id = ?", (user_id,)
        )
        .fetchone()
    )
    if g.user is None:
        # Stale session pointing at a deleted user.
        session.clear()
        g.user = None


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def safe_redirect_target(target):
    """Only allow same-site relative paths as a post-login destination."""
    if not target:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target or "\n" in target or "\r" in target:
        return None
    return target


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf():
    sent = request.form.get("csrf_token", "")
    stored = session.get("csrf_token", "")
    if not sent or not stored or not hmac.compare_digest(sent, stored):
        abort(400, description="Invalid or missing CSRF token.")


def init_app(app):
    app.before_request(_rotate_csrf)
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["current_user"] = lambda: g.get("user")


def _rotate_csrf():
    csrf_token()
    if request.method not in SAFE_METHODS:
        validate_csrf()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
def client_fingerprint():
    """Hash the client IP with the app secret so raw addresses are not stored."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "")
    return hashlib.sha256(
        ("%s|%s" % (current_app.config["SECRET_KEY"], ip)).encode("utf-8")
    ).hexdigest()


def record_login_failure():
    from db import get_db

    db = get_db()
    db.execute(
        "INSERT INTO login_attempts (ip_hash, created_at) VALUES (?, ?)",
        (client_fingerprint(), int(time.time())),
    )
    db.commit()


def login_is_throttled():
    """True when this client has exceeded the allowed failed-attempt budget."""
    from db import get_db

    max_attempts = current_app.config["LOGIN_MAX_ATTEMPTS"]
    window = current_app.config["LOGIN_ATTEMPT_WINDOW"]
    cutoff = int(time.time()) - window

    row = (
        get_db()
        .execute(
            "SELECT count(*) FROM login_attempts WHERE ip_hash = ? AND created_at > ?",
            (client_fingerprint(), cutoff),
        )
        .fetchone()
    )
    return row[0] >= max_attempts


def clear_login_failures():
    from db import get_db

    get_db().execute(
        "DELETE FROM login_attempts WHERE ip_hash = ?", (client_fingerprint(),)
    )
    get_db().commit()


def prune_login_attempts(older_than_seconds=None):
    """Delete stale attempt rows. Called opportunistically."""
    from db import get_db

    window = older_than_seconds or current_app.config["LOGIN_ATTEMPT_WINDOW"]
    db = get_db()
    db.execute(
        "DELETE FROM login_attempts WHERE created_at < ?",
        (int(time.time()) - window * 4,),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
def login_user(user_row, remember=False):
    session.clear()
    session["user_id"] = user_row["id"]
    session["csrf_token"] = secrets.token_hex(32)
    session.permanent = True
    clear_login_failures()


def logout_user():
    session.clear()


def flash_errors(messages):
    for message in messages:
        flash(message, "error")