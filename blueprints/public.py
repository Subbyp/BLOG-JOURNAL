"""Public-facing routes: home, posts, tags, archive, search, comments, media."""

from datetime import datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from auth import login_required
from comments import CommentError, comment_rate_limited, submit_comment
from content import render_markdown, table_of_contents
from db import get_setting
from queries import (
    archive_counts,
    get_approved_comments,
    get_post_by_id,
    get_post_by_slug,
    get_tag_by_slug,
    increment_view_count,
    list_all_tags_with_counts,
    list_posts_for_period,
    list_published_posts,
    recent_comment_count,
    related_posts,
    search_posts,
    search_snippet,
)

bp = Blueprint("blog", __name__)

MONTHS = {
    "01": "January", "02": "February", "03": "March", "04": "April",
    "05": "May", "06": "June", "07": "July", "08": "August",
    "09": "September", "10": "October", "11": "November", "12": "December",
}


def posts_per_page():
    try:
        return int(get_setting("posts_per_page") or current_app.config["POSTS_PER_PAGE"])
    except (TypeError, ValueError):
        return current_app.config["POSTS_PER_PAGE"]


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------
@bp.route("/")
def index():
    per_page = posts_per_page()
    posts, pagination = list_published_posts(request.args, per_page=per_page)

    featured = None
    if pagination["page"] == 1 and posts:
        featured = next((p for p in posts if p["is_featured"]), None)
        if featured is not None:
            posts = [p for p in posts if p["id"] != featured["id"]]

    return render_template(
        "index.html",
        posts=posts,
        featured=featured,
        pagination=pagination,
        page_title=None,
        active="home",
    )


# ---------------------------------------------------------------------------
# Post detail
# ---------------------------------------------------------------------------
@bp.route("/post/<slug>")
def post_detail(slug):
    post = get_post_by_slug(slug)
    if post is None:
        abort(404)

    increment_view_count(post["id"])
    post["view_count"] = (post["view_count"] or 0) + 1

    body = get_post_body(post["id"])
    return render_template(
        "post.html",
        post=post,
        content_html=render_markdown(body),
        toc=table_of_contents(body),
        comments=get_approved_comments(post["id"]),
        related=related_posts(post),
        allow_comments=bool(post["allow_comments"]) and _comments_globally_enabled(),
        page_title=post["title"],
        page_description=post["excerpt"],
        active="home",
    )


def get_post_body(post_id):
    from db import get_db

    row = get_db().execute("SELECT content FROM posts WHERE id = ?", (post_id,)).fetchone()
    return row["content"] if row else ""


def _comments_globally_enabled():
    return get_setting("comments_enabled") == "1"


@bp.route("/post/<slug>/comment", methods=["POST"])
def add_comment(slug):
    post = get_post_by_slug(slug)
    if post is None:
        abort(404)
    if not post["allow_comments"] or not _comments_globally_enabled():
        abort(403)

    # Honeypot: a field no human should fill in.
    if (request.form.get("website") or "").strip():
        flash("Thanks for your comment.", "success")
        return redirect(url_for("blog.post_detail", slug=slug) + "#comments")

    if comment_rate_limited(post["id"]):
        flash("You are commenting too quickly. Please wait a moment.", "error")
        return redirect(url_for("blog.post_detail", slug=slug) + "#comments")

    payload = dict(request.form)
    payload["user_agent"] = request.headers.get("User-Agent", "")
    try:
        _, status = submit_comment(post, payload)
    except CommentError as exc:
        flash(str(exc), "error")
        return redirect(url_for("blog.post_detail", slug=slug) + "#comments")

    if status == "approved":
        flash("Your comment is live.", "success")
    else:
        flash("Thanks! Your comment is awaiting moderation.", "success")
    return redirect(url_for("blog.post_detail", slug=slug) + "#comments")


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------
@bp.route("/tags")
def tag_index():
    return render_template(
        "tags.html",
        tags=list_all_tags_with_counts(),
        page_title="Tags",
        active="tags",
    )


@bp.route("/tag/<slug>")
def tag_detail(slug):
    tag = get_tag_by_slug(slug)
    if tag is None:
        abort(404)
    posts, pagination = list_published_posts(
        request.args, tag_slug=slug, per_page=posts_per_page()
    )
    return render_template(
        "tag.html",
        tag=tag,
        posts=posts,
        pagination=pagination,
        page_title="Tag: %s" % tag["name"],
        active="tags",
    )


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------
@bp.route("/archive")
def archive_index():
    return render_template(
        "archive.html",
        buckets=archive_counts(),
        months=MONTHS,
        page_title="Archive",
        active="archive",
    )


@bp.route("/archive/<year>")
@bp.route("/archive/<year>/<month>")
def archive_period(year, month=None):
    if not (year.isdigit() and len(year) == 4):
        abort(404)
    if month and (not month.isdigit() or month not in MONTHS):
        abort(404)
    posts = list_posts_for_period(year, month)
    label = "%s %s" % (MONTHS.get(month, ""), year) if month else year
    return render_template(
        "archive_period.html",
        posts=posts,
        label=label.strip(),
        page_title="Archive: %s" % label.strip(),
        active="archive",
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
@bp.route("/search")
def search():
    term = (request.args.get("q") or "").strip()[:120]
    posts, pagination = ([], None)
    snippets = {}
    if term:
        posts, pagination = search_posts(term, request.args, per_page=posts_per_page())
        snippets = {p["id"]: search_snippet(p["id"], term) for p in posts}
    return render_template(
        "search.html",
        term=term,
        posts=posts,
        pagination=pagination,
        snippets=snippets,
        page_title="Search",
        active="search",
    )


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------
@bp.route("/about")
def about():
    return render_template(
        "about.html",
        post_count=None,
        comment_count=recent_comment_count(3650),
        page_title="About",
        active="about",
    )


# ---------------------------------------------------------------------------
# Draft preview (signed, short-lived links)
# ---------------------------------------------------------------------------
def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="post-preview")


def preview_token(post_id):
    return _serializer().dumps({"post_id": post_id})


@bp.route("/preview/<token>")
@login_required
def preview(token):
    """Render a draft. Requires a signed-in user; the token is not a login."""
    try:
        data = _serializer().loads(token, max_age=60 * 60 * 24 * 3)
    except SignatureExpired:
        abort(410, description="This preview link has expired.")
    except BadSignature:
        abort(404)
    post = get_post_by_id(data["post_id"])
    if post is None:
        abort(404)
    body = get_post_body(post["id"])
    return render_template(
        "post.html",
        post=post,
        content_html=render_markdown(body),
        toc=table_of_contents(body),
        comments=[],
        related=[],
        allow_comments=False,
        is_preview=True,
        page_title="Preview: %s" % post["title"],
        active=None,
    )


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------
@bp.route("/media/<path:filename>")
def media_file(filename):
    response = send_from_directory(
        current_app.config["UPLOAD_FOLDER"], filename, max_age=60 * 60 * 24 * 30
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@bp.route("/healthz")
def healthz():
    from db import get_db

    get_db().execute("SELECT 1").fetchone()
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}