-- ---------------------------------------------------------------------------
-- Blog schema. Applied by db.init_db() and safe to re-run for fresh installs.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    email         TEXT,
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    slug         TEXT    NOT NULL UNIQUE,
    excerpt      TEXT    NOT NULL DEFAULT '',
    content      TEXT    NOT NULL DEFAULT '',
    cover_image  TEXT,
    status       TEXT    NOT NULL DEFAULT 'draft',      -- draft | published
    is_featured  INTEGER NOT NULL DEFAULT 0,
    allow_comments INTEGER NOT NULL DEFAULT 1,
    view_count   INTEGER NOT NULL DEFAULT 0,
    reading_time INTEGER NOT NULL DEFAULT 1,
    word_count   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT,
    CHECK (status IN ('draft', 'published'))
);

CREATE INDEX IF NOT EXISTS idx_posts_status     ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_published  ON posts(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_created    ON posts(created_at DESC);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS post_tags (
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    PRIMARY KEY (post_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_post_tags_tag ON post_tags(tag_id);

CREATE TABLE IF NOT EXISTS comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id      INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    parent_id    INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    author_name  TEXT    NOT NULL,
    author_email TEXT    NOT NULL,
    body         TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',   -- pending | approved | spam
    ip_hash      TEXT,
    user_agent   TEXT,
    created_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('pending', 'approved', 'spam'))
);

CREATE INDEX IF NOT EXISTS idx_comments_post   ON comments(post_id, status);
CREATE INDEX IF NOT EXISTS idx_comments_status ON comments(status, created_at DESC);

CREATE TABLE IF NOT EXISTS media (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT NOT NULL,
    original_name TEXT NOT NULL,
    alt_text      TEXT NOT NULL DEFAULT '',
    mime_type     TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- Full-text search over post content, kept in sync by triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
    title,
    excerpt,
    content,
    content='posts',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS posts_fts_insert AFTER INSERT ON posts BEGIN
    INSERT INTO posts_fts(rowid, title, excerpt, content)
    VALUES (new.id, new.title, new.excerpt, new.content);
END;

CREATE TRIGGER IF NOT EXISTS posts_fts_delete AFTER DELETE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, title, excerpt, content)
    VALUES ('delete', old.id, old.title, old.excerpt, old.content);
END;

CREATE TRIGGER IF NOT EXISTS posts_fts_update AFTER UPDATE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, title, excerpt, content)
    VALUES ('delete', old.id, old.title, old.excerpt, old.content);
    INSERT INTO posts_fts(rowid, title, excerpt, content)
    VALUES (new.id, new.title, new.excerpt, new.content);
END;

-- Rate limiting for login attempts and comment spam control.
CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_hash    TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(ip_hash, created_at);