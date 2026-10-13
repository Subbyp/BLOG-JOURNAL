"""Comment validation and submission helpers."""

import re
import time

from flask import current_app

from db import get_db
from auth import client_fingerprint

SPAM_KEYWORDS = {
    "viagra", "cialis", "casino", "porn", "xxx", "loan", "payday",
    "seo services", "buy followers", "crypto giveaway", "free money",
}


class CommentError(Exception):
    pass


def _clean(value, length):
    return " ".join((value or "").split())[:length].strip()


def validate_comment(form):
    """Return a normalized dict or raise CommentError with a message."""
    name = _clean(form.get("author_name"), 60)
    email = _clean(form.get("author_email"), 120)
    body = (form.get("body") or "").strip()[:4000]
    parent_id = form.get("parent_id") or None

    if not name:
        raise CommentError("Please enter your name.")
    if len(name) < 2:
        raise CommentError("That name looks too short.")
    if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise CommentError("Please enter a valid email address.")
    if not body:
        raise CommentError("Your comment cannot be empty.")
    if len(body) < 2:
        raise CommentError("That comment looks too short.")
    if len(re.findall(r"https?://\S+", body, flags=re.IGNORECASE)) > 3:
        raise CommentError("Too many links in one comment.")

    lowered = body.lower()
    if any(keyword in lowered for keyword in SPAM_KEYWORDS):
        return {
            "author_name": name,
            "author_email": email,
            "body": body,
            "parent_id": _coerce_int(parent_id),
            "status": "spam",
            "reason": "keyword",
        }

    return {
        "author_name": name,
        "author_email": email,
        "body": body,
        "parent_id": _coerce_int(parent_id),
        "status": "pending",
        "reason": None,
    }


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parent_belongs_to_post(parent_id, post_id):
    if not parent_id:
        return True
    row = get_db().execute(
        "SELECT post_id FROM comments WHERE id = ?", (parent_id,)
    ).fetchone()
    return row is not None and row["post_id"] == post_id


def submit_comment(post, form):
    """Validate and store a comment. Returns the created comment id.

    Comments start in the moderation queue unless spam is detected, in which
    case they are filed directly as spam.
    """
    data = validate_comment(form)

    if not _parent_belongs_to_post(data["parent_id"], post["id"]):
        data["parent_id"] = None

    if data["status"] == "pending" and not current_app.config.get(
        "COMMENTS_NEED_MODERATION", True
    ):
        data["status"] = "approved"

    db = get_db()
    cur = db.execute(
        "INSERT INTO comments (post_id, parent_id, author_name, author_email, body,"
        " status, ip_hash, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            post["id"],
            data["parent_id"],
            data["author_name"],
            data["author_email"],
            data["body"],
            data["status"],
            client_fingerprint(),
            (form.get("user_agent") or "")[:300],
        ),
    )
    db.commit()
    return cur.lastrowid, data["status"]


def set_comment_status(comment_id, status):
    if status not in {"pending", "approved", "spam"}:
        return False
    db = get_db()
    cur = db.execute("UPDATE comments SET status = ? WHERE id = ?", (status, comment_id))
    db.commit()
    return cur.rowcount > 0


def delete_comment(comment_id):
    db = get_db()
    cur = db.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    db.commit()
    return cur.rowcount > 0


def comment_rate_limited(post_id, window_seconds=60, max_comments=3):
    """Block rapid-fire submission from a single client."""
    cutoff = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - window_seconds)
    )
    row = get_db().execute(
        "SELECT count(*) FROM comments WHERE post_id = ? AND ip_hash = ? AND created_at >= ?",
        (post_id, client_fingerprint(), cutoff),
    ).fetchone()
    return row[0] >= max_comments