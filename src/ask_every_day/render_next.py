"""Prepare one canonical themed asset, without credentials, staging or publishing."""

import argparse
import json
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.logging_config import configure_logging
from ask_every_day.publication_assets import AssetError, prepare_next_asset
from ask_every_day.publish_next_cli import publication_lock
from ask_every_day.repository import open_repository


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare the next queued Post's themed image"
    )
    parser.add_argument("--config", type=Path, default=Path("aed.toml"))
    parser.add_argument(
        "--previous-background",
        help="Explicit one-time bootstrap from the verified last historical publication",
    )
    args = parser.parse_args()
    configure_logging()
    failure = None
    try:
        config = load_config(args.config)
        with (
            open_repository(config) as repository,
            publication_lock(config.database_path),
        ):
            asset = prepare_next_asset(
                repository, config, previous_background=args.previous_background
            )
            entry = repository.next_queue_entry()
            print(
                json.dumps(
                    {"queue": "empty"}
                    if asset is None
                    else {
                        "post_id": entry.post_id if entry else None,
                        "background_color": asset.assignment.background,
                        "palette_sequence": asset.assignment.sequence,
                        "asset_sha256": asset.jpeg_sha256,
                        "asset_path": str(config.assets_path / f"{entry.post_id}.jpg")
                        if entry
                        else None,
                    }
                )
            )
    except Exception as error:
        failure = str(error) if isinstance(error, AssetError) else "render_next_failed"
    except KeyboardInterrupt:
        failure = "render_next_cancelled"
    if failure:
        parser.exit(1, failure + "; see docs/background-rotation.md.\n")


if __name__ == "__main__":
    main()
