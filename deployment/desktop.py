"""Prepare a disabled local deployment outside Git; never access credentials."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.deployment import export_bundle, restore_bundle

ROOT = Path(__file__).resolve().parents[1]


def prepare(source: Path, data: Path, target: Path, image: str) -> None:
    target = target.resolve()
    if target.exists() or any((p / ".git").exists() for p in (target, *target.parents)):
        raise ValueError("new_destination_outside_git_required")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("immutable_image_required")
    subprocess.run(
        ["docker", "image", "inspect", image], check=True, capture_output=True
    )
    target.mkdir(mode=0o700)
    config = load_config(source)
    export_bundle(config, data, target / "source-bundle")
    restore_bundle(target / "source-bundle", target / "state")
    for name in ("config", "credentials"):
        (target / name).mkdir(mode=0o700)
    text = (
        (target / "state/aed.toml")
        .read_text()
        .replace('"aed.sqlite3"', '"/var/lib/aed/aed.sqlite3"')
        .replace('"assets"', '"/var/lib/aed/assets"')
    )
    (target / "config/aed.toml").write_text(text)
    (target / "config/schedule.toml").write_text(
        'timezone="Europe/London"\nhour=19\nminute=0\nwindow_minutes=30\nauthorization_days=30\n'
    )
    compose = (
        (ROOT / "container/compose.yaml")
        .read_text()
        .replace("ask-every-day:local", image)
        .replace(
            "command: [preview]", "command: [preview, --data-dir, /var/lib/aed/data]"
        )
    )
    (target / "compose.yaml").write_text(compose)
    (target / "image-id").write_text(image + "\n")
    for directory in (target, target / "config"):
        for path in directory.iterdir():
            if path.is_file():
                os.chmod(path, 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        prepare(args.source_config, args.data_dir, args.directory, args.image)
        print(
            json.dumps(
                {
                    "local_deployment_prepared": True,
                    "authorization": "disabled",
                    "credentials": "absent",
                }
            )
        )
    except Exception:
        parser.exit(
            1,
            "Local preparation failed; inspect the private candidate; source state remains authoritative.\n",
        )


if __name__ == "__main__":
    main()
