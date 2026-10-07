"""
Helpers for handling user-uploaded images (avatars).

Files are stored on the local filesystem under app.config['UPLOAD_FOLDER']
(the ./uploads/ directory, which is excluded from git). Uploaded images are
re-encoded with Pillow to strip EXIF/metadata and any embedded payloads,
resized to a sane maximum, and saved under a random filename.
"""

import os
import uuid
from io import BytesIO

from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

# Stored avatars are square-ish profile pictures; cap dimensions to keep
# disk usage predictable.
MAX_IMAGE_DIMENSION = 512
BACKGROUND_MAX_IMAGE_DIMENSION = 1920


def allowed_file(filename, app_config):
    return ('.' in filename and
            filename.rsplit('.', 1)[1].lower()
            in app_config['ALLOWED_EXTENSIONS'])


def save_upload_image(file_storage, app_config, subdir='avatars',
                      max_kb=None, max_dimension=MAX_IMAGE_DIMENSION):
    """Validate + persist an uploaded image.

    :param file_storage: ``request.files[...]`` result (may be None)
    :param app_config: current app's config dict-like
    :param subdir: folder inside UPLOAD_FOLDER (kept generic so other
                   image types can reuse this later)
    :param max_kb: optional per-file size limit in KB (from site settings)
    :param max_dimension: largest retained width or height after re-encoding
    :returns: relative path like ``avatars/3-a1b2c3d4.jpg`` (store this in
              the DB; serve it via the /uploads route)
    :raises ValueError: with a user-facing message if invalid
    """
    if file_storage is None or not file_storage.filename:
        raise ValueError('No file was selected.')

    filename = secure_filename(file_storage.filename)
    if not allowed_file(filename, app_config):
        raise ValueError('Only PNG, JPG, GIF or WEBP images are allowed.')

    raw = file_storage.read()
    if max_kb and len(raw) > max_kb * 1024:
        raise ValueError(f'Image is too large (max {max_kb} KB).')

    # Re-encode through Pillow: rejects non-images regardless of extension,
    # strips EXIF/GPS metadata and any appended malicious payloads.
    try:
        img = Image.open(BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_dimension, max_dimension))
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1])
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
    except Exception:
        raise ValueError('The uploaded file could not be read as an image.')

    target_dir = os.path.join(app_config['UPLOAD_FOLDER'], subdir)
    os.makedirs(target_dir, exist_ok=True)

    out_name = f'{uuid.uuid4().hex}.jpg'
    out_path = os.path.join(target_dir, out_name)
    img.save(out_path, format='JPEG', quality=88, optimize=True)

    return f'{subdir}/{out_name}'


def delete_upload_image(rel_path, app_config):
    """Best-effort removal of a previously stored upload."""
    if not rel_path:
        return
    base = os.path.abspath(app_config['UPLOAD_FOLDER'])
    full = os.path.abspath(os.path.join(base, rel_path))
    # Guard against path traversal outside the uploads root.
    if not full.startswith(base + os.sep):
        return
    try:
        if os.path.isfile(full):
            os.remove(full)
    except OSError:
        pass
