"""Authentication routes: login and logout."""

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from auth import (
    login_is_throttled,
    login_user,
    logout_user,
    prune_login_attempts,
    record_login_failure,
    safe_redirect_target,
    verify_password,
)
from queries import get_user_by_username

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user") is not None:
        return redirect(url_for("admin.dashboard"))

    next_url = safe_redirect_target(request.args.get("next")) or url_for(
        "admin.dashboard"
    )

    if request.method == "POST":
        if login_is_throttled():
            flash("Too many failed attempts. Please try again later.", "error")
            return render_template("login.html"), 429

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        next_url = safe_redirect_target(request.form.get("next")) or next_url

        user = get_user_by_username(username)
        if user and verify_password(user["password_hash"], password):
            login_user(user, remember=bool(request.form.get("remember")))
            prune_login_attempts()
            flash("Welcome back, %s." % user["username"], "success")
            return redirect(next_url)

        record_login_failure()
        flash("Invalid username or password.", "error")

    return render_template("login.html", next=next_url)


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))