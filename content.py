"""Content processing: Markdown rendering, slugs, excerpts, reading time, TOC."""

import html
import re
import unicodedata

import mistune
from markupsafe import Markup

_PLUGINS = ["strikethrough", "table", "footnotes", "url", "task_lists"]

# HTML is escaped rather than passed through, so authors cannot inject scripts
# or arbitrary markup. Code blocks are escaped by mistune as well.


_heading_ids = {}


def _heading_text(raw):
    """Reduce a rendered heading to the plain text the TOC will show."""
    text = html.unescape(re.sub(r"<[^>]+>", "", raw or ""))
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip().rstrip("#").strip()


def _heading_id(text):
    """Return a unique anchor id, matching what table_of_contents generates."""
    base = slugify(_heading_text(text)) or "section"
    count = _heading_ids.get(base, 0)
    _heading_ids[base] = count + 1
    return base if count == 0 else "%s-%d" % (base, count)


class _HeadingIdsRenderer(mistune.HTMLRenderer):
    """Add id attributes to h2/h3 so the table of contents can link to them."""

    def heading(self, text, level, **attrs):
        if level in (2, 3) and not attrs.get("id"):
            attrs["id"] = _heading_id(text)
        return super().heading(text, level, **attrs)


_markdown = mistune.create_markdown(
    escape=True, plugins=_PLUGINS, renderer=_HeadingIdsRenderer()
)


def render_markdown(text):
    """Render Markdown to sanitized HTML, with anchor ids on h2-h4."""
    if not text:
        return Markup("")
    _heading_ids.clear()
    return Markup(_markdown(text))


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_NON_WORD = re.compile(r"[^a-z0-9-]+")


def slugify(value):
    """Lowercase ASCII slug. Non-ASCII letters are folded, not dropped."""
    if not value:
        return ""
    folded = unicodedata.normalize("NFKD", str(value))
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_only.lower()).strip("-")
    return slug[:80].strip("-")


def is_valid_slug(value):
    return bool(value) and _NON_WORD.fullmatch(value) is not None and len(value) <= 80


# ---------------------------------------------------------------------------
# Excerpts
# ---------------------------------------------------------------------------
_FRONTMATTER = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_HEADING_LINE = re.compile(r"^\s*#{1,6}\s")
_CODE_FENCE = re.compile(r"^\s*(```|~~~)")
# A table row is layout, not prose; it should not end up in an excerpt.
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$|^\s*\|?[\s:-]*-{3,}[\s:|-]*\|?\s*$")


def plain_text(markdown_text):
    """Rough Markdown-to-text reduction, used for excerpts and meta tags."""
    text = _FRONTMATTER.sub("", markdown_text or "")
    lines = []
    in_fence = False
    for line in text.splitlines():
        if _CODE_FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or _IMAGE_LINE.match(line) or _TABLE_ROW.match(line):
            continue
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", line)
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^\s{0,3}>\s?", "", line)
        line = re.sub(r"^\s{0,3}([-*+]|\d+\.)\s+", "", line)
        line = re.sub(r"^\[[ xX]\]\s*", "", line)
        line = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", r"\1\2", line)
        line = re.sub(r"\*([^*]+)\*|_([^_]+)_", r"\1\2", line)
        line = re.sub(r"~~([^~]+)~~", r"\1", line)
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def excerpt_from(markdown_text, length=200):
    """Build a plain-text excerpt, trimmed on a word boundary."""
    text = plain_text(markdown_text).replace("\n", " ").strip()
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return (cut or text[:length]).rstrip() + "…"


def reading_time(markdown_text):
    """Minutes to read, at 225 words per minute, minimum one minute."""
    words = len(plain_text(markdown_text).split())
    return max(1, round(words / 225.0 + 0.499))


def word_count(markdown_text):
    return len(plain_text(markdown_text).split())


# ---------------------------------------------------------------------------
# Table of contents
# ---------------------------------------------------------------------------
def table_of_contents(markdown_text):
    """Collect h2/h3 headings into a nested structure for a sidebar TOC.

    Heading text is escaped here and marked safe so Jinja renders it as text.
    """
    from markupsafe import escape

    in_fence = False
    toc = []
    slug_counts = {}
    used = set()

    for line in (markdown_text or "").splitlines():
        if _CODE_FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^\s{0,3}(#{2,3})\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        level = len(match.group(1))
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", match.group(2))
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"[*_~]", "", text).strip()
        if not text:
            continue

        base = slugify(text) or "section"
        count = slug_counts.get(base, 0)
        slug_counts[base] = count + 1
        anchor = base if count == 0 and base not in used else "%s-%d" % (base, count)
        used.add(anchor)

        entry = {"level": level, "text": text, "anchor": anchor, "children": []}
        if level == 2 or not toc:
            toc.append(entry)
        else:
            toc[-1]["children"].append(entry)

    return toc


# ---------------------------------------------------------------------------
# Comment rendering
# ---------------------------------------------------------------------------
_COMMENT_LINK = re.compile(r"(https?://[^\s<>\"']+)")


def linkify_comments(text):
    """Escape comment text, then turn bare URLs into safe links."""
    from markupsafe import escape

    escaped = str(escape(text or ""))
    return _COMMENT_LINK.sub(
        lambda m: '<a href="%s" rel="nofollow noopener ugc" target="_blank">%s</a>'
        % (m.group(1), m.group(1)),
        escaped,
    )