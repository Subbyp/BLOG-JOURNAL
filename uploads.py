"""Image upload handling: validation, safe filenames, storage."""

import os
import secrets
import time

from PIL import Image
from werkzeug.utils import secure_filename

from content import slugify
from db import ensure_upload_dir
from queries import create_media


class UploadError(Exception):
    pass


MAX_DIMENSION = 2400
THUMBNAIL_WIDTH = 640


def _allowed(filename, allowed):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in allowed
    )


def _verify_image(stream):
    """Confirm the bytes are a real image and identify the format."""
    try:
        stream.seek(0)
        probe = Image.open(stream)
        probe.verify()
        stream.seek(0)
        reopened = Image.open(stream)
        return reopened.format.lower(), reopened.size
    except Exception:
        raise UploadError("That file is not a valid image.")
    finally:
        stream.seek(0)


def save_image(file_storage, alt_text="", user_id=None):
    """Persist an uploaded image, downscale if oversized, and record metadata.

    Returns the created media row id.
    """
    from flask import current_app

    allowed = current_app.config["ALLOWED_IMAGE_EXTENSIONS"]
    filename = file_storage.filename or ""
    if not filename or not _allowed(filename, allowed):
        raise UploadError(
            "Unsupported file type. Allowed: %s." % ", ".join(sorted(allowed))
        )

    fmt, size = _verify_image(file_storage.stream)

    folder = ensure_upload_dir()
    stem = slugify(os.path.splitext(secure_filename(filename))[0]) or "image"
    ext = "jpg" if fmt in {"jpeg", "jpg"} else fmt
    unique = "%s-%s.%s" % (stem[:50], secrets.token_hex(4), ext)
    destination = os.path.join(folder, unique)

    try:
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)
        image = _flatten_for_jpeg(image, fmt)
        if max(image.size) > MAX_DIMENSION:
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
        save_kwargs = _save_options(fmt)
        image.save(destination, **save_kwargs)
    except UploadError:
        raise
    except Exception:
        raise UploadError("The image could not be processed.")

    actual_size = os.path.getsize(destination)
    mime = Image.MIME.get(fmt.upper(), "image/%s" % ext)

    media_id = create_media(
        filename=unique,
        original_name=filename[:255],
        mime_type=mime,
        size_bytes=actual_size,
        alt_text=(alt_text or "")[:200],
        user_id=user_id,
    )
    return media_id


def _flatten_for_jpeg(image, fmt):
    if fmt in {"jpeg", "jpg"} and image.mode in {"RGBA", "P", "LA"}:
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
        return background
    if fmt in {"jpeg", "jpg"} and image.mode != "RGB":
        return image.convert("RGB")
    return image


def _save_options(fmt):
    if fmt in {"jpeg", "jpg"}:
        return {"quality": 85, "optimize": True, "progressive": True}
    if fmt == "png":
        return {"optimize": True}
    return {}


def media_url(filename):
    from flask import url_for

    return url_for("media_file", filename=filename)


def delete_file(filename):
    from flask import current_app

    if not filename:
        return
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.basename(filename))
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass