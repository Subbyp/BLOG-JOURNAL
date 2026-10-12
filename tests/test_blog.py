"""Test suite for the blog. Run with: python -m unittest discover tests"""

import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from auth import hash_password  # noqa: E402
from db import get_db  # noqa: E402

CSRF_RE = re.compile(rb'name="csrf_token" value="([a-f0-9]+)"')


class BaseTestCase(unittest.TestCase):
    """Each test gets a fresh database and upload directory."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="blogtest-")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.upload_dir = os.path.join(self.tmpdir, "uploads")

        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.db_path,
                "UPLOAD_FOLDER": self.upload_dir,
                "SECRET_KEY": "test-secret-key",
                "WTF_CSRF_ENABLED": False,
                "COMMENTS_NEED_MODERATION": True,
            }
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO users (username, email, password_hash, is_admin)"
                " VALUES ('admin', 'admin@test.local', ?, 1)",
                (hash_password("admin123"),),
            )
            db.execute(
                "INSERT INTO users (username, email, password_hash, is_admin)"
                " VALUES ('author', 'author@test.local', ?, 0)",
                (hash_password("author123"),),
            )
            db.commit()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # --- helpers ---------------------------------------------------------
    def csrf(self):
        """Fetch a CSRF token from a page that renders a form."""
        for path in ("/login", "/admin/profile", "/admin/posts/new"):
            response = self.client.get(path)
            match = CSRF_RE.search(response.data)
            if match:
                return match.group(1).decode()
        self.fail("no CSRF token found in any rendered form")

    def login(self, username="admin", password="admin123"):
        return self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf(),
                "username": username,
                "password": password,
            },
            follow_redirects=True,
        )

    def create_post(
        self,
        title="Test Post",
        content="Some content here.",
        tags="",
        publish=True,
        extra=None,
    ):
        data = {
            "csrf_token": self.csrf(),
            "title": title,
            "content": content,
            "tags": tags,
            "publish": "1" if publish else "0",
            "allow_comments": "1",
        }
        if extra:
            data.update(extra)
        response = self.client.post(
            "/admin/posts/new", data=data, follow_redirects=True
        )
        return response

    def post_id_by_slug(self, slug):
        with self.app.app_context():
            row = get_db().execute(
                "SELECT id FROM posts WHERE slug = ?", (slug,)
            ).fetchone()
        return row["id"] if row else None

    def comments_on(self, post_id):
        with self.app.app_context():
            return get_db().execute(
                "SELECT * FROM comments WHERE post_id = ?", (post_id,)
            ).fetchall()


# ===========================================================================
class PublicPagesTest(BaseTestCase):
    def test_home_page_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No posts yet", response.data)

    def test_empty_state_offers_new_post_to_admin(self):
        self.login()
        response = self.client.get("/")
        self.assertIn(b"Write a post", response.data)

    def test_static_pages_render(self):
        for path in ("/tags", "/archive", "/about", "/search"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_404_page(self):
        response = self.client.get("/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertIn(b"404", response.data)

    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_security_headers_present(self):
        response = self.client.get("/")
        self.assertIn("X-Content-Type-Options", response.headers)
        self.assertIn("X-Frame-Options", response.headers)
        self.assertIn("Content-Security-Policy", response.headers)
        self.assertIn("nosniff", response.headers["X-Content-Type-Options"])


# ===========================================================================
class PostLifecycleTest(BaseTestCase):
    def test_create_published_post_and_view_it(self):
        self.login()
        self.create_post(title="Hello World", content="# Hi\n\nBody text.")

        home = self.client.get("/")
        self.assertIn(b"Hello World", home.data)

        post = self.client.get("/post/hello-world")
        self.assertEqual(post.status_code, 200)
        self.assertIn(b"<h1>Hi</h1>", post.data)

    def test_draft_is_hidden_from_public(self):
        self.login()
        self.create_post(title="Secret Draft", publish=False)

        self.assertEqual(self.client.get("/post/secret-draft").status_code, 404)
        home = self.client.get("/")
        self.assertNotIn(b"Secret Draft", home.data)

    def test_draft_visible_in_admin(self):
        self.login()
        self.create_post(title="Secret Draft", publish=False)
        response = self.client.get("/admin/posts")
        self.assertIn(b"Secret Draft", response.data)

    def test_slug_collisions_are_resolved(self):
        self.login()
        self.create_post(title="Duplicate Title")
        self.create_post(title="Duplicate Title")
        with self.app.app_context():
            slugs = [
                r["slug"]
                for r in get_db().execute("SELECT slug FROM posts ORDER BY id")
            ]
        self.assertEqual(slugs, ["duplicate-title", "duplicate-title-2"])

    def test_edit_updates_content(self):
        self.login()
        self.create_post(title="Editable", content="original")
        post_id = self.post_id_by_slug("editable")

        response = self.client.post(
            "/admin/posts/%d/edit" % post_id,
            data={
                "csrf_token": self.csrf(),
                "title": "Editable",
                "content": "brand new content",
                "publish": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        page = self.client.get("/post/editable")
        self.assertIn(b"brand new content", page.data)
        self.assertNotIn(b"original", page.data)

    def test_publish_toggle(self):
        self.login()
        self.create_post(title="Toggle Me", publish=False)
        post_id = self.post_id_by_slug("toggle-me")

        self.client.post(
            "/admin/posts/%d/status" % post_id,
            data={"csrf_token": self.csrf(), "status": "published"},
            follow_redirects=True,
        )
        self.assertEqual(self.client.get("/post/toggle-me").status_code, 200)

        self.client.post(
            "/admin/posts/%d/status" % post_id,
            data={"csrf_token": self.csrf(), "status": "draft"},
            follow_redirects=True,
        )
        self.assertEqual(self.client.get("/post/toggle-me").status_code, 404)

    def test_delete_post(self):
        self.login()
        self.create_post(title="Delete Me")
        post_id = self.post_id_by_slug("delete-me")

        self.client.post(
            "/admin/posts/%d/delete" % post_id,
            data={"csrf_token": self.csrf()},
            follow_redirects=True,
        )
        self.assertEqual(self.client.get("/post/delete-me").status_code, 404)

    def test_title_required(self):
        self.login()
        response = self.client.post(
            "/admin/posts/new",
            data={"csrf_token": self.csrf(), "title": "", "content": "x"},
        )
        self.assertEqual(response.status_code, 400)

    def test_view_count_increments(self):
        self.login()
        self.create_post(title="Counted Post")
        self.client.get("/post/counted-post")
        self.client.get("/post/counted-post")
        with self.app.app_context():
            row = get_db().execute(
                "SELECT view_count FROM posts WHERE slug = 'counted-post'"
            ).fetchone()
        self.assertGreaterEqual(row["view_count"], 2)


# ===========================================================================
class TagsAndArchiveTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.create_post(title="Tagged Post", tags="python, web dev")
        self.create_post(title="Other Post", tags="python")

    def test_tags_page_lists_tags(self):
        response = self.client.get("/tags")
        self.assertIn(b"python", response.data)
        self.assertIn(b"web-dev", response.data)

    def test_tag_detail_filters_posts(self):
        response = self.client.get("/tag/python")
        self.assertIn(b"Tagged Post", response.data)
        self.assertIn(b"Other Post", response.data)

        response = self.client.get("/tag/web-dev")
        self.assertIn(b"Tagged Post", response.data)
        self.assertNotIn(b"Other Post", response.data)

    def test_unknown_tag_is_404(self):
        self.assertEqual(self.client.get("/tag/nope").status_code, 404)

    def test_archive_buckets(self):
        response = self.client.get("/archive")
        self.assertEqual(response.status_code, 200)

    def test_archive_period(self):
        with self.app.app_context():
            month = get_db().execute(
                "SELECT substr(published_at,1,7) AS m FROM posts LIMIT 1"
            ).fetchone()["m"]
        year, mon = month[:4], month[5:7]
        response = self.client.get("/archive/%s/%s" % (year, mon))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Tagged Post", response.data)

    def test_bad_archive_year_is_404(self):
        self.assertEqual(self.client.get("/archive/notayear").status_code, 404)
        self.assertEqual(self.client.get("/archive/2024/13").status_code, 404)


# ===========================================================================
class SearchTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.create_post(
            title="SQLite Full Text Search",
            content="FTS5 provides an inverted index for fast lookup.",
        )
        self.create_post(title="Unrelated Topic", content="Gardening in the rain.")

    def test_search_finds_by_title(self):
        response = self.client.get("/search?q=sqlite")
        self.assertIn(b"SQLite Full Text Search", response.data)

    def test_search_finds_by_content(self):
        response = self.client.get("/search?q=inverted")
        self.assertIn(b"SQLite Full Text Search", response.data)

    def test_search_multiple_terms_is_anded(self):
        response = self.client.get("/search?q=sqlite+inverted")
        self.assertIn(b"SQLite Full Text Search", response.data)

        response = self.client.get("/search?q=sqlite+gardening")
        self.assertNotIn(b"SQLite Full Text Search", response.data)

    def test_search_with_no_results(self):
        response = self.client.get("/search?q=zzzznotfound")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No results", response.data)

    def test_search_operators_are_literal(self):
        """FTS5 syntax must not leak into the query."""
        for term in ['sqlite OR gardening', 'sqlite*', 'NEAR(a b)', 'title:sqlite',
                     'a AND b', '^sqlite', 'sqlite -gardening']:
            response = self.client.get("/search", query_string={"q": term})
            self.assertEqual(response.status_code, 200, term)

    def test_search_excludes_drafts(self):
        self.create_post(title="DraftOnlySecret", content="uniquewordxyz", publish=False)
        response = self.client.get("/search?q=uniquewordxyz")
        self.assertNotIn(b"DraftOnlySecret", response.data)

    def test_search_ignores_fts_syntax_error(self):
        response = self.client.get("/search?q=" + "%22" * 30)
        self.assertEqual(response.status_code, 200)


# ===========================================================================
class AuthTest(BaseTestCase):
    def test_login_and_logout(self):
        response = self.login()
        self.assertIn(b"Welcome back", response.data)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

        logout = self.client.post(
            "/logout",
            data={"csrf_token": self.csrf()},
            follow_redirects=True,
        )
        self.assertIn(b"You have been signed out", logout.data)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_bad_password_rejected(self):
        response = self.login(password="wrongpass")
        self.assertIn(b"Invalid username or password", response.data)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_admin_routes_require_login(self):
        for path in ("/admin/", "/admin/posts", "/admin/comments", "/admin/media",
                     "/admin/settings", "/admin/users", "/admin/profile"):
            self.assertEqual(self.client.get(path).status_code, 302, path)

    def test_csrf_required_for_post(self):
        response = self.client.post("/login", data={"username": "admin",
                                                     "password": "admin123"})
        self.assertEqual(response.status_code, 400)

    def test_csrf_token_rotate_preserved_on_get(self):
        response = self.client.get("/login")
        self.assertIn(b'name="csrf_token"', response.data)

    def test_login_throttle(self):
        for _ in range(8):
            self.client.post(
                "/login",
                data={
                    "csrf_token": self.csrf(),
                    "username": "admin",
                    "password": "nope",
                },
            )
        response = self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf(),
                "username": "admin",
                "password": "admin123",
            },
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn(b"Too many failed attempts", response.data)

    def test_non_admin_cannot_manage_users(self):
        self.login("author", "author123")
        self.assertEqual(self.client.get("/admin/users").status_code, 403)
        response = self.client.get("/admin/posts")
        self.assertEqual(response.status_code, 200)

    def test_author_cannot_manage_site_wide_resources(self):
        self.login("author", "author123")
        for path in ("/admin/comments", "/admin/settings"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

        for path, data in (
            ("/admin/settings", {"site_title": "Changed"}),
            ("/admin/comments/1/status", {"status": "approved"}),
            ("/admin/comments/1/delete", {}),
            ("/admin/media/1/delete", {}),
            ("/admin/posts/1/delete", {}),
        ):
            with self.subTest(path=path):
                data = dict(data, csrf_token=self.csrf())
                self.assertEqual(self.client.post(path, data=data).status_code, 403)

    def test_author_admin_navigation_hides_restricted_sections(self):
        self.login("author", "author123")
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"/admin/comments", response.data)
        self.assertNotIn(b"/admin/settings", response.data)
        self.assertNotIn(b"/admin/users", response.data)

    def test_open_redirect_blocked(self):
        """A scheme-relative or absolute next= must not survive into the form."""
        for hostile in (
            "//evil.com/x",
            "https://evil.com/x",
            "/\\evil.com",
            "http://evil.com",
        ):
            self.client.get("/login", query_string={"next": hostile})
            response = self.client.get(
                "/login", query_string={"next": hostile}
            )
            match = re.search(
                rb'name="next" value="([^"]*)"', response.data
            )
            self.assertIsNotNone(match, hostile)
            rendered = match.group(1).decode()
            self.assertNotIn("evil.com", rendered, hostile)
            self.assertTrue(
                rendered.startswith("/") and not rendered.startswith("//"),
                "hostile next leaked: %r" % rendered,
            )

    def test_open_redirect_blocked_on_submit(self):
        response = self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf(),
                "username": "admin",
                "password": "admin123",
                "next": "//evil.com/x",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("evil.com", response.headers["Location"])
        self.assertIn("/admin/", response.headers["Location"])


# ===========================================================================
class CommentsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.create_post(title="Commentable")
        self.post_id = self.post_id_by_slug("commentable")
        self.client.post("/admin/profile", data={"csrf_token": self.csrf()})

    def _submit(self, name="Reader", email="r@x.com", body="Nice post", parent=""):
        return self.client.post(
            "/post/commentable/comment",
            data={
                "csrf_token": self.csrf(),
                "author_name": name,
                "author_email": email,
                "body": body,
                "parent_id": parent,
                "website": "",
            },
            follow_redirects=True,
        )

    def test_comment_is_queued_not_shown(self):
        self._submit()
        self.assertEqual(len(self.comments_on(self.post_id)), 1)
        self.assertEqual(self.comments_on(self.post_id)[0]["status"], "pending")
        page = self.client.get("/post/commentable")
        self.assertNotIn(b"Nice post", page.data)

    def test_approved_comment_is_visible(self):
        self._submit(body="Visible comment")
        with self.app.app_context():
            comment = get_db().execute(
                "SELECT id FROM comments WHERE body = 'Visible comment'"
            ).fetchone()
            get_db().execute(
                "UPDATE comments SET status='approved' WHERE id = ?", (comment["id"],)
            )
            get_db().commit()
        page = self.client.get("/post/commentable")
        self.assertIn(b"Visible comment", page.data)

    def test_validation_errors(self):
        cases = [
            {"author_name": "", "author_email": "r@x.com", "body": "hi"},
            {"author_name": "R", "author_email": "r@x.com", "body": "hi"},
            {"author_name": "R", "author_email": "bad", "body": "hi"},
            {"author_name": "R", "author_email": "r@x.com", "body": ""},
            {"author_name": "R", "author_email": "r@x.com", "body": "h"},
        ]
        for fields in cases:
            data = {"csrf_token": self.csrf(), "website": "", "parent_id": ""}
            data.update(fields)
            response = self.client.post(
                "/post/commentable/comment", data=data, follow_redirects=True
            )
            self.assertEqual(response.status_code, 200, fields)
            self.assertIn(b"flash-error", response.data, fields)
            self.assertEqual(len(self.comments_on(self.post_id)), 0, fields)

    def test_too_many_links_rejected(self):
        body = " ".join(["https://spam.example/%d" % i for i in range(5)])
        response = self._submit(body=body)
        self.assertIn(b"Too many links", response.data)
        self.assertEqual(len(self.comments_on(self.post_id)), 0)

    def test_honeypot_silently_accepted(self):
        response = self.client.post(
            "/post/commentable/comment",
            data={
                "csrf_token": self.csrf(),
                "author_name": "Bot",
                "author_email": "b@x.com",
                "body": "spam spam spam",
                "website": "http://spam",
                "parent_id": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.comments_on(self.post_id)), 0)

    def test_keyword_filter_files_as_spam(self):
        self._submit(body="buy viagra online now")
        row = self.comments_on(self.post_id)[0]
        self.assertEqual(row["status"], "spam")

    def test_comment_xss_is_escaped(self):
        self._submit(body="<script>alert(1)</script>")
        with self.app.app_context():
            get_db().execute("UPDATE comments SET status='approved'")
            get_db().commit()
        page = self.client.get("/post/commentable")
        self.assertNotIn(b"<script>alert(1)</script>", page.data)
        self.assertIn(b"&lt;script&gt;", page.data)

    def test_comment_urls_are_linkified(self):
        self._submit(body="see https://example.com/page")
        with self.app.app_context():
            get_db().execute("UPDATE comments SET status='approved'")
            get_db().commit()
        page = self.client.get("/post/commentable")
        self.assertIn(b'href="https://example.com/page"', page.data)
        self.assertIn(b'rel="nofollow noopener ugc"', page.data)

    def test_comments_disabled_on_post(self):
        self.login()
        self.create_post(title="NoComments", extra={"allow_comments": "0"})
        response = self.client.post(
            "/post/nocomments/comment",
            data={
                "csrf_token": self.csrf(),
                "author_name": "R",
                "author_email": "r@x.com",
                "body": "hello",
                "website": "",
                "parent_id": "",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_moderation_actions(self):
        self._submit(body="Moderate me")
        comment = self.comments_on(self.post_id)[0]

        response = self.client.post(
            "/admin/comments/%d/status" % comment["id"],
            data={"csrf_token": self.csrf(), "status": "approved"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.comments_on(self.post_id)[0]["status"], "approved")

        self.client.post(
            "/admin/comments/%d/delete" % comment["id"],
            data={"csrf_token": self.csrf()},
            follow_redirects=True,
        )
        self.assertEqual(len(self.comments_on(self.post_id)), 0)

    def test_threaded_replies(self):
        self._submit(body="Top level")
        parent = self.comments_on(self.post_id)[0]
        self._submit(body="A reply", parent=str(parent["id"]))
        rows = self.comments_on(self.post_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["parent_id"], parent["id"])


# ===========================================================================
class MediaTest(BaseTestCase):
    def _png_bytes(self, color=(255, 0, 0)):
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (40, 30), color).save(buffer, format="PNG")
        buffer.seek(0)
        return buffer.read()

    def test_upload_and_list(self):
        self.login()
        response = self.client.post(
            "/admin/media/upload",
            data={
                "csrf_token": self.csrf(),
                "image": (io_bytes(self._png_bytes()), "photo.png"),
                "alt_text": "A red rectangle",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"A red rectangle", response.data)
        listing = self.client.get("/admin/media")
        self.assertIn(b"photo.png", listing.data)

    def test_upload_rejects_non_image(self):
        self.login()
        response = self.client.post(
            "/admin/media/upload",
            data={
                "csrf_token": self.csrf(),
                "image": (io_bytes(b"this is not an image"), "evil.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn(b"not a valid image", response.data)

    def test_upload_rejects_bad_extension(self):
        self.login()
        response = self.client.post(
            "/admin/media/upload",
            data={
                "csrf_token": self.csrf(),
                "image": (io_bytes(self._png_bytes()), "script.exe"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn(b"Unsupported file type", response.data)

    def test_filename_is_not_derived_from_input_path(self):
        """A traversal attempt must not escape the upload directory."""
        self.login()
        self.client.post(
            "/admin/media/upload",
            data={
                "csrf_token": self.csrf(),
                "image": (io_bytes(self._png_bytes()), "../../evil.png"),
            },
            content_type="multipart/form-data",
        )
        with self.app.app_context():
            row = get_db().execute("SELECT filename FROM media").fetchone()
        if row:
            self.assertNotIn("..", row["filename"])
            self.assertNotIn("/", row["filename"])

    def test_oversized_image_is_downscaled(self):
        import io

        from PIL import Image

        self.login()
        buffer = io.BytesIO()
        Image.new("RGB", (4000, 3000), (10, 20, 30)).save(buffer, format="PNG")
        buffer.seek(0)
        self.client.post(
            "/admin/media/upload",
            data={
                "csrf_token": self.csrf(),
                "image": (io_bytes(buffer.getvalue()), "big.png"),
            },
            content_type="multipart/form-data",
        )
        with self.app.app_context():
            row = get_db().execute("SELECT filename FROM media").fetchone()
        saved = os.path.join(self.upload_dir, row["filename"])
        with Image.open(saved) as img:
            self.assertLessEqual(max(img.size), 2400)

    def test_media_file_route_serves_upload(self):
        self.login()
        self.client.post(
            "/admin/media/upload",
            data={
                "csrf_token": self.csrf(),
                "image": (io_bytes(self._png_bytes()), "served.png"),
            },
            content_type="multipart/form-data",
        )
        with self.app.app_context():
            row = get_db().execute("SELECT filename FROM media").fetchone()
        response = self.client.get("/media/" + row["filename"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


def io_bytes(data):
    import io

    return io.BytesIO(data)


# ===========================================================================
class MarkdownTest(BaseTestCase):
    def render(self, markdown):
        self.login()
        self.create_post(title="RenderTest", content=markdown)
        return self.client.get("/post/rendertest").data

    def test_basic_formatting(self):
        html = self.render("**bold** and *em* and `code`")
        self.assertIn(b"<strong>bold</strong>", html)
        self.assertIn(b"<em>em</em>", html)
        self.assertIn(b"<code>code</code>", html)

    def test_headings_and_lists(self):
        html = self.render("## Heading\n\n- one\n- two\n\n1. first\n2. second")
        self.assertIn(b'<h2 id="heading">Heading</h2>', html)
        self.assertIn(b"<ul>", html)
        self.assertIn(b"<ol>", html)

    def test_links_and_images(self):
        html = self.render("[site](https://example.com) ![alt](/media/x.png)")
        self.assertIn(b'href="https://example.com"', html)
        self.assertIn(b'src="/media/x.png"', html)

    def test_raw_html_is_escaped(self):
        html = self.render("<script>alert(1)</script>")
        self.assertNotIn(b"<script>alert(1)</script>", html)
        self.assertIn(b"&lt;script&gt;", html)

    def test_img_onerror_is_escaped(self):
        html = self.render('<img src=x onerror="alert(1)">')
        # The tag itself must not survive as live markup; the text may remain
        # as escaped entities, which is inert.
        self.assertNotIn(b"<img src=x", html)
        self.assertIn(b"&lt;img src=x", html)

    def test_code_block_is_escaped(self):
        html = self.render("```\n<script>alert(1)</script>\n```")
        self.assertNotIn(b"<script>alert(1)</script>", html)
        self.assertIn(b"&lt;script&gt;", html)

    def test_table_support(self):
        html = self.render("| a | b |\n|---|---|\n| 1 | 2 |")
        self.assertIn(b"<table>", html)

    def test_strikethrough(self):
        html = self.render("~~gone~~")
        self.assertIn(b"<del>", html)

    def test_toc_generated_for_headings(self):
        html = self.render("## First Section\n\ntext\n\n## Second Section\n\ntext")
        self.assertIn(b"Table of contents", html)
        self.assertIn(b'href="#first-section"', html)
        self.assertIn(b'href="#second-section"', html)

    def test_toc_ignores_code_fences(self):
        html = self.render(
            "```\n## Not A Heading\n```\n\n## Real Heading\n\ntext\n\n## Second"
        )
        self.assertNotIn(b'href="#not-a-heading"', html)
        self.assertIn(b'href="#real-heading"', html)
        self.assertIn(b'href="#second"', html)

    def test_heading_anchors_exist_and_match_toc(self):
        """Every TOC link must resolve to an id on a rendered heading."""
        html = self.render(
            "## Same Name\n\na\n\n### Sub *Heading*\n\nb\n\n"
            "## Same Name\n\nc\n\n## Same Name\n\nd"
        )
        anchors = re.findall(rb'<h[23] id="([^"]+)"', html)
        self.assertEqual(
            anchors,
            [b"same-name", b"sub-heading", b"same-name-1", b"same-name-2"],
        )
        # Every table-of-contents link must resolve to a real heading id.
        aside = re.findall(rb'<aside class="toc".*?</aside>', html, re.DOTALL)
        self.assertTrue(aside, "expected a table of contents")
        for anchor in set(re.findall(rb'href="#([^"]+)"', b"".join(aside))):
            self.assertIn(anchor, anchors)

    def test_duplicate_headings_get_unique_anchors(self):
        html = self.render("## Repeat\n\na\n\n## Repeat\n\nb")
        self.assertIn(b'href="#repeat"', html)
        self.assertIn(b'href="#repeat-1"', html)


# ===========================================================================
class ContentHelpersTest(BaseTestCase):
    def test_slugify(self):
        from content import slugify

        self.assertEqual(slugify("Hello, World!"), "hello-world")
        self.assertEqual(slugify("  Multiple   Spaces  "), "multiple-spaces")
        self.assertEqual(slugify("Café Ünïcode"), "cafe-unicode")
        self.assertEqual(slugify("!!!"), "")
        self.assertEqual(slugify("a" * 200), "a" * 80)

    def test_excerpt_from(self):
        from content import excerpt_from

        self.assertEqual(excerpt_from("# Title\n\nShort body."), "Title Short body.")
        long = excerpt_from("word " * 200, length=50)
        self.assertLessEqual(len(long), 51)
        self.assertTrue(long.endswith("…"))

    def test_excerpt_strips_images_and_code(self):
        from content import excerpt_from

        text = excerpt_from("![img](/a.png)\n\n```\ncode here\n```\n\nReal text")
        self.assertNotIn("img", text)
        self.assertNotIn("code here", text)
        self.assertIn("Real text", text)

    def test_excerpt_strips_tables_and_task_markers(self):
        """Table rows and checkbox markers are layout, so they stay out of
        excerpts, meta descriptions, and the RSS feed."""
        from content import excerpt_from

        text = excerpt_from(
            "Lead in.\n\n"
            "| Column | Value |\n"
            "|---|---|\n"
            "| a | 1 |\n\n"
            "- [x] done thing\n"
            "- [ ] pending thing\n\n"
            "Tail out."
        )
        self.assertEqual(text, "Lead in. done thing pending thing Tail out.")
        self.assertNotIn("|", text)
        self.assertNotIn("[x]", text)

    def test_reading_time(self):
        from content import reading_time

        self.assertEqual(reading_time("short"), 1)
        self.assertGreater(reading_time("word " * 2000), 5)

    def test_plain_text_handles_links_and_emphasis(self):
        from content import plain_text

        result = plain_text("**bold** [link](https://x.com) and ~~gone~~")
        self.assertEqual(result, "bold link and gone")

    def test_word_count(self):
        from content import word_count

        self.assertEqual(word_count("one two three"), 3)


# ===========================================================================
class FeedsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.create_post(title="Feed Post", content="Feed body content.")

    def test_rss_is_valid_xml(self):
        from xml.dom import minidom

        response = self.client.get("/feed.xml")
        self.assertEqual(response.status_code, 200)
        minidom.parseString(response.data)
        self.assertIn(b"Feed Post", response.data)

    def test_rss_only_has_published(self):
        self.create_post(title="Hidden Draft", publish=False)
        self.assertNotIn(b"Hidden Draft", self.client.get("/feed.xml").data)

    def test_sitemap_is_valid_xml(self):
        from xml.dom import minidom

        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        minidom.parseString(response.data)
        self.assertIn(b"feed-post", response.data)

    def test_robots_points_to_sitemap(self):
        response = self.client.get("/robots.txt")
        self.assertIn(b"Sitemap:", response.data)
        self.assertIn(b"Disallow: /admin", response.data)


# ===========================================================================
class PreviewTest(BaseTestCase):
    def test_preview_link_works_for_draft(self):
        self.login()
        self.create_post(title="Preview Me", publish=False)
        post_id = self.post_id_by_slug("preview-me")

        redirect = self.client.get("/admin/posts/%d/preview" % post_id)
        self.assertEqual(redirect.status_code, 302)
        response = self.client.get(redirect.headers["Location"])
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Preview of an unpublished draft", response.data)

    def test_preview_requires_login(self):
        self.login()
        self.create_post(title="Private Draft", publish=False)
        post_id = self.post_id_by_slug("private-draft")
        token = self.client.get(
            "/admin/posts/%d/preview" % post_id
        ).headers["Location"].split("/preview/")[1]

        self.client.post("/logout", data={"csrf_token": self.csrf()})
        response = self.client.get("/preview/" + token)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        # A valid token must not be usable by a signed-out visitor.
        self.assertNotIn(
            b"unpublished draft",
            self.client.get("/preview/" + token, follow_redirects=True).data,
        )

    def test_tampered_token_rejected(self):
        self.login()
        response = self.client.get("/preview/garbage.token.here")
        self.assertIn(response.status_code, (404, 410))


# ===========================================================================
class SettingsAndUsersTest(BaseTestCase):
    def test_update_settings(self):
        self.login()
        response = self.client.post(
            "/admin/settings",
            data={
                "csrf_token": self.csrf(),
                "site_title": "My Journal",
                "site_tagline": "Tagline here",
                "site_description": "A description",
                "site_author": "Jane",
                "posts_per_page": "3",
                "comments_enabled": "1",
                "footer_text": "All rights reserved",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        home = self.client.get("/")
        self.assertIn(b"My Journal", home.data)
        self.assertIn(b"Jane", home.data)

    def test_posts_per_page_applies(self):
        self.login()
        for i in range(6):
            self.create_post(title="Bulk Post %d" % i)
        self.client.post(
            "/admin/settings",
            data={
                "csrf_token": self.csrf(),
                "posts_per_page": "2",
                "site_title": "T",
                "comments_enabled": "1",
            },
            follow_redirects=True,
        )
        page1 = self.client.get("/").data
        self.assertIn(b"Bulk Post 5", page1)
        self.assertNotIn(b"Bulk Post 0", page1)
        self.assertEqual(self.client.get("/?page=2").status_code, 200)

    def test_site_wide_comment_toggle(self):
        self.login()
        self.create_post(title="Toggle Comments")
        self.client.post(
            "/admin/settings",
            data={
                "csrf_token": self.csrf(),
                "posts_per_page": "8",
                "site_title": "T",
                "comments_enabled": "0",
            },
            follow_redirects=True,
        )
        page = self.client.get("/post/toggle-comments")
        self.assertIn(b"Comments are closed", page.data)

    def test_create_user(self):
        self.login()
        response = self.client.post(
            "/admin/users/new",
            data={
                "csrf_token": self.csrf(),
                "username": "newbie",
                "email": "n@x.com",
                "password": "longpassword",
                "is_admin": "0",
            },
            follow_redirects=True,
        )
        self.assertIn(b"User created", response.data)
        self.assertIn(b"newbie", self.client.get("/admin/users").data)

    def test_duplicate_username_rejected(self):
        self.login()
        response = self.client.post(
            "/admin/users/new",
            data={
                "csrf_token": self.csrf(),
                "username": "admin",
                "email": "a@b.c",
                "password": "longpassword",
            },
            follow_redirects=True,
        )
        self.assertIn(b"taken", response.data)

    def test_weak_password_rejected(self):
        self.login()
        response = self.client.post(
            "/admin/users/new",
            data={
                "csrf_token": self.csrf(),
                "username": "weakling",
                "password": "short",
            },
            follow_redirects=True,
        )
        self.assertIn(b"at least 8", response.data)

    def test_cannot_delete_self(self):
        self.login()
        with self.app.app_context():
            my_id = get_db().execute(
                "SELECT id FROM users WHERE username='admin'"
            ).fetchone()["id"]
        response = self.client.post(
            "/admin/users/%d/delete" % my_id,
            data={"csrf_token": self.csrf()},
            follow_redirects=True,
        )
        self.assertIn(b"cannot delete your own", response.data)

    def test_change_own_password(self):
        self.login()
        response = self.client.post(
            "/admin/profile",
            data={
                "csrf_token": self.csrf(),
                "current_password": "admin123",
                "new_password": "newstrongpassword",
                "confirm_password": "newstrongpassword",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Password changed", response.data)
        # Old password must stop working, new one must work.
        self.client.post("/logout", data={"csrf_token": self.csrf()})
        self.assertIn(
            b"Invalid username or password",
            self.login(password="admin123").data,
        )
        self.client.post("/logout", data={"csrf_token": self.csrf()})
        self.assertIn(
            b"Welcome back", self.login(password="newstrongpassword").data
        )

    def test_change_password_wrong_current(self):
        self.login()
        response = self.client.post(
            "/admin/profile",
            data={
                "csrf_token": self.csrf(),
                "current_password": "wrong",
                "new_password": "newstrongpassword",
                "confirm_password": "newstrongpassword",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Current password is incorrect", response.data)


# ===========================================================================
class PaginationTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login()
        for i in range(7):
            self.create_post(title="Paged Post %d" % i, tags="paging")

    def test_pagination_controls_render(self):
        # Default page size is 8, so drop to 2 to force several pages.
        self.client.post(
            "/admin/settings",
            data={
                "csrf_token": self.csrf(),
                "site_title": "The Journal",
                "posts_per_page": "2",
                "comments_enabled": "1",
            },
            follow_redirects=True,
        )
        page = self.client.get("/").data
        self.assertIn(b"Page 1 of 4", page)

    def test_out_of_range_page_clamps_to_last(self):
        """A page number past the end shows the final page, not an empty list."""
        response = self.client.get("/?page=99")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Paged Post 0", response.data)

    def test_page_two_has_older_posts(self):
        self.client.post(
            "/admin/settings",
            data={
                "csrf_token": self.csrf(),
                "site_title": "The Journal",
                "posts_per_page": "2",
                "comments_enabled": "1",
            },
            follow_redirects=True,
        )
        page = self.client.get("/?page=2").data
        self.assertIn(b"Page 2 of 4", page)

    def test_invalid_page_falls_back(self):
        self.assertEqual(self.client.get("/?page=abc").status_code, 200)
        self.assertEqual(self.client.get("/?page=999").status_code, 200)
        self.assertEqual(self.client.get("/?page=-1").status_code, 200)

    def test_per_page_clamped(self):
        self.assertEqual(self.client.get("/?per_page=99999").status_code, 200)

    def test_pagination_preserves_tag_filter(self):
        self.client.post(
            "/admin/settings",
            data={
                "csrf_token": self.csrf(),
                "site_title": "The Journal",
                "posts_per_page": "2",
                "comments_enabled": "1",
            },
            follow_redirects=True,
        )
        page = self.client.get("/tag/paging?page=2").data
        self.assertIn(b"Page 2 of 4", page)

    def test_search_pagination_preserves_query(self):
        response = self.client.get("/search?q=paged&page=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"q=paged", response.data)


# ===========================================================================
class RelatedPostsTest(BaseTestCase):
    def test_related_posts_share_tags(self):
        self.login()
        self.create_post(title="Main Post", tags="shared")
        self.create_post(title="Related One", tags="shared")
        page = self.client.get("/post/main-post").data
        self.assertIn(b"Related reading", page)
        self.assertIn(b"Related One", page)

    def test_no_related_section_without_overlap(self):
        self.login()
        self.create_post(title="Lonely Post", tags="unique")
        page = self.client.get("/post/lonely-post").data
        self.assertNotIn(b"Related reading", page)


# ===========================================================================
class ContentSecurityTest(BaseTestCase):
    """The CSP forbids inline script, so no page may ship one."""

    PAGES = (
        "/",
        "/post/csp-target",
        "/about",
        "/login",
        "/admin/",
        "/admin/posts",
        "/admin/comments",
        "/admin/media",
        "/admin/settings",
        "/admin/users",
    )

    def _body(self, path):
        self.login()
        self.create_post(
            title="CSP Target",
            content="## A Heading\n\ntext\n\n## Another\n\nmore",
        )
        return self.client.get(path).data

    def test_no_inline_script_tags_or_handlers(self):
        for path in self.PAGES:
            body = self._body(path)
            for tag in re.findall(rb"<script\b[^>]*>", body):
                # A data block (JSON-LD) is not executed, so script-src does
                # not apply to it. Anything else must be an external file.
                if b"application/ld+json" in tag:
                    self.assertNotIn(b"src=", tag, path)
                    continue
                self.assertIn(
                    b"src=",
                    tag,
                    "%s has an inline <script>: %r" % (path, tag),
                )
            for handler in re.findall(rb'\son(click|submit|change|load)=', body):
                self.fail("%s has an inline on%s handler" % (path, handler))

    def test_csp_header_is_present_and_strict(self):
        for path in self.PAGES:
            csp = self.client.get(path).headers.get("Content-Security-Policy", "")
            self.assertIn("default-src 'self'", csp, path)
            self.assertIn("script-src 'self'", csp, path)
            self.assertNotIn("unsafe-inline", csp, path)
            self.assertNotIn("unsafe-eval", csp, path)

    def test_destructive_forms_declare_data_confirm(self):
        self.login()
        self.create_post(title="Doomed Post", tags="x")
        self.assertTrue(self.post_id_by_slug("doomed-post"))
        response = self.client.get("/admin/posts")
        self.assertIn(b'data-confirm="Delete this post permanently?"', response.data)

    def test_theme_init_is_served_as_a_static_file(self):
        response = self.client.get("/static/js/theme-init.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["Content-Type"])


if __name__ == "__main__":
    unittest.main(verbosity=2)