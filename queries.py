"""Data access helpers. All SQL lives here; views stay declarative."""

import math
import re

from content import excerpt_from, reading_time
from db import get_db


# ---------------------------------------------------------------------------
# Post selection
# ---------------------------------------------------------------------------
_POST_COLUMNS = """
    p.id, p.title, p.slug, p.excerpt, p.cover_image, p.status, p.is_featured,
    p.allow_comments, p.view_count, p.reading_time, p.word_count,
    p.created_at, p.updated_at, p.published_at
"""


def _paginate(args, default_per_page):
    try:
        page = max(1, int(args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(args.get("per_page", default_per_page))
    except (TypeError, ValueError):
        per_page = default_per_page
    per_page = max(1, min(per_page, 100))
    return page, per_page


def _total_pages(total, per_page):
    return max(1, math.ceil(total / float(per_page)))


def _page_window(page, per_page, total):
    total_pages = _total_pages(total, per_page)
    page = min(page, total_pages)
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_num": page - 1 if page > 1 else None,
        "next_num": page + 1 if page < total_pages else None,
        "first_num": 1,
        "last_num": total_pages,
    }


def _count(db, sql, params):
    return db.execute(sql, params).fetchone()[0]


def list_published_posts(args, tag_slug=None, per_page=8):
    """Published posts, newest first, paginated."""
    page, per_page = _paginate(args, per_page)
    db = get_db()

    where = "WHERE p.status = 'published'"
    params = []
    if tag_slug:
        where += (
            " AND EXISTS (SELECT 1 FROM post_tags pt JOIN tags t ON t.id = pt.tag_id"
            " WHERE pt.post_id = p.id AND t.slug = ?)"
        )
        params.append(tag_slug)

    total = _count(db, "SELECT count(*) FROM posts p " + where, params)
    page = min(page, _total_pages(total, per_page))

    rows = db.execute(
        "SELECT %s FROM posts p %s ORDER BY p.published_at DESC, p.id DESC LIMIT ? OFFSET ?"
        % (_POST_COLUMNS, where),
        params + [per_page, (page - 1) * per_page],
    ).fetchall()

    posts = [_hydrate(row) for row in rows]
    return posts, _page_window(page, per_page, total)


def get_post_by_slug(slug, include_unpublished=False):
    db = get_db()
    if include_unpublished:
        row = db.execute(
            "SELECT %s FROM posts p WHERE p.slug = ?" % _POST_COLUMNS, (slug,)
        ).fetchone()
    else:
        row = db.execute(
            "SELECT %s FROM posts p WHERE p.slug = ? AND p.status = 'published'"
            % _POST_COLUMNS,
            (slug,),
        ).fetchone()
    return _hydrate(row) if row else None


def get_post_by_id(post_id):
    row = get_db().execute(
        "SELECT %s FROM posts p WHERE p.id = ?" % _POST_COLUMNS, (post_id,)
    ).fetchone()
    return _hydrate(row) if row else None


def get_post_by_id_with_content(post_id):
    row = get_db().execute(
        "SELECT *, content FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    return row


def _hydrate(row):
    """Attach computed fields and tags to a post row."""
    if row is None:
        return None
    data = dict(row)
    data.setdefault("excerpt", "")
    data["tags"] = get_tags_for_post(data["id"])
    return data


def increment_view_count(post_id):
    db = get_db()
    db.execute("UPDATE posts SET view_count = view_count + 1 WHERE id = ?", (post_id,))
    db.commit()


# ---------------------------------------------------------------------------
# Admin listing
# ---------------------------------------------------------------------------
def list_all_posts(args, status=None, per_page=20):
    page, per_page = _paginate(args, per_page)
    db = get_db()

    where, params = "", []
    if status in {"draft", "published"}:
        where = "WHERE status = ?"
        params.append(status)

    total = _count(db, "SELECT count(*) FROM posts " + where, params)
    page = min(page, _total_pages(total, per_page))
    rows = db.execute(
        "SELECT id, title, slug, status, is_featured, allow_comments, view_count,"
        " created_at, updated_at, published_at FROM posts %s"
        " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?" % where,
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return [dict(r) for r in rows], _page_window(page, per_page, total)


def related_posts(post, limit=3):
    """Posts sharing a tag, excluding the post itself."""
    rows = get_db().execute(
        "SELECT DISTINCT %s FROM posts p"
        " JOIN post_tags pt ON pt.post_id = p.id"
        " WHERE p.status = 'published' AND p.id != ?"
        "   AND pt.tag_id IN (SELECT tag_id FROM post_tags WHERE post_id = ?)"
        " ORDER BY p.published_at DESC LIMIT ?" % _POST_COLUMNS,
        (post["id"], post["id"], limit),
    ).fetchall()
    return [_hydrate(r) for r in rows]


def archive_counts():
    """Year and month buckets for published posts, newest first."""
    rows = get_db().execute(
        "SELECT substr(published_at, 1, 4) AS year,"
        "       substr(published_at, 1, 7) AS month,"
        "       count(*) AS total"
        " FROM posts WHERE status = 'published' AND published_at IS NOT NULL"
        " GROUP BY year, month ORDER BY year DESC, month DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def list_posts_for_period(year, month=None):
    where = "WHERE status = 'published' AND substr(published_at, 1, 4) = ?"
    params = [year]
    if month:
        where += " AND substr(published_at, 1, 7) = ?"
        params.append("%s-%s" % (year, month))
    rows = get_db().execute(
        "SELECT %s FROM posts p %s ORDER BY p.published_at DESC" % (_POST_COLUMNS, where),
        params,
    ).fetchall()
    return [_hydrate(r) for r in rows]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def _fts_query(term):
    """Turn user input into a safe FTS5 MATCH expression.

    Every token is quoted so operators (NEAR, *, -, :, ^) are treated as
    literal text rather than query syntax.
    """
    tokens = [t for t in _tokenize(term) if t]
    if not tokens:
        return None
    return " AND ".join('"%s"' % t.replace('"', '""') for t in tokens)


def _tokenize(term):
    return re.split(r"\s+", (term or "").strip())


def search_posts(term, args, per_page=8):
    page, per_page = _paginate(args, per_page)
    db = get_db()
    match = _fts_query(term)
    if not match:
        return [], _page_window(page, per_page, 0)

    # bm25 weights: title matches count for more than body matches.
    select = (
        "SELECT %s, bm25(posts_fts, 10.0, 4.0, 1.0) AS rank"
        " FROM posts_fts JOIN posts p ON p.id = posts_fts.rowid"
        " WHERE posts_fts MATCH ? AND p.status = 'published'"
        " ORDER BY rank, p.published_at DESC" % _POST_COLUMNS
    )
    count_sql = (
        "SELECT count(*) FROM posts_fts JOIN posts p ON p.id = posts_fts.rowid"
        " WHERE posts_fts MATCH ? AND p.status = 'published'"
    )

    try:
        total = _count(db, count_sql, (match,))
        page = min(page, _total_pages(total, per_page))
        rows = db.execute(
            select + " LIMIT ? OFFSET ?", (match, per_page, (page - 1) * per_page)
        ).fetchall()
    except Exception:
        # A malformed MATCH expression should not produce a 500.
        db.rollback()
        return [], _page_window(page, per_page, 0)

    return [_hydrate(r) for r in rows], _page_window(page, per_page, total)


def search_snippet(post_id, term):
    """A short highlighted context string around the first match."""
    from markupsafe import escape

    tokens = [t for t in _tokenize(term) if t]
    if not tokens:
        return ""
    row = get_db().execute(
        "SELECT content FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    if row is None:
        return ""
    body = (row["content"] or "").replace("\n", " ")
    lowered = body.lower()
    for token in tokens:
        index = lowered.find(token.lower())
        if index != -1:
            start = max(0, index - 60)
            end = min(len(body), index + len(token) + 90)
            chunk = body[start:end].strip()
            if start > 0:
                chunk = "…" + chunk
            if end < len(body):
                chunk = chunk + "…"
            return str(escape(chunk))
    return str(escape(excerpt_from(body, 160)))


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------
def get_tags_for_post(post_id):
    rows = get_db().execute(
        "SELECT t.id, t.name, t.slug FROM tags t"
        " JOIN post_tags pt ON pt.tag_id = t.id"
        " WHERE pt.post_id = ? ORDER BY t.name", (post_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_all_tags_with_counts(published_only=True):
    condition = (
        "AND p.status = 'published'" if published_only else ""
    )
    rows = get_db().execute(
        "SELECT t.id, t.name, t.slug, count(pt.post_id) AS post_count"
        " FROM tags t JOIN post_tags pt ON pt.tag_id = t.id"
        " JOIN posts p ON p.id = pt.post_id %s"
        " GROUP BY t.id, t.name, t.slug"
        " HAVING post_count > 0"
        " ORDER BY post_count DESC, t.name ASC" % condition
    ).fetchall()
    return [dict(r) for r in rows]


def get_tag_by_slug(slug):
    row = get_db().execute(
        "SELECT id, name, slug FROM tags WHERE slug = ?", (slug,)
    ).fetchone()
    return dict(row) if row else None


def set_post_tags(post_id, tag_names):
    """Replace a post's tags. Returns the resulting tag list."""
    from content import slugify

    db = get_db()
    db.execute("DELETE FROM post_tags WHERE post_id = ?", (post_id,))
    if isinstance(tag_names, str):
        tag_names = parse_tag_input(tag_names)

    seen = set()
    for raw in tag_names:
        name = " ".join(str(raw).split())[:40].strip()
        if not name:
            continue
        slug = slugify(name)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        db.execute("INSERT OR IGNORE INTO tags (name, slug) VALUES (?, ?)", (name, slug))
        row = db.execute("SELECT id FROM tags WHERE slug = ?", (slug,)).fetchone()
        if row:
            db.execute(
                "INSERT OR IGNORE INTO post_tags (post_id, tag_id) VALUES (?, ?)",
                (post_id, row["id"]),
            )
    db.commit()
    return get_tags_for_post(post_id)


def parse_tag_input(value):
    """Accept comma-separated tags from a single form field."""
    import re

    return [t for t in re.split(r"[,\n]+", value or "") if t.strip()]


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------
def get_approved_comments(post_id):
    rows = get_db().execute(
        "SELECT id, post_id, parent_id, author_name, body, created_at"
        " FROM comments WHERE post_id = ? AND status = 'approved'"
        " ORDER BY created_at ASC", (post_id,)
    ).fetchall()
    return _nest_comments([dict(r) for r in rows])


def get_comment_counts(post_id):
    row = get_db().execute(
        "SELECT status, count(*) AS total FROM comments WHERE post_id = ? GROUP BY status",
        (post_id,),
    ).fetchall()
    return {r["status"]: r["total"] for r in row}


def _nest_comments(flat):
    """Build a two-level reply tree from a flat, time-ordered list."""
    by_id = {c["id"]: dict(c, children=[]) for c in flat}
    roots = []
    for comment in by_id.values():
        parent = by_id.get(comment["parent_id"]) if comment["parent_id"] else None
        if parent is not None:
            parent["children"].append(comment)
        else:
            roots.append(comment)
    return roots


def list_comments(args, status=None, post_id=None, per_page=25):
    page, per_page = _paginate(args, per_page)
    db = get_db()

    clauses, params = [], []
    if status:
        clauses.append("c.status = ?")
        params.append(status)
    if post_id:
        clauses.append("c.post_id = ?")
        params.append(post_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = _count(db, "SELECT count(*) FROM comments c " + where, params)
    page = min(page, _total_pages(total, per_page))
    rows = db.execute(
        "SELECT c.id, c.post_id, c.parent_id, c.author_name, c.author_email,"
        " c.body, c.status, c.created_at, p.title AS post_title, p.slug AS post_slug"
        " FROM comments c JOIN posts p ON p.id = c.post_id " + where +
        " ORDER BY c.created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return [dict(r) for r in rows], _page_window(page, per_page, total)


def get_comment(comment_id):
    row = get_db().execute(
        "SELECT * FROM comments WHERE id = ?", (comment_id,)
    ).fetchone()
    return dict(row) if row else None


def get_recent_comments(limit=5):
    rows = get_db().execute(
        "SELECT c.id, c.author_name, c.body, c.status, c.created_at,"
        " p.title AS post_title FROM comments c JOIN posts p ON p.id = c.post_id"
        " ORDER BY c.created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def recent_comment_count(days=7):
    row = get_db().execute(
        "SELECT count(*) FROM comments WHERE created_at >= datetime('now', ?)",
        ("-%d days" % days,),
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------
def create_media(filename, original_name, mime_type, size_bytes, alt_text="", user_id=None):
    db = get_db()
    cur = db.execute(
        "INSERT INTO media (filename, original_name, alt_text, mime_type, size_bytes,"
        " uploaded_by) VALUES (?, ?, ?, ?, ?, ?)",
        (filename, original_name, alt_text, mime_type, size_bytes, user_id),
    )
    db.commit()
    return cur.lastrowid


def list_media(args, per_page=24):
    page, per_page = _paginate(args, per_page)
    db = get_db()
    total = _count(db, "SELECT count(*) FROM media", ())
    page = min(page, _total_pages(total, per_page))
    rows = db.execute(
        "SELECT id, filename, original_name, alt_text, mime_type, size_bytes, created_at"
        " FROM media ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page),
    ).fetchall()
    return [dict(r) for r in rows], _page_window(page, per_page, total)


def get_media(media_id):
    row = get_db().execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
    return dict(row) if row else None


def update_media(media_id, alt_text):
    db = get_db()
    db.execute("UPDATE media SET alt_text = ? WHERE id = ?", (alt_text[:200], media_id))
    db.commit()


def delete_media(media_id):
    """Delete a media row. Returns (filename, rowcount) for file cleanup."""
    db = get_db()
    row = db.execute("SELECT filename FROM media WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        return None, 0
    db.execute("DELETE FROM media WHERE id = ?", (media_id,))
    db.commit()
    return row["filename"], 1


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def list_users():
    rows = get_db().execute(
        "SELECT id, username, email, is_admin, created_at FROM users ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_user_by_username(username):
    row = get_db().execute(
        "SELECT id, username, email, password_hash, is_admin FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    return dict(row) if row else None


def get_user(user_id):
    row = get_db().execute(
        "SELECT id, username, email, password_hash, is_admin FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def create_user(username, email, password_hash, is_admin=1):
    db = get_db()
    cur = db.execute(
        "INSERT INTO users (username, email, password_hash, is_admin) VALUES (?, ?, ?, ?)",
        (username, email or None, password_hash, 1 if is_admin else 0),
    )
    db.commit()
    return cur.lastrowid


def username_exists(username, exclude_id=None):
    if exclude_id:
        row = get_db().execute(
            "SELECT 1 FROM users WHERE username = ? AND id != ?", (username, exclude_id)
        ).fetchone()
    else:
        row = get_db().execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
    return row is not None


def admin_user_count():
    return get_db().execute("SELECT count(*) FROM users WHERE is_admin = 1").fetchone()[0]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def dashboard_stats():
    db = get_db()
    row = db.execute(
        "SELECT"
        "  (SELECT count(*) FROM posts) AS total_posts,"
        "  (SELECT count(*) FROM posts WHERE status = 'published') AS published_posts,"
        "  (SELECT count(*) FROM posts WHERE status = 'draft') AS draft_posts,"
        "  (SELECT count(*) FROM comments) AS total_comments,"
        "  (SELECT count(*) FROM comments WHERE status = 'pending') AS pending_comments,"
        "  (SELECT count(*) FROM comments WHERE status = 'spam') AS spam_comments,"
        "  (SELECT count(*) FROM tags) AS total_tags,"
        "  (SELECT count(*) FROM media) AS total_media,"
        "  (SELECT coalesce(sum(view_count), 0) FROM posts) AS total_views"
    ).fetchone()
    return dict(row)


def top_posts(limit=5):
    rows = get_db().execute(
        "SELECT id, title, slug, view_count, status FROM posts"
        " ORDER BY view_count DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def estimate_reading_time_total():
    row = get_db().execute(
        "SELECT sum(length(content)) AS total_chars FROM posts WHERE status = 'published'"
    ).fetchone()
    chars = row["total_chars"] or 0
    return max(1, round(chars / 1500.0))


def reading_time_of(markdown_text):
    return reading_time(markdown_text)