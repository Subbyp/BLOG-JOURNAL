"""Admin routes: dashboard, posts, comments, media, users, settings."""

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from auth import (
    admin_required,
    hash_password,
    login_required,
    validate_password,
    verify_password,
)
from content import excerpt_from, reading_time
from db import all_settings, get_db, set_setting
from posts import (
    create_post,
    delete_post,
    get_full_post,
    set_status,
    toggle_featured,
    update_post,
)
from queries import (
    admin_user_count,
    create_user,
    dashboard_stats,
    delete_media,
    get_media,
    get_user,
    list_all_posts,
    list_comments,
    list_media,
    list_users,
    parse_tag_input,
    recent_comment_count,
    top_posts,
    update_media,
    username_exists,
)
from uploads import UploadError, delete_file, save_image

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _want_json():
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
def dashboard():
    stats = dashboard_stats()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        recent_comments=recent_comment_count(7),
        top=top_posts(),
        pending=list_comments({"page": 1}, status="pending", per_page=5)[0],
        recent_posts=list_all_posts({"page": 1}, per_page=5)[0],
        page_title="Dashboard",
    )


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------
@bp.route("/posts")
@login_required
def posts():
    status = request.args.get("status")
    if status not in {"draft", "published"}:
        status = None
    items, pagination = list_all_posts(request.args, status=status)
    return render_template(
        "admin/posts.html",
        posts=items,
        pagination=pagination,
        status=status,
        page_title="Posts",
    )


def _form_to_tags(form):
    if form.get("tags"):
        return parse_tag_input(form["tags"])
    return parse_tag_input(",".join(form.getlist("tag")))


@bp.route("/posts/new", methods=["GET", "POST"])
@login_required
def new_post():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        content = request.form.get("content") or ""
        if not title:
            flash("A title is required.", "error")
            return render_template(
                "admin/editor.html", post=dict(request.form), tags=[], mode="new"
            ), 400

        post_id = create_post(
            title=title,
            content=content,
            slug=(request.form.get("slug") or "").strip() or None,
            excerpt=(request.form.get("excerpt") or "").strip(),
            status="published" if request.form.get("publish") == "1" else "draft",
            is_featured=1 if request.form.get("is_featured") == "1" else 0,
            allow_comments=1 if request.form.get("allow_comments", "1") == "1" else 0,
            cover_image=(request.form.get("cover_image") or "").strip() or None,
            tags=_form_to_tags(request.form),
            published_at=(request.form.get("published_at") or "").strip() or None,
        )
        flash("Post created.", "success")
        return redirect(url_for("admin.edit_post", post_id=post_id))

    return render_template(
        "admin/editor.html", post=None, tags=[], mode="new", page_title="New post"
    )


@bp.route("/posts/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = get_full_post(post_id)
    if post is None:
        abort(404)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        content = request.form.get("content") or ""
        if not title:
            flash("A title is required.", "error")
            return render_template(
                "admin/editor.html",
                post=get_full_post(post_id),
                tags=[t["name"] for t in get_full_post(post_id)["tags"]],
                mode="edit",
                page_title="Edit post",
            ), 400

        update_post(
            post_id,
            title=title,
            content=content,
            slug=(request.form.get("slug") or "").strip() or None,
            excerpt=(request.form.get("excerpt") or "").strip(),
            status="published" if request.form.get("publish") == "1" else "draft",
            is_featured=1 if request.form.get("is_featured") == "1" else 0,
            allow_comments=1 if request.form.get("allow_comments", "1") == "1" else 0,
            cover_image=(request.form.get("cover_image") or "").strip() or None,
            tags=_form_to_tags(request.form),
            published_at=(request.form.get("published_at") or "").strip() or None,
        )
        flash("Post saved.", "success")
        if request.form.get("continue") == "1":
            return redirect(url_for("admin.edit_post", post_id=post_id))
        return redirect(url_for("admin.posts"))

    return render_template(
        "admin/editor.html",
        post=post,
        tags=[t["name"] for t in post["tags"]],
        mode="edit",
        reading_time=reading_time(post["content"]),
        page_title="Edit post",
    )


@bp.route("/posts/<int:post_id>/status", methods=["POST"])
@login_required
def change_status(post_id):
    status = request.form.get("status")
    if not set_status(post_id, status):
        abort(404)
    flash("Post marked as %s." % status, "success")
    return redirect(request.referrer or url_for("admin.posts"))


@bp.route("/posts/<int:post_id>/feature", methods=["POST"])
@login_required
def feature_post(post_id):
    if get_full_post(post_id) is None:
        abort(404)
    toggle_featured(post_id)
    flash("Featured flag updated.", "success")
    return redirect(request.referrer or url_for("admin.posts"))


@bp.route("/posts/<int:post_id>/delete", methods=["POST"])
@admin_required
def remove_post(post_id):
    if not delete_post(post_id):
        abort(404)
    flash("Post deleted.", "success")
    return redirect(url_for("admin.posts"))


@bp.route("/posts/<int:post_id>/preview")
@login_required
def preview_post(post_id):
    from blueprints.public import preview_token

    if get_full_post(post_id) is None:
        abort(404)
    return redirect(url_for("blog.preview", token=preview_token(post_id)))


@bp.route("/posts/<int:post_id>/excerpt")
@login_required
def generate_excerpt(post_id):
    post = get_full_post(post_id)
    if post is None:
        abort(404)
    return jsonify({"excerpt": excerpt_from(post["content"])})


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------
@bp.route("/comments")
@admin_required
def comments():
    status = request.args.get("status")
    if status not in {"pending", "approved", "spam"}:
        status = None
    items, pagination = list_comments(request.args, status=status)
    counts = {
        "all": list_comments({"page": 1}, per_page=1)[1]["total"],
        "pending": list_comments({"page": 1}, status="pending", per_page=1)[1]["total"],
        "approved": list_comments({"page": 1}, status="approved", per_page=1)[1]["total"],
        "spam": list_comments({"page": 1}, status="spam", per_page=1)[1]["total"],
    }
    return render_template(
        "admin/comments.html",
        comments=items,
        pagination=pagination,
        status=status,
        counts=counts,
        page_title="Comments",
    )


@bp.route("/comments/<int:comment_id>/status", methods=["POST"])
@admin_required
def comment_status(comment_id):
    from comments import set_comment_status

    new_status = request.form.get("status")
    if not set_comment_status(comment_id, new_status):
        abort(404)
    if _want_json():
        return jsonify({"ok": True, "status": new_status})
    flash("Comment marked as %s." % new_status, "success")
    return redirect(request.referrer or url_for("admin.comments"))


@bp.route("/comments/<int:comment_id>/delete", methods=["POST"])
@admin_required
def comment_delete(comment_id):
    from comments import delete_comment

    if not delete_comment(comment_id):
        abort(404)
    if _want_json():
        return jsonify({"ok": True})
    flash("Comment deleted.", "success")
    return redirect(request.referrer or url_for("admin.comments"))


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------
@bp.route("/media")
@login_required
def media():
    items, pagination = list_media(request.args)
    return render_template(
        "admin/media.html",
        media=items,
        pagination=pagination,
        page_title="Media",
    )


@bp.route("/media/upload", methods=["POST"])
@login_required
def media_upload():
    file = request.files.get("image") or request.files.get("file")
    if file is None or not file.filename:
        if _want_json():
            return jsonify({"ok": False, "error": "No file provided."}), 400
        flash("No file provided.", "error")
        return redirect(url_for("admin.media"))

    try:
        media_id = save_image(
            file, alt_text=(request.form.get("alt_text") or ""), user_id=g.user["id"]
        )
    except UploadError as exc:
        if _want_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "error")
        return redirect(url_for("admin.media"))

    item = get_media(media_id)
    url = url_for("blog.media_file", filename=item["filename"])
    if _want_json():
        return jsonify(
            {
                "ok": True,
                "id": media_id,
                "url": url,
                "filename": item["filename"],
                "alt": item["alt_text"],
            }
        )
    flash("Image uploaded.", "success")
    return redirect(url_for("admin.media"))


@bp.route("/media/<int:media_id>/alt", methods=["POST"])
@login_required
def media_alt(media_id):
    if get_media(media_id) is None:
        abort(404)
    update_media(media_id, (request.form.get("alt_text") or "").strip())
    flash("Alt text saved.", "success")
    return redirect(url_for("admin.media"))


@bp.route("/media/<int:media_id>/delete", methods=["POST"])
@admin_required
def media_delete(media_id):
    filename, rowcount = delete_media(media_id)
    if not rowcount:
        abort(404)
    delete_file(filename)
    if _want_json():
        return jsonify({"ok": True})
    flash("Image deleted.", "success")
    return redirect(url_for("admin.media"))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
SETTING_KEYS = [
    "site_title",
    "site_tagline",
    "site_description",
    "site_author",
    "posts_per_page",
    "comments_enabled",
    "footer_text",
]


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    if request.method == "POST":
        for key in SETTING_KEYS:
            if key == "comments_enabled":
                value = "1" if request.form.get(key) == "1" else "0"
            else:
                value = (request.form.get(key) or "").strip()[:500]
            set_setting(key, value)
        get_db().commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))

    return render_template(
        "admin/settings.html", settings=all_settings(), page_title="Settings"
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@bp.route("/users")
@admin_required
def users():
    return render_template(
        "admin/users.html", users=list_users(), page_title="Users"
    )


@bp.route("/users/new", methods=["POST"])
@admin_required
def user_create():
    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""

    error = None
    if not username or len(username) > 60:
        error = "Username is required (max 60 characters)."
    elif username_exists(username):
        error = "That username is taken."
    else:
        error = validate_password(password)

    if error:
        flash(error, "error")
        return redirect(url_for("admin.users"))

    create_user(
        username,
        email,
        hash_password(password),
        is_admin=1 if request.form.get("is_admin", "1") == "1" else 0,
    )
    flash("User created.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/password", methods=["POST"])
@admin_required
def user_password(user_id):
    user = get_user(user_id)
    if user is None:
        abort(404)
    password = request.form.get("password") or ""
    error = validate_password(password)
    if error:
        flash(error, "error")
        return redirect(url_for("admin.users"))
    get_db().execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(password), user_id),
    )
    get_db().commit()
    flash("Password updated for %s." % user["username"], "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def user_delete(user_id):
    if user_id == g.user["id"]:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin.users"))
    user = get_user(user_id)
    if user is None:
        abort(404)
    if user["is_admin"] and admin_user_count() <= 1:
        flash("At least one administrator must remain.", "error")
        return redirect(url_for("admin.users"))
    get_db().execute("DELETE FROM users WHERE id = ?", (user_id,))
    get_db().commit()
    flash("User deleted.", "success")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        row = get_user(g.user["id"])
        if not verify_password(row["password_hash"], current):
            flash("Current password is incorrect.", "error")
        elif new != confirm:
            flash("New passwords do not match.", "error")
        else:
            error = validate_password(new)
            if error:
                flash(error, "error")
            else:
                get_db().execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(new), g.user["id"]),
                )
                get_db().commit()
                flash("Password changed.", "success")
                return redirect(url_for("admin.profile"))
        return redirect(url_for("admin.profile"))

    return render_template("admin/profile.html", page_title="Profile")