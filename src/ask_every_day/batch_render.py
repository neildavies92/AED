"""Generate local PNG/JPEG assets from persisted Posts, with per-file replacement."""

import argparse
import logging
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.domain import AppConfig
from ask_every_day.jpeg_export import export_jpeg
from ask_every_day.logging_config import configure_logging
from ask_every_day.png_export import export_png
from ask_every_day.renderer import render_svg
from ask_every_day.repository import Repository, open_repository

logger = logging.getLogger(
    "ask_every_day.batch_render" if __name__ == "__main__" else __name__
)


class BatchRenderError(ValueError):
    """A batch stopped; prior completed assets remain available for a safe rerun."""


@dataclass(frozen=True)
class BatchRenderResult:
    post_count: int
    png_count: int
    jpeg_count: int


def _write_pair(directory: Path, post_id: str, png: bytes, jpeg: bytes) -> None:
    staged: list[tuple[Path, Path]] = []
    try:
        for suffix, payload in ((".png", png), (".jpg", jpeg)):
            with tempfile.NamedTemporaryFile(
                dir=directory, prefix=f".{post_id}-", suffix=".tmp", delete=False
            ) as stream:
                staged.append((Path(stream.name), directory / f"{post_id}{suffix}"))
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        for temporary, destination in staged:
            os.replace(temporary, destination)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def render_assets(repository: Repository, config: AppConfig) -> BatchRenderResult:
    """Render repository Posts in ID order; output writes belong only here.

    Each file is replaced atomically, but the pair and batch are not transactions.
    Unknown files are left alone. PNG and JPEG export finish before staging either.
    """
    completed = 0
    post_id: str | None = None
    logger.info(
        "batch_render_started",
        extra={
            "event": "batch_render_started",
            "output_path": str(config.assets_path),
        },
    )
    try:
        posts = repository.list_posts()
        if not posts:
            raise BatchRenderError("No Posts found; run make import-content first.")
        config.assets_path.mkdir(parents=True, exist_ok=True)
        for post in posts:
            post_id = post.id
            svg = render_svg(post, config.renderer)
            png = export_png(svg, config.renderer)
            jpeg = export_jpeg(png)
            _write_pair(config.assets_path, post.id, png, jpeg)
            completed += 1
    except Exception as error:
        logger.error(
            "batch_render_failed",
            extra={
                "event": "batch_render_failed",
                "post_id": post_id,
                "post_count": completed,
                "output_path": str(config.assets_path),
                "error_type": type(error).__name__,
            },
        )
        if isinstance(error, BatchRenderError):
            raise
        location = f" for {post_id}" if post_id else " before rendering"
        raise BatchRenderError(
            f"Batch stopped{location} after {completed} complete Posts "
            f"({type(error).__name__}); correct the cause and rerun."
        ) from error
    logger.info(
        "batch_render_completed",
        extra={
            "event": "batch_render_completed",
            "post_count": completed,
            "output_path": str(config.assets_path),
        },
    )
    return BatchRenderResult(completed, completed, completed)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render persisted Posts to PNG and JPEG"
    )
    parser.add_argument("--config", type=Path, default=Path("aed.toml"))
    args = parser.parse_args()
    configure_logging()
    try:
        config = load_config(args.config)
        with open_repository(config) as repository:
            result = render_assets(repository, config)
        print(
            f"Rendered {result.post_count} Posts: "
            f"{result.png_count} PNG and {result.jpeg_count} JPEG assets"
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        if not isinstance(error, BatchRenderError):
            logger.error(
                "batch_render_failed",
                extra={
                    "event": "batch_render_failed",
                    "error_type": type(error).__name__,
                },
            )
        message = (
            str(error)
            if isinstance(error, BatchRenderError)
            else (
                f"Batch setup failed ({type(error).__name__}); inspect config/local state."
            )
        )
        parser.exit(1, message + "\n")


if __name__ == "__main__":
    main()
