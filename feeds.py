"""RSS feed, sitemap, and robots.txt generation (stdlib only)."""

import html
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin
from xml.sax.saxutils import escape

from flask import Blueprint, Response, current_app, request, url_for

from db import get_db, get_setting
from queries import list_all_tags_with_counts, list_published_posts

bp = Blueprint("feeds", __name__)


def _absolute(path):
    base = current_app.config["SITE_URL"]
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _parse_timestamp(value):
    if not value:
        return datetime.now(timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


@bp.route("/feed.xml")
def rss():
    posts, _ = list_published_posts(
        {"page": 1, "per_page": 30}, per_page=30
    )
    site_title = get_setting("site_title")
    site_desc = get_setting("site_description")
    now = format_datetime(datetime.now(timezone.utc))

    items = []
    for post in posts:
        link = _absolute(url_for("blog.post_detail", slug=post["slug"]))
        pub = format_datetime(_parse_timestamp(post["published_at"]))
        description = html.escape(post["excerpt"] or post["title"])
        items.append(
            "    <item>\n"
            "      <title>%s</title>\n"
            "      <link>%s</link>\n"
            "      <guid isPermaLink=\"true\">%s</guid>\n"
            "      <pubDate>%s</pubDate>\n"
            "      <description>%s</description>\n"
            "    </item>" % (
                escape(post["title"]), escape(link), escape(link), pub, description
            )
        )

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        "    <title>%s</title>\n"
        "    <link>%s</link>\n"
        "    <description>%s</description>\n"
        "    <language>en</language>\n"
        "    <lastBuildDate>%s</lastBuildDate>\n"
        '    <atom:link href="%s" rel="self" type="application/rss+xml" />\n'
        "%s\n"
        "  </channel>\n"
        "</rss>\n" % (
            escape(site_title),
            escape(current_app.config["SITE_URL"]),
            escape(site_desc),
            now,
            escape(_absolute(url_for("feeds.rss"))),
            "\n".join(items),
        )
    )
    return Response(body, mimetype="application/rss+xml")


@bp.route("/sitemap.xml")
def sitemap():
    urls = [
        ("/", None),
        (url_for("blog.tag_index"), None),
        (url_for("blog.archive_index"), None),
        (url_for("blog.about"), None),
    ]

    posts, _ = list_published_posts({"page": 1, "per_page": 500}, per_page=500)
    for post in posts:
        urls.append(
            (url_for("blog.post_detail", slug=post["slug"]), post["updated_at"] or post["published_at"])
        )

    for tag in list_all_tags_with_counts():
        urls.append((url_for("blog.tag_detail", slug=tag["slug"]), None))

    entries = []
    for path, lastmod in urls:
        loc = escape(_absolute(path))
        if lastmod:
            day = lastmod[:10]
            entries.append(
                "  <url><loc>%s</loc><lastmod>%s</lastmod></url>" % (loc, day)
            )
        else:
            entries.append("  <url><loc>%s</loc></url>" % loc)

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "%s\n"
        "</urlset>\n" % "\n".join(entries)
    )
    return Response(body, mimetype="application/xml")


@bp.route("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Disallow: /admin",
        "Disallow: /login",
        "Disallow: /preview",
        "Allow: /",
        "",
        "Sitemap: %s" % _absolute(url_for("feeds.sitemap")),
    ]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")