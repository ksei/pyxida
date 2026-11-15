"""Local applicant-photo validation and print-ready portrait normalization."""
import base64
import io
import warnings
from datetime import datetime, timezone

from ai import WorkflowError

DEFAULT_DOCUMENT_SETTINGS = {"photo_cv": True, "photo_letter": False}
MAX_PHOTO_BYTES = 5 * 1024 * 1024


def document_settings(values=None):
    values = DEFAULT_DOCUMENT_SETTINGS if values is None else values
    if not isinstance(values, dict) or set(values) != set(DEFAULT_DOCUMENT_SETTINGS):
        raise WorkflowError("Choose whether the picture appears on the CV and cover letter.")
    if any(type(value) is not bool for value in values.values()):
        raise WorkflowError("Picture options must be checked or unchecked.")
    return dict(values)


def normalize_photo(encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as e:
        raise WorkflowError("The picture could not be read. Upload a JPEG, PNG or WebP image.") from e
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise WorkflowError("Choose a picture smaller than 5 MB.")
    try:
        from PIL import Image, ImageOps
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.format not in ("JPEG", "PNG", "WEBP"):
                    raise WorkflowError("Use a JPEG, PNG or WebP picture.")
                if original.width * original.height > 24000000:
                    raise WorkflowError("Choose a picture with at most 24 megapixels.")
                image = ImageOps.exif_transpose(original)
                if image.width < 80 or image.height < 100:
                    raise WorkflowError("Choose a larger picture so it stays clear in print.")
                image = ImageOps.fit(image.convert("RGBA"), (640, 800), Image.Resampling.LANCZOS)
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                output = io.BytesIO()
                background.save(output, format="JPEG", quality=94, optimize=True)
    except WorkflowError:
        raise
    except ImportError as e:
        raise WorkflowError("Install requirements.txt to enable applicant pictures.") from e
    except Exception as e:
        raise WorkflowError("This picture could not be decoded. Try another JPEG or PNG.") from e
    return {"data": base64.b64encode(output.getvalue()).decode(), "mime": "image/jpeg",
            "width": 640, "height": 800, "updated_at": datetime.now(timezone.utc).isoformat()}


def photo_bytes(photo):
    return base64.b64decode(photo["data"]) if photo else None


def document_photo(state, kind):
    settings = state.get("document_settings", DEFAULT_DOCUMENT_SETTINGS)
    enabled = settings["photo_cv" if kind == "cv" else "photo_letter"]
    return photo_bytes(state.get("photo")) if enabled else None
