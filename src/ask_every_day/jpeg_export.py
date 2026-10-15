"""Publishable JPEG bytes from the project's sRGB PNG rendering pipeline."""

import logging
from io import BytesIO

from PIL import Image

logger = logging.getLogger(__name__)
SIZE = (1080, 1350)
MAX_JPEG_BYTES = 8_000_000
BACKGROUND = (244, 240, 232)


class JPEGExportError(ValueError):
    """Source or result does not satisfy the project's JPEG contract."""


def _dimensions(image: Image.Image) -> None:
    if image.size != SIZE:
        raise JPEGExportError(
            "Image must be exactly 1080x1350; resizing is not implicit"
        )


def validate_jpeg(data: bytes) -> None:
    """Validate a complete, untagged sRGB-contract RGB JPEG without writes.

    This checks representation, not the unknowable color provenance of arbitrary
    untagged pixels. Callers must supply project-pipeline sRGB content.
    """
    if not isinstance(data, bytes):
        raise TypeError("JPEG data must be bytes")
    try:
        if len(data) > MAX_JPEG_BYTES:
            raise JPEGExportError("JPEG exceeds the 8,000,000-byte limit")
        if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
            raise JPEGExportError("JPEG must have complete SOI/EOI markers")
        with Image.open(BytesIO(data)) as image:
            if image.format != "JPEG" or image.mode != "RGB":
                raise JPEGExportError("Output must be an RGB JPEG without alpha")
            _dimensions(image)
            if any(key in image.info for key in ("icc_profile", "exif", "comment")):
                raise JPEGExportError("Output must omit ICC, EXIF and comment metadata")
            image.load()
    except JPEGExportError:
        raise
    except (OSError, ValueError, SyntaxError) as error:
        raise JPEGExportError("Malformed JPEG data") from error


def export_jpeg(png: bytes) -> bytes:
    """Convert RGB/RGBA project PNG to fixed-size, metadata-free RGB JPEG.

    Untagged input is interpreted as sRGB by project contract, not color-detected.
    Profile/gamma/chromaticity overrides are rejected, never silently converted.
    Transparency is flattened onto the Classic warm background.
    """
    if not isinstance(png, bytes):
        raise TypeError("PNG data must be bytes")
    context = {"format": "jpeg", "width": SIZE[0], "height": SIZE[1]}
    try:
        with Image.open(BytesIO(png)) as source:
            if source.format != "PNG":
                raise JPEGExportError("Source must be PNG bytes")
            _dimensions(source)
            if source.mode not in {"RGB", "RGBA"}:
                raise JPEGExportError("Source PNG must use RGB or RGBA mode")
            if "transparency" in source.info:
                raise JPEGExportError(
                    "PNG color-key transparency must be converted to RGBA"
                )
            if getattr(source, "n_frames", 1) != 1:
                raise JPEGExportError("Animated PNG is unsupported")
            if any(
                key in source.info for key in ("icc_profile", "gamma", "chromaticity")
            ):
                raise JPEGExportError("PNG color profile overrides are unsupported")
            if "srgb" in source.info and source.info["srgb"] not in range(4):
                raise JPEGExportError("PNG sRGB rendering intent is invalid")
            source.verify()
        with Image.open(BytesIO(png)) as source:
            source.load()
            # A new image prevents source EXIF, text and other metadata propagation.
            with Image.new("RGB", SIZE, BACKGROUND) as rgb:
                if source.mode == "RGBA":
                    with source.getchannel("A") as alpha:
                        rgb.paste(source, mask=alpha)
                else:
                    rgb.paste(source)
                output = BytesIO()
                rgb.save(
                    output,
                    format="JPEG",
                    quality=95,
                    subsampling=0,
                    progressive=False,
                    optimize=False,
                    exif=b"",
                    icc_profile=None,
                )
                result = output.getvalue()
        validate_jpeg(result)
    except (OSError, ValueError, SyntaxError) as error:
        logger.warning(
            "jpeg_export_failed",
            extra={
                "event": "jpeg_export_failed",
                "error_type": type(error).__name__,
                **context,
            },
        )
        if isinstance(error, JPEGExportError):
            raise
        raise JPEGExportError("PNG could not be exported to JPEG") from error
    logger.info(
        "jpeg_exported",
        extra={"event": "jpeg_exported", "byte_size": len(result), **context},
    )
    return result
