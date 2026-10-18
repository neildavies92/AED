"""In-memory CairoSVG conversion, separate from text layout and asset storage."""

import logging
from xml.etree import ElementTree

from ask_every_day.domain import RendererConfig

logger = logging.getLogger(__name__)


class PNGExportError(ValueError):
    """SVG cannot be converted, or the native Cairo runtime is unavailable."""


def _center_classic_brand(root: ElementTree.Element, config: RendererConfig) -> bool:
    """Work around CairoSVG #317 for the canonical two-colour header only."""
    namespace = "{http://www.w3.org/2000/svg}"
    brand = root.find(f".//{namespace}text[@id='classic-brand']")
    if brand is None:
        return False
    from cairosvg.surface import cairo

    # Measure with the same backend and scale as rasterization, never a guessed width.
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    context = cairo.Context(surface)
    scale = min(config.width / 1080, config.height / 1350)
    context.scale(scale, scale)
    group = root.find(namespace + "g")
    assert group is not None
    family = group.attrib["font-family"].split(",")[0].strip()
    context.select_font_face(family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    context.set_font_size(float(brand.attrib["font-size"]))
    text = "".join(brand.itertext())
    bearing, _, width, _, _, _ = context.text_extents(text)
    spacing = float(brand.attrib["letter-spacing"])
    offset = bearing + width / 2 + (len(text) - 1) * spacing / 2
    surface.finish()
    brand.set("x", str(float(brand.attrib["x"]) - offset))
    brand.set("text-anchor", "start")
    # CairoSVG restarts letter spacing at each span; preserve the one boundary gap.
    brand[1].set("dx", str(spacing))
    return True


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
        source = (
            ElementTree.tostring(root)
            if _center_classic_brand(root, config)
            else svg.encode("utf-8")
        )
        result = cairosvg.svg2png(
            bytestring=source,
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
