"""Post write operations."""

from content import excerpt_from, reading_time, slugify, word_count
from db import get_db
from queries import get_tags_for_post, set_post_tags


def _now():
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def slug_exists(slug, exclude_id=None):
    db = get_db()
    if exclude_id:
        row = db.execute(
            "SELECT 1 FROM posts WHERE slug = ? AND id != ?", (slug, exclude_id)
        ).fetchone()
    else:
        row = db.execute("SELECT 1 FROM posts WHERE slug = ?", (slug,)).fetchone()
    return row is not None


def unique_slug(desired, exclude_id=None):
    """Return a slug based on `desired` that is not already taken."""
    base = slugify(desired) or "post"
    if not slug_exists(base, exclude_id):
        return base
    n = 2
    while True:
        candidate = "%s-%d" % (base[:76], n)
        if not slug_exists(candidate, exclude_id):
            return candidate
        n += 1


def create_post(
    title,
    content,
    slug=None,
    excerpt="",
    status="draft",
    is_featured=0,
    allow_comments=1,
    cover_image=None,
    tags=None,
    published_at=None,
):
    db = get_db()
    final_slug = unique_slug(slug or title)
    excerpt = (excerpt or "").strip() or excerpt_from(content)
    now = _now()
    published = published_at or (now if status == "published" else None)

    cur = db.execute(
        "INSERT INTO posts (title, slug, excerpt, content, cover_image, status,"
        " is_featured, allow_comments, reading_time, word_count, created_at,"
        " updated_at, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            title.strip(),
            final_slug,
            excerpt,
            content,
            cover_image,
            status,
            1 if is_featured else 0,
            1 if allow_comments else 0,
            reading_time(content),
            word_count(content),
            now,
            now,
            published,
        ),
    )
    db.commit()
    if tags:
        set_post_tags(cur.lastrowid, tags)
    return cur.lastrowid


def update_post(
    post_id,
    title,
    content,
    slug=None,
    excerpt="",
    status="draft",
    is_featured=0,
    allow_comments=1,
    cover_image=None,
    tags=None,
    published_at=None,
):
    db = get_db()
    current = db.execute(
        "SELECT status, published_at FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    if current is None:
        return False

    final_slug = slug if slug and slug == slugify(slug) else unique_slug(
        slug or title, exclude_id=post_id
    )
    if slug_exists(final_slug, exclude_id=post_id):
        final_slug = unique_slug(final_slug, exclude_id=post_id)

    excerpt = (excerpt or "").strip() or excerpt_from(content)

    published = published_at
    if status == "published" and not published:
        published = current["published_at"] or _now()
    if status == "draft":
        published = current["published_at"]

    db.execute(
        "UPDATE posts SET title = ?, slug = ?, excerpt = ?, content = ?,"
        " cover_image = ?, status = ?, is_featured = ?, allow_comments = ?,"
        " reading_time = ?, word_count = ?, updated_at = ?, published_at = ?"
        " WHERE id = ?",
        (
            title.strip(),
            final_slug,
            excerpt,
            content,
            cover_image,
            status,
            1 if is_featured else 0,
            1 if allow_comments else 0,
            reading_time(content),
            word_count(content),
            _now(),
            published,
            post_id,
        ),
    )
    db.commit()
    set_post_tags(post_id, tags or [])
    return True


def delete_post(post_id):
    db = get_db()
    cur = db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    db.commit()
    return cur.rowcount > 0


def set_status(post_id, status):
    db = get_db()
    if status not in {"draft", "published"}:
        return False
    row = db.execute("SELECT published_at FROM posts WHERE id = ?", (post_id,)).fetchone()
    if row is None:
        return False
    published_at = row["published_at"]
    if status == "published" and not published_at:
        published_at = _now()
    db.execute(
        "UPDATE posts SET status = ?, published_at = ?, updated_at = ? WHERE id = ?",
        (status, published_at, _now(), post_id),
    )
    db.commit()
    return True


def toggle_featured(post_id):
    db = get_db()
    db.execute("UPDATE posts SET is_featured = 1 - is_featured WHERE id = ?", (post_id,))
    db.commit()


def get_full_post(post_id):
    """Post row plus tags, for admin editing."""
    row = get_db().execute(
        "SELECT * FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["tags"] = get_tags_for_post(post_id)
    return data