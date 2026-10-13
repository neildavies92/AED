"""Deterministic SVG generation; layout and output storage are separate concerns."""

import logging
from xml.etree import ElementTree

from jinja2 import Environment, PackageLoader, StrictUndefined, TemplateError

from ask_every_day.domain import Post, RendererConfig

logger = logging.getLogger(__name__)
_TEMPLATES = {"Classic": "classic.svg.jinja"}
type ManualLines = tuple[tuple[str, str, str], ...]


class RenderError(ValueError):
    """Unsupported configuration, inconsistent layout, or broken SVG template."""


def render_svg(
    post: Post, config: RendererConfig, *, lines: ManualLines | None = None
) -> str:
    """Return SVG without writes; optional hand-set lines bridge AED-011/012.

    Without explicit lines the entire prompt is one unhighlighted line. No fit
    guarantee is made. Config selects the template and must match the Post.
    """
    if not isinstance(post, Post) or not isinstance(config, RendererConfig):
        raise TypeError("render_svg requires Post and RendererConfig values")
    context = {
        "post_id": post.id,
        "template": config.template,
        "width": config.width,
        "height": config.height,
    }
    try:
        if config.template not in _TEMPLATES:
            raise RenderError(f"Unsupported template: {config.template!r}")
        if post.template != config.template:
            raise RenderError("Post template must match RendererConfig.template")
        if lines is None:
            lines = ((post.prompt, "", ""),)
        else:
            if " ".join("".join(line) for line in lines) != post.prompt:
                raise RenderError("Hand-set lines must reproduce the exact prompt")
            if " ".join(line[1] for line in lines if line[1]) != (
                post.highlight_text or ""
            ):
                raise RenderError("Hand-set highlight must reproduce highlight_text")
        environment = Environment(
            loader=PackageLoader("ask_every_day", "templates"),
            autoescape=True,
            undefined=StrictUndefined,
        )
        svg = environment.get_template(_TEMPLATES[config.template]).render(
            post=post, config=config, lines=lines
        )
        root = ElementTree.fromstring(svg)
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise RenderError("Template must produce an SVG root element")
    except (TemplateError, ElementTree.ParseError, RenderError) as error:
        logger.warning(
            "svg_render_failed",
            extra={
                "event": "svg_render_failed",
                "error_type": type(error).__name__,
                **context,
            },
        )
        if isinstance(error, RenderError):
            raise
        raise RenderError(
            "SVG template could not be rendered; check template/content"
        ) from error
    logger.info("svg_rendered", extra={"event": "svg_rendered", **context})
    return svg
