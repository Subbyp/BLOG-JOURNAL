"""Application factory and top-level wiring for the blog."""

import os

from flask import Flask, g, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import db
from config import Config


def create_app(config_object=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if config_object:
        if isinstance(config_object, dict):
            app.config.update(config_object)
        else:
            app.config.from_object(config_object)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    app.config.setdefault("PREFERRED_URL_SCHEME", "https" if app.config["SITE_URL"].startswith("https://") else "http")

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)

    from blueprints.admin import bp as admin_bp
    from blueprints.auth_routes import bp as auth_bp
    from blueprints.public import bp as public_bp
    from feeds import bp as feeds_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(feeds_bp)

    auth.init_app(app)
    _register_jinja(app)
    _register_context(app)
    _register_error_handlers(app)
    _register_after_request(app)

    with app.app_context():
        db.init_db()

    return app


def _register_jinja(app):
    from content import linkify_comments, reading_time, render_markdown, word_count

    app.jinja_env.filters["markdown"] = render_markdown
    app.jinja_env.filters["reading_time"] = reading_time
    app.jinja_env.filters["word_count"] = word_count
    app.jinja_env.filters["comment_text"] = linkify_comments
    app.jinja_env.globals["now"] = _now


def _now():
    from datetime import datetime

    return datetime.utcnow()


def _page_url(page):
    """Rebuild the current URL with the page query parameter replaced."""
    from urllib.parse import urlencode

    args = request.args.to_dict(flat=True)
    args.pop("page", None)
    if page is not None:
        args["page"] = page
    query = urlencode(args)
    return request.path + ("?" + query if query else "")


def _register_context(app):
    from auth import load_logged_in_user
    from db import all_settings
    from queries import list_all_tags_with_counts

    app.before_request(load_logged_in_user)
    app.jinja_env.globals["page_url"] = _page_url

    @app.context_processor
    def inject_site():
        try:
            settings = all_settings()
        except Exception:
            settings = {}
        return {
            "site": settings,
            "nav_tags": _safe_nav_tags(),
            "current_path": request.path,
        }


def _safe_nav_tags():
    from queries import list_all_tags_with_counts

    try:
        return list_all_tags_with_counts()[:8]
    except Exception:
        return []


def _register_after_request(app):
    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        response.headers.setdefault("Content-Security-Policy", app.config["CSP"])
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        # Cache uploads aggressively, everything else not at all by default.
        if request.endpoint != "blog.media_file":
            response.headers.setdefault("Cache-Control", "no-cache")
        return response


def _register_error_handlers(app):
    @app.errorhandler(403)
    def forbidden(error):
        return render_template("error.html", code=403, message="Forbidden"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="Page not found"), 404

    @app.errorhandler(410)
    def gone(error):
        return render_template("error.html", code=410, message="Gone"), 410

    @app.errorhandler(413)
    def too_large(error):
        limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return (
            render_template(
                "error.html",
                code=413,
                message="That upload exceeds the %d MB limit." % limit,
            ),
            413,
        )

    @app.errorhandler(400)
    def bad_request(error):
        message = getattr(error, "description", None) or "Bad request"
        return render_template("error.html", code=400, message=message), 400

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Unhandled error")
        return render_template("error.html", code=500, message="Something went wrong"), 500


app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"], port=int(os.environ.get("PORT", "5000")))