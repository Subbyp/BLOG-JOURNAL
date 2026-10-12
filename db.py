"""Database connection handling, schema bootstrap, and settings access."""

import os
import sqlite3

import click
from flask import current_app, g

DEFAULT_SETTINGS = {
    "site_title": "The Journal",
    "site_tagline": "Notes on engineering, systems, and the craft of building things.",
    "site_description": (
        "A blog about software engineering, systems design, and building "
        "things for the web."
    ),
    "site_author": "",
    "posts_per_page": "8",
    "comments_enabled": "1",
    "footer_text": "",
}


def get_db():
    """Return the request-scoped SQLite connection, opening it on first use."""
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=10,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if absent. Safe to call on every startup."""
    db = get_db()
    with open(current_app.config["SCHEMA"], "r") as f:
        db.executescript(f.read())
    _sync_fts(db)
    db.commit()


def _sync_fts(db):
    """Rebuild the FTS index if it drifted out of sync with the posts table."""
    fts_count = db.execute("SELECT count(*) FROM posts_fts").fetchone()[0]
    post_count = db.execute("SELECT count(*) FROM posts").fetchone()[0]
    if fts_count != post_count:
        db.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")


def get_setting(key, default=None):
    row = get_db().execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is not None:
        return row["value"]
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(key, "")


def all_settings():
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    stored = {r["key"]: r["value"] for r in rows}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(stored)
    return merged


def set_setting(key, value):
    get_db().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def ensure_upload_dir():
    path = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(path, exist_ok=True)
    return path


@click.command("init-db")
def init_db_command():
    """flask init-db: create tables and the first admin user."""
    init_db()
    db = get_db()
    user_count = db.execute("SELECT count(*) FROM users").fetchone()[0]

    if user_count == 0:
        import getpass

        from werkzeug.security import generate_password_hash

        username = os.environ.get("ADMIN_USER", "admin")
        password = os.environ.get("ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass("Password for %r: " % username)
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                raise click.ClickException("Passwords do not match.")
        db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        db.commit()
        click.echo("Created admin user %r" % username)
    else:
        click.echo("Database ready (%d user(s) already present)." % user_count)


@click.command("seed-demo")
def seed_demo_command():
    """flask seed-demo: insert sample posts so the site is not empty."""
    from posts import create_post

    db = get_db()
    if db.execute("SELECT count(*) FROM posts").fetchone()[0] > 0:
        click.echo("Posts already exist, nothing seeded.")
        return

    user = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    user_id = user["id"] if user else None

    samples = [
        (
            "Designing a Blog That Outlives Its Framework",
            "Web frameworks churn, content is forever. Here is how I structure a "
            "small blog so the writing survives every dependency upgrade.",
            "Framework churn is a fact of life on the web. A new release lands, a "
            "package gets deprecated, and suddenly the tool you chose two years ago "
            "is the thing slowing you down. Writing is not like that. The essay you "
            "publish today should still be readable in a decade.\n\n"
            "## Store content separately from the app\n\n"
            "The single most important decision is keeping posts in a database "
            "rather than in version-controlled template files. It makes publishing "
            "instant, makes search possible, and means a framework upgrade never "
            "touches your writing.\n\n"
            "- Keep the schema simple enough to migrate by hand\n"
            "- Never store rendered HTML; render on read\n"
            "- Treat slugs as stable public URLs\n\n"
            "## Render on read, cache aggressively\n\n"
            "Rendering is cheap. You can afford to re-render on every request and "
            "still serve thousands of hits a second from SQLite.\n\n"
            "> The best infrastructure is the kind nobody has to think about.\n\n"
            "## Keep dependencies boring\n\n\n"
            "Fewer dependencies means fewer upgrade decisions. Every library you add "
            "is a maintenance obligation you accept on behalf of your future self.",
            ["architecture", "web"],
        ),
        (
            "A Practical Guide to Full-Text Search in SQLite",
            "SQLite ships an FTS5 extension that most people never discover. It "
            "handles inverted indexes, ranking, and prefix matching in a few lines.",
            "If you have ever written `LIKE '%term%'` and worried about table scans, "
            "FTS5 is the answer, and it is already compiled into most SQLite builds.\n\n"
            "## The virtual table\n\n"
            "Create an external-content table and mirror your writes with triggers. "
            "The index then stays in sync automatically.\n\n"
            "```sql\nCREATE VIRTUAL TABLE posts_fts USING fts5(\n    title, excerpt, content,\n    content='posts', content_rowid='id'\n);\n```\n\n"
            "## Ranking matters\n\n"
            "Use `bm25()` to weight a title match above a body match. It is the "
            "difference between a useful result set and noise.\n\n"
            "## Watch the tokenizer\n\n"
            "The default `unicode61` tokenizer splits on punctuation and strips "
            "diacritics, which is what you want for prose. Switch tokenizers only "
            "when you have a specific reason.",
            ["search", "sqlite", "databases"],
        ),
        (
            "Shipping Small Features Without Burnout",
            "A feature that ships on Friday is worth more than a perfect one that "
            "ships next quarter. Some habits for keeping throughput steady.",
            "Burnout rarely comes from hard work. It comes from work with no visible "
            "edge, held together by anxiety.\n\n"
            "## Cap the scope out loud\n\n"
            "If a task cannot be described in one sentence, it is two tasks wearing a "
            "trench coat.\n\n"
            "## Make the first version embarrassing\n\n"
            "Ship the thing you would be embarrassed to show, then fix it in public. "
            "Perfect is a very expensive place to start.\n\n"
            "## Close the loop\n\n"
            "Every finished feature needs an ending. If it does not have one, it is "
            "not finished, it is paused.",
            ["process", "career"],
        ),
    ]

    # Spread the demo posts across a few months so the archive has content.
    for index, (title, excerpt, content, tags) in enumerate(samples):
        published = "2024-%02d-%02d 09:00:00" % (index + 1, index + 3)
        create_post(
            title=title,
            content=content,
            excerpt=excerpt,
            status="published",
            is_featured=1 if index == 0 else 0,
            tags=tags,
            published_at=published,
        )
        with db:
            db.execute(
                "UPDATE posts SET created_at = ?, updated_at = ?"
                " WHERE slug = ?",
                (published, published, title.lower().replace(" ", "-")),
            )
    click.echo("Seeded %d demo posts." % len(samples))


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_demo_command)