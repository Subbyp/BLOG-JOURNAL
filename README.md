# The Journal

A full-stack blog built on Flask and SQLite. Server-rendered, no build step,
no client framework. Posts are written in Markdown, rendered to sanitized HTML
on read, and indexed for full-text search with SQLite FTS5.

## What it does

**Reading**
- Home page with a featured post and a paginated post list
- Post pages with reading time, word count, view counts, and an automatic
  table of contents built from `##`/`###` headings
- Tags with counts, tag-filtered listings, and a month-by-month archive
- Full-text search across titles, excerpts, and bodies, ranked with `bm25`
  so title matches outrank body matches
- Related posts based on shared tags
- Markdown rendering with tables, strikethrough, footnotes, and task lists

**Writing**
- Draft and published states, cover images, custom slugs, tags, excerpts
- Split-pane editor with a live Markdown preview and an insert toolbar
- Image upload with format validation and automatic downscaling
- Signed, expiring preview links for drafts
- Multiple users with admin and author roles

**Comments**
- Threaded replies, per-post and site-wide toggles
- Every comment is held for moderation; obvious spam is filed automatically
- Honeypot field, link-count limit, and per-IP rate limiting
- Comment text is escaped and linkified, never rendered as HTML

**Operations**
- RSS 2.0 feed, `sitemap.xml`, and `robots.txt`
- Open Graph and Twitter cards, plus `BlogPosting` JSON-LD on post pages
- CSRF protection on every mutating request, hashed passwords, session
  cookie hardening, login throttling, and a strict Content-Security-Policy
- Dark mode that respects the system preference, responsive layout, and
  keyboard-accessible controls

## Requirements

- Python 3.8+
- The packages in `requirements.txt`
- For production, a stable `SECRET_KEY`, an HTTPS `SITE_URL`, persistent writable storage, and Gunicorn

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create the database and the first admin user. The command prompts for a
password if you do not supply one:

```bash
export SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
.venv/bin/flask --app app init-db
```

Optionally add sample content:

```bash
.venv/bin/flask --app app seed-demo
```

Run it:

```bash
.venv/bin/flask --app app run
```

The site is at <http://localhost:5000> and the dashboard at `/admin`.

## Production deployment

Do not expose Flask's development server to the internet. Install the pinned
dependencies, configure the environment variables below, initialize the
database and administrator once, then run the app with Gunicorn behind a TLS
terminating reverse proxy:

```bash
export SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
export SITE_URL="https://blog.example.com"
export DATABASE="/var/lib/the-journal/blog.db"
export UPLOAD_FOLDER="/var/lib/the-journal/uploads"
export ADMIN_USER="admin"
export ADMIN_PASSWORD="<set-a-unique-strong-password>"
.venv/bin/python -m flask --app app init-db
.venv/bin/gunicorn --bind 127.0.0.1:8000 --workers 2 --access-logfile - app:app
```

Keep `SECRET_KEY` stable and private across restarts and all workers. Configure
the proxy to serve HTTPS, forward requests to Gunicorn, and sanitize forwarded
headers. Ensure the service user can write to the database and upload paths,
and persist and back up both paths. Use a process manager or container restart
policy to supervise Gunicorn. Create the initial administrator before starting
the service; omit `ADMIN_PASSWORD` only when running initialization interactively
and entering the password at its prompt. Never use the development `run.sh` or
Flask's built-in server as the public production server.

## Configuration

All settings come from the environment.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | random per process | Signs sessions and preview tokens. Set this in production. |
| `SITE_URL` | `http://localhost:5000` | Absolute origin used in the feed, sitemap, and canonical URLs. An `https://` value turns on secure cookies and HSTS. |
| `DATABASE` | `instance/blog.db` | SQLite database path. |
| `UPLOAD_FOLDER` | `instance/uploads` | Where uploaded images are written. |
| `MAX_UPLOAD_MB` | `8` | Upload size limit. |
| `POSTS_PER_PAGE` | `8` | Fallback page size when unset in the admin settings. |
| `LOGIN_MAX_ATTEMPTS` | `8` | Failed logins allowed per IP before throttling. |
| `LOGIN_ATTEMPT_WINDOW` | `900` | Throttle window in seconds. |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` | Used by `init-db` when no users exist. |
| `FLASK_DEBUG` | `false` | Enables the debugger and reloader. |

Site title, tagline, description, page size, and the comment toggle are edited
in the admin under Settings, not through the environment.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

102 tests cover the public pages, post lifecycle, tags, archive, search
(including FTS5 operator injection attempts), authentication and CSRF,
comments and XSS, uploads, Markdown rendering and heading anchors, feeds,
previews, settings, users, pagination, and a pass that asserts no page ships an
inline script or event handler. Each test runs against a throwaway database in
a temp directory.

## Layout

```
app.py                 application factory, error handlers, security headers
config.py              environment-driven configuration
db.py                  connection handling, schema bootstrap, settings, CLI
auth.py                passwords, sessions, CSRF, login throttling
content.py             Markdown rendering, slugs, excerpts, reading time, TOC
queries.py             read paths, all SQL
posts.py               post writes
comments.py            comment validation and submission
uploads.py             image validation, downscaling, storage
feeds.py               RSS, sitemap, robots
blueprints/
  public.py            reading routes
  auth_routes.py       login and logout
  admin.py             dashboard, posts, comments, media, users, settings
templates/             Jinja templates
static/                stylesheet, theme bootstrap, two small scripts, favicon
tests/test_blog.py     test suite
schema.sql             database schema and FTS triggers
```

## Notes on security

Markdown is rendered with HTML escaping on, so an author cannot inject scripts
through post content or a code block. Comment bodies are escaped before URLs are
linkified. Uploads are checked by decoding the image rather than trusting the
filename or extension, and stored under a generated name. Every state-changing
request requires a CSRF token, and passwords are stored as salted hashes.

The Content-Security-Policy is `script-src 'self'` with no `unsafe-inline`, so
every script is a separate file and destructive actions confirm through a
delegated listener rather than an inline `onsubmit`. Adding a feature that needs
inline script means adding a file under `static/js/`, not relaxing the policy.

Set `SECRET_KEY` and an `https://` `SITE_URL` before exposing this to the
internet. The built-in server is fine for development but not for production;
run it under a WSGI server such as gunicorn or waitress.