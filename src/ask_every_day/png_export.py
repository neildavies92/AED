"""In-memory CairoSVG conversion, separate from text layout and asset storage."""

import logging
from xml.etree import ElementTree

from ask_every_day.domain import RendererConfig

logger = logging.getLogger(__name__)


class PNGExportError(ValueError):
    """SVG cannot be converted, or the native Cairo runtime is unavailable."""


def export_png(svg: str, config: RendererConfig) -> bytes:
    """Return PNG bytes at config dimensions, preserving the SVG background.

    Intended for renderer-produced SVG, not arbitrary untrusted uploads. External
    resources and XML entities stay disabled by CairoSVG's safe conversion mode.
    No files are written; callers own storage and directory creation.
    """
    if not isinstance(svg, str) or not isinstance(config, RendererConfig):
        raise TypeError("export_png requires an SVG string and RendererConfig")
    context = {"format": "png", "width": config.width, "height": config.height}
    try:
        if "<!DOCTYPE" in svg or "<!ENTITY" in svg:
            raise PNGExportError("SVG document types and entities are unsupported")
        root = ElementTree.fromstring(svg)
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise PNGExportError("Input must have a namespaced SVG root element")
        try:
            import cairosvg
        except (ImportError, OSError) as error:
            raise PNGExportError(
                "CairoSVG could not load; install native Cairo and libffi "
                "as described in docs/png-export.md"
            ) from error
        result = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            output_width=config.width,
            output_height=config.height,
            unsafe=False,
        )
        if not isinstance(result, bytes):
            raise PNGExportError("CairoSVG did not return PNG bytes")
    except Exception as error:
        logger.warning(
            "png_export_failed",
            extra={
                "event": "png_export_failed",
                "error_type": type(error).__name__,
                **context,
            },
        )
        if isinstance(error, PNGExportError):
            raise
        raise PNGExportError(
            "SVG could not be exported to PNG; check its syntax and supported features"
        ) from error
    logger.info("png_exported", extra={"event": "png_exported", **context})
    return result
