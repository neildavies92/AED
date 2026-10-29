"""Verify the approved Docker font files and exact fresh raster bytes."""

import hashlib
import json
import sys
from pathlib import Path

from ask_every_day.domain import AppConfig
from ask_every_day.font_review import inventory
from ask_every_day.jpeg_export import export_jpeg
from ask_every_day.media_staging import _jpeg
from ask_every_day.png_export import export_png
from ask_every_day.publication_assets import _png_hash, read_ledger, themed_renderer
from ask_every_day.publish_next import PublishPlan
from ask_every_day.renderer import render_svg
from ask_every_day.repository import Repository

_KEYS = (
    "requested_family",
    "requested_style",
    "fontconfig_version",
    "face_index",
    "sha256",
)


class CanonicalRendererError(ValueError):
    """Fixed operational diagnostics only."""


def docker_runtime() -> bool:
    return sys.platform == "linux" and Path("/.dockerenv").is_file()


def verify_editorial_fonts() -> list[dict]:
    if not docker_runtime():
        raise CanonicalRendererError("canonical_docker_renderer_required")
    rows = inventory()
    expected = json.loads(Path(__file__).with_name("editorial-fonts.json").read_text())
    if (
        len(rows) != 8
        or not all(row["exact_family"] and row["style_matches"] for row in rows)
        or [{key: row[key] for key in _KEYS} for row in rows] != expected
    ):
        raise CanonicalRendererError("approved_editorial_font_files_required")
    return rows


def canonical_asset_valid(
    repository: Repository, config: AppConfig, plan: PublishPlan
) -> bool:
    if plan.asset_sha256 is None:
        return False
    settings = themed_renderer(config, plan.background_color)
    png = export_png(render_svg(plan.post, settings), settings)
    jpeg_hash = hashlib.sha256(export_jpeg(png)).hexdigest()
    ledger = read_ledger(config)
    asset = ledger.assets.get(plan.post.id) if ledger else None
    if asset is None:
        return False
    try:
        return (
            jpeg_hash == plan.asset_sha256 == asset.jpeg_sha256
            and hashlib.sha256(_jpeg(repository, config, plan.post.id)).hexdigest()
            == jpeg_hash
            and hashlib.sha256(png).hexdigest() == asset.png_sha256
            and _png_hash(config.assets_path / f"{plan.post.id}.png")
            == asset.png_sha256
        )
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    try:
        verify_editorial_fonts()
        print(json.dumps({"approved_editorial_fonts": True}))
    except Exception:
        sys.exit("approved_editorial_fonts_unavailable")
