"""Nonpublishing Docker Desktop storage and synthetic networking acceptance."""

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path

from PIL import Image

from ask_every_day.domain import AppConfig, Post
from ask_every_day.file_credential_store import FileSecretBackend
from ask_every_day.logging_config import configure_logging
from ask_every_day.media_staging import stage_media
from ask_every_day.repository import open_repository


def storage_check(state: Path, credentials: Path) -> dict:
    if os.geteuid() != 10001 or os.getegid() != 10001:
        raise ValueError("runtime_identity")
    for directory in (state, credentials):
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 10001
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("private_mount_required")
    # Only a dedicated synthetic namespace is touched; real credentials are never read.
    backend = FileSecretBackend(credentials)
    backend.write(
        "aed057-private-mount-probe", "credential", b"SYNTHETIC_STORAGE_PROBE"
    )
    try:
        assert (
            backend.read("aed057-private-mount-probe", "credential")
            == b"SYNTHETIC_STORAGE_PROBE"
        )
    finally:
        backend.delete("aed057-private-mount-probe", "credential")
    with tempfile.TemporaryDirectory(
        prefix=".aed-desktop-probe-", dir=state
    ) as temporary:
        first = Path(temporary) / "first"
        first.write_bytes(b"SYNTHETIC_STATE_PROBE")
        first.replace(Path(temporary) / "second")
        if (Path(temporary) / "second").read_bytes() != b"SYNTHETIC_STATE_PROBE":
            raise ValueError("state_roundtrip")
    return {"uid": 10001, "private_mounts_valid": True, "atomic_write_valid": True}


def synthetic_staging() -> dict:
    # No production mounts, editorial input, credential manager or publishing client.
    with tempfile.TemporaryDirectory(prefix="aed-synthetic-stage-") as temporary:
        root = Path(temporary)
        config = AppConfig(
            root / "synthetic.sqlite3", root / "assets", "@ask.every.day"
        )
        config.assets_path.mkdir()
        with Image.new("RGB", (1080, 1350), "#E2EFF9") as image:
            image.save(config.assets_path / "AED-9999.jpg", format="JPEG")
        with open_repository(config) as repository:
            repository.create_post(
                Post(
                    "AED-9999",
                    "Synthetic staging acceptance",
                    "Test",
                    "Test",
                    "Classic",
                )
            )
            with stage_media(repository, config, "AED-9999"):
                pass
    return {
        "synthetic_https_verified": True,
        "tunnel_closed": True,
        "instagram_requests": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("storage", "staging"))
    parser.add_argument("--state", type=Path, default=Path("/var/lib/aed"))
    parser.add_argument(
        "--credentials", type=Path, default=Path("/var/lib/aed-credentials")
    )
    args = parser.parse_args()
    configure_logging()
    try:
        output = (
            storage_check(args.state, args.credentials)
            if args.command == "storage"
            else synthetic_staging()
        )
        print(json.dumps(output))
    except Exception:
        parser.exit(1, "Local Docker validation failed; no publication attempted.\n")


if __name__ == "__main__":
    main()
