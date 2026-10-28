"""Offline, coordinated deployment bundles and daily publication observation."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.domain import AppConfig, PublicationStatus
from ask_every_day.publication_assets import read_ledger
from ask_every_day.publication_recovery import backup_state, snapshot
from ask_every_day.publish_next import CopyCatalog, preview_next
from ask_every_day.publish_next_cli import publication_lock
from ask_every_day.repository import open_repository
from ask_every_day.scheduler_policy import LONDON, digest, load_schedule

DATA_FILES = (
    "prompts.csv",
    "taxonomy.csv",
    "hashtags.csv",
    "captions.csv",
    "caption_profiles.csv",
)


class DeploymentError(ValueError):
    """Fixed diagnostics; never include file contents or underlying exception text."""


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def files(root: Path) -> dict[str, str]:
    result = {}
    if root.is_symlink() or not root.is_dir():
        raise DeploymentError("invalid_directory")
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise DeploymentError("non_regular_file")
        result[path.relative_to(root).as_posix()] = file_hash(path)
    return result


def describe(config: AppConfig, data: Path) -> dict:
    """Read a validated snapshot; caller coordinates writers for migration."""
    with closing(
        sqlite3.connect(
            config.database_path.resolve(strict=True).as_uri() + "?mode=ro", uri=True
        )
    ) as sql:
        if (
            sql.execute("PRAGMA user_version").fetchone() != (3,)
            or sql.execute("PRAGMA integrity_check").fetchone() != ("ok",)
            or sql.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise DeploymentError("database_integrity")
    with open_repository(config, read_only=True) as repo:
        attempts = [snapshot(repo, row.id) for row in repo.list_publications()]
        if any(item.report()["operator_action_required"] for item in attempts):
            raise DeploymentError("unresolved_publication")
        plan = preview_next(repo, config, CopyCatalog.load(data))
        ledger = read_ledger(config)
        if ledger is not None:
            for post_id, asset in ledger.assets.items():
                for suffix, expected in (
                    ("jpg", asset.jpeg_sha256),
                    ("png", asset.png_sha256),
                ):
                    path = config.assets_path / f"{post_id}.{suffix}"
                    if path.is_symlink() or file_hash(path) != expected:
                        raise DeploymentError("canonical_asset_changed")
        return {
            "schema": 3,
            "posts_sha256": digest([asdict(p) for p in repo.list_posts()]),
            "history": [
                {"fingerprint": item.fingerprint, **item.report()} for item in attempts
            ],
            "queue": [asdict(q) for q in repo.list_queue()],
            "next": None
            if plan is None
            else {
                "post_id": plan.post.id,
                "position": plan.entry.position,
                "background": plan.background_color,
                "sequence": plan.palette_sequence,
                "jpeg_sha256": plan.asset_sha256,
            },
            "palette": asdict(config.palette),
            "renderer": asdict(config.renderer),
            "account": config.account_handle,
            "assets": files(config.assets_path),
            "data": {name: file_hash(data / name) for name in DATA_FILES},
        }


def config_text(config: AppConfig) -> str:
    palette = config.palette
    return (
        'database_path = "aed.sqlite3"\nassets_path = "assets"\n'
        f"account_handle = {json.dumps(config.account_handle)}\n\n"
        "[renderer]\n"
        f"template = {json.dumps(config.renderer.template)}\n"
        f"width = {config.renderer.width}\nheight = {config.renderer.height}\n\n"
        "[palette]\n"
        f"backgrounds = {json.dumps(palette.backgrounds)}\n"
        f"text_color = {json.dumps(palette.text_color)}\n"
        f"accent_color = {json.dumps(palette.accent_color)}\nseed = {palette.seed}\n"
    )


def private_tree(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())


def export_bundle(config: AppConfig, data: Path, target: Path) -> Path:
    """Stopped writers + common lock + SQLite backup; never copy live DB bytes."""
    if target.exists() or target.is_symlink():
        raise DeploymentError("destination_exists")
    for source in (config.assets_path, data):
        if target.resolve().is_relative_to(source.resolve()):
            raise DeploymentError("destination_inside_source")
    with publication_lock(config.database_path):
        before = describe(config, data)
        temporary = Path(tempfile.mkdtemp(prefix=".aed-transfer-", dir=target.parent))
        try:
            saved = backup_state(config, temporary)
            (saved / "state.sqlite3").rename(temporary / "aed.sqlite3")
            palette = saved / "state.sqlite3.palette.json"
            if palette.exists():
                palette.rename(temporary / "aed.sqlite3.palette.json")
            shutil.rmtree(saved)
            shutil.copytree(config.assets_path, temporary / "assets")
            (temporary / "data").mkdir()
            for name in DATA_FILES:
                source = data / name
                if source.is_symlink() or not source.is_file():
                    raise DeploymentError("invalid_data")
                shutil.copyfile(source, temporary / "data" / name)
            (temporary / "aed.toml").write_text(config_text(config))
            # Retain recovery evidence separately; never install active authorization.
            evidence = temporary / "evidence"
            evidence.mkdir()
            state = config.database_path.resolve()
            candidates = [state.with_name(state.name + ".schedule.json")]
            candidates += list(state.parent.glob("scheduler.jsonl*"))
            for source in candidates:
                if source.exists() or source.is_symlink():
                    if source.is_symlink() or not source.is_file():
                        raise DeploymentError("invalid_evidence")
                    shutil.copyfile(source, evidence / source.name)
            after = describe(config, data)
            copied = describe(load_config(temporary / "aed.toml"), temporary / "data")
            if digest(before) != digest(after) or digest(before) != digest(copied):
                raise DeploymentError("source_changed")
            manifest = {
                "version": 1,
                "created_at": datetime.now(UTC).isoformat(),
                "state": before,
                "files": files(temporary),
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n"
            )
            verify_bundle(temporary)
            private_tree(temporary)
            # Target must remain absent; operator owns/quiesces its trusted parent.
            if target.exists():
                raise DeploymentError("destination_exists")
            temporary.rename(target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return target


def verify_bundle(bundle: Path) -> dict:
    inventory = files(bundle)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    if manifest.get("version") != 1:
        raise DeploymentError("bundle_version")
    inventory.pop("manifest.json")
    if inventory != manifest["files"]:
        raise DeploymentError("bundle_checksum")
    allowed = {"aed.toml", "aed.sqlite3", "aed.sqlite3.palette.json"}
    for name in inventory:
        if name not in allowed and not re.fullmatch(
            r"(?:assets/AED-[0-9]{4}\.(?:jpg|png)|data/[a-z_]+\.csv|"
            r"evidence/(?:[^/]+\.schedule\.json|scheduler\.jsonl(?:\.[0-9]+)?))",
            name,
        ):
            raise DeploymentError("unexpected_bundle_file")
    config = load_config(bundle / "aed.toml")
    if (
        config.database_path != bundle.resolve() / "aed.sqlite3"
        or config.assets_path != bundle.resolve() / "assets"
    ):
        raise DeploymentError("bundle_paths")
    actual = describe(config, bundle / "data")
    if digest(actual) != digest(manifest["state"]):
        raise DeploymentError("bundle_state")
    return manifest


def restore_bundle(bundle: Path, target: Path) -> None:
    verify_bundle(bundle)
    if target.exists() or target.is_symlink():
        raise DeploymentError("destination_exists")
    temporary = Path(tempfile.mkdtemp(prefix=".aed-restore-", dir=target.parent))
    try:
        shutil.copytree(bundle, temporary, dirs_exist_ok=True)
        private_tree(temporary)
        verify_bundle(temporary)
        if target.exists():
            raise DeploymentError("destination_exists")
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def daily_report(config: AppConfig, now: datetime, window_end: int) -> dict:
    local = now.astimezone(LONDON)
    with open_repository(config, read_only=True) as repo:
        attempts = [snapshot(repo, row.id) for row in repo.list_publications()]
    unresolved = any(item.report()["operator_action_required"] for item in attempts)
    successes = [
        item
        for item in attempts
        if item.record.publication.status is PublicationStatus.PUBLISHED
        and item.record.publication.published_at is not None
        and item.record.publication.published_at.astimezone(LONDON).date()
        == local.date()
    ]
    complete = [
        item
        for item in successes
        if item.checkpoint is not None
        and item.checkpoint.phase.value == "completed"
        and item.record.publication.external_id
    ]
    due = local.hour * 60 + local.minute >= window_end
    healthy = not unresolved and len(successes) <= 1 and (not due or len(complete) == 1)
    return {
        "event": "daily_publication_check",
        "local_day": local.date().isoformat(),
        "healthy": healthy,
        "window_ended": due,
        "published_count": len(successes),
        "completed_count": len(complete),
        "unresolved": unresolved,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("export", "verify", "restore", "inspect", "monitor")
    )
    parser.add_argument("--config", type=Path, default=Path("aed.toml"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--schedule-config", type=Path, default=Path("schedule.toml"))
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            if args.target is None:
                parser.error("--target required")
            export_bundle(load_config(args.config), args.data_dir, args.target)
            output = {"event": "deployment_bundle_created"}
        elif args.command in {"verify", "restore"}:
            if args.bundle is None:
                parser.error("--bundle required")
            manifest = verify_bundle(args.bundle)
            if args.command == "restore":
                if args.target is None:
                    parser.error("--target required")
                restore_bundle(args.bundle, args.target)
            output = {"event": "deployment_bundle_verified", "state": manifest["state"]}
        elif args.command == "monitor":
            schedule = load_schedule(args.schedule_config)
            output = daily_report(
                load_config(args.config),
                datetime.now(UTC),
                schedule.hour * 60 + schedule.minute + schedule.window_minutes,
            )
            print(json.dumps(output))
            raise SystemExit(0 if output["healthy"] else 1)
        else:
            output = describe(load_config(args.config), args.data_dir)
        print(json.dumps(output))
    except (OSError, ValueError, KeyError, TypeError):
        print(
            json.dumps(
                {"event": "deployment_operation_failed", "operation": args.command}
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
