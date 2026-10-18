"""Decode and resize frames in memory; never persist full camera images."""
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_FRAME_BYTES = 8 * 1024 * 1024


class InvalidFrame(ValueError):
    """An upload cannot be decoded as a camera frame."""


def prepare_frame(data: bytes, max_px: int = 640) -> bytes:
    """Return an upright JPEG, preserving aspect ratio and stripping metadata."""
    if not data or len(data) > MAX_FRAME_BYTES:
        raise InvalidFrame("Frame must be nonempty and at most 8 MiB.")
    if not 128 <= max_px <= 2048:
        raise ValueError("Frame size must be between 128 and 2048 pixels.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise InvalidFrame("Use a JPEG, PNG, or WebP frame.")
                picture = ImageOps.exif_transpose(source).convert("RGB")
                picture.thumbnail((max_px, max_px))
                output = io.BytesIO()
                picture.save(output, "JPEG", quality=80)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise InvalidFrame("Frame is not a decodable image.") from exc
