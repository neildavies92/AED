"""Hand-set design spike; automatic text layout belongs to later tickets."""

import logging
from pathlib import Path

from ask_every_day import renderer
from ask_every_day.domain import Post, RendererConfig
from ask_every_day.logging_config import configure_logging

logger = logging.getLogger(
    "ask_every_day.classic_prototype" if __name__ == "__main__" else __name__
)

SAMPLE_POST = Post(
    id="AED-0001",
    prompt="Who are you when no one needs anything from you?",
    category="Self-awareness",
    subcategory="Identity",
    template="Classic",
    highlight_text="no one needs anything from you",
)
# Each line contains unhighlighted prefix, highlighted text, and suffix.
SAMPLE_LINES = (
    ("Who are you when", "", ""),
    ("", "no one needs", ""),
    ("", "anything from you", "?"),
)


def render_prototype(post: Post, lines: tuple[tuple[str, str, str], ...]) -> str:
    """Render explicitly art-directed lines, with no wrapping or font measuring."""
    return renderer.render_svg(post, RendererConfig(), lines=lines)


def main() -> None:
    """Write the deliberate reference specimen to the ignored output directory."""
    configure_logging()
    output = Path("output/classic-prototype.svg")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_prototype(SAMPLE_POST, SAMPLE_LINES), encoding="utf-8")
    except (OSError, ValueError) as error:
        logger.error(
            "Classic prototype failed",
            extra={
                "event": "classic_prototype_failed",
                "post_id": SAMPLE_POST.id,
                "output_path": str(output),
                "error_type": type(error).__name__,
            },
        )
        raise SystemExit(1) from None
    logger.info(
        "Classic prototype written",
        extra={
            "event": "classic_prototype_written",
            "post_id": SAMPLE_POST.id,
            "output_path": str(output),
            "width": 1080,
            "height": 1350,
        },
    )


if __name__ == "__main__":
    main()
