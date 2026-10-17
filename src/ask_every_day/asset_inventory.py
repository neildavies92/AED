"""Strict, read-only validation of the flat SQLite-backed PNG/JPEG inventory."""

import argparse
import logging
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from ask_every_day.config import load_config
from ask_every_day.domain import AppConfig
from ask_every_day.jpeg_export import validate_jpeg
from ask_every_day.logging_config import configure_logging
from ask_every_day.repository import Repository, SQLiteRepository

logger = logging.getLogger(
    "ask_every_day.asset_inventory" if __name__ == "__main__" else __name__
)


class AssetInventoryError(ValueError):
    """The expected asset inventory is missing, malformed or contains extra entries."""


@dataclass(frozen=True)
class AssetInventoryResult:
    post_count: int
    png_count: int
    jpeg_count: int


def _validate_png(data: bytes, dimensions: tuple[int, int]) -> None:
    with Image.open(BytesIO(data)) as image:
        if image.format != "PNG" or image.size != dimensions:
            raise ValueError("Expected PNG format and configured dimensions")
        image.verify()
    with Image.open(BytesIO(data)) as image:
        image.load()


def validate_asset_inventory(
    repository: Repository, config: AppConfig
) -> AssetInventoryResult:
    """Inspect names and decoded images; never repair, write, recurse or render."""
    root = config.assets_path
    context = {"output_path": str(root)}
    logger.info(
        "asset_inventory_validation_started",
        extra={
            "event": "asset_inventory_validation_started",
            **context,
        },
    )
    try:
        posts = repository.list_posts()
        ids = [post.id for post in posts]
        if not ids:
            raise AssetInventoryError("No Posts found; run make import-content first.")
        if len(set(ids)) != len(ids):
            raise AssetInventoryError("Duplicate expected Post IDs")
        if root.is_symlink() or not root.is_dir():
            raise AssetInventoryError(
                "Assets path must be an existing regular directory"
            )
        expected = {
            f"{post_id}{suffix}" for post_id in ids for suffix in (".png", ".jpg")
        }
        entries = {entry.name: entry for entry in root.iterdir()}
        details: list[str] = []
        issue_count = 0

        def issue(name: str, reason: str) -> None:
            nonlocal issue_count
            issue_count += 1
            details.append(f"{name[:160]!r}: {reason}")
            details.sort()
            del details[10:]

        for name in sorted(expected - entries.keys()):
            issue(name, "missing asset")
        for name in sorted(entries):
            path = entries[name]
            if name not in expected:
                issue(name, "unexpected entry")
                continue
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    issue(name, "expected a regular file, not a directory or symlink")
                    continue
                # JPEG's authoritative validator enforces its existing byte limit.
                data = path.read_bytes()
                if name.endswith(".png"):
                    _validate_png(data, (config.renderer.width, config.renderer.height))
                else:
                    validate_jpeg(data)
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                issue(
                    name, "unreadable or invalid image format/dimensions/representation"
                )
        if issue_count:
            raise AssetInventoryError(
                f"Asset inventory has {issue_count} issue(s); showing {len(details)}: "
                + "; ".join(details)
            )
    except Exception as error:
        logger.error(
            "asset_inventory_validation_failed",
            extra={
                "event": "asset_inventory_validation_failed",
                "error_type": type(error).__name__,
                **context,
            },
        )
        if isinstance(error, AssetInventoryError):
            raise
        raise AssetInventoryError(
            "Cannot read asset inventory or repository"
        ) from error
    logger.info(
        "asset_inventory_validated",
        extra={
            "event": "asset_inventory_validated",
            "post_count": len(ids),
            **context,
        },
    )
    return AssetInventoryResult(len(ids), len(ids), len(ids))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the local PNG/JPEG inventory"
    )
    parser.add_argument("--config", type=Path, default=Path("aed.toml"))
    args = parser.parse_args()
    configure_logging()
    try:
        config = load_config(args.config)
        # Normal open_repository may create/migrate; validation must not do either.
        uri = config.database_path.absolute().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            result = validate_asset_inventory(SQLiteRepository(connection), config)
        print(
            f"Validated {result.post_count} Posts: "
            f"{result.png_count} PNG and {result.jpeg_count} JPEG assets"
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        if not isinstance(error, AssetInventoryError):
            logger.error(
                "asset_inventory_validation_failed",
                extra={
                    "event": "asset_inventory_validation_failed",
                    "error_type": type(error).__name__,
                },
            )
        message = (
            str(error)
            if isinstance(error, AssetInventoryError)
            else (
                "Cannot open config or existing current-schema SQLite database; "
                "import/migrate content before validation."
            )
        )
        parser.exit(1, message + "\n")


if __name__ == "__main__":
    main()
