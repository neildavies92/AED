"""Network-free disposable-volume acceptance; all remote IDs/credentials synthetic."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from ask_every_day.config import load_config
from ask_every_day.content_import import load_snapshot
from ask_every_day.deployment import export_bundle, restore_bundle, verify_bundle
from ask_every_day.domain import AppConfig, QueueEntry
from ask_every_day.file_credential_store import FileCredentialStore
from ask_every_day.instagram_auth import (
    REDIRECT_URI,
    SCOPES,
    AuthConfig,
    CredentialManager,
    InstagramAuthClient,
    InstagramCredential,
)
from ask_every_day.instagram_client import InstagramIdentity
from ask_every_day.jpeg_export import export_jpeg, validate_jpeg
from ask_every_day.notifications import Notice, NotificationStore, send_notice
from ask_every_day.png_export import export_png
from ask_every_day.publication_assets import (
    ledger_path,
    prepare_next_asset,
    validate_palette_success,
)
from ask_every_day.publication_checkpoints import CheckpointService
from ask_every_day.publication_recovery import backup_state, snapshot
from ask_every_day.publication_service import PublicationService
from ask_every_day.publish_next import CopyCatalog, preview_next
from ask_every_day.publish_next_cli import publication_lock
from ask_every_day.recovery_domain import PublicationContext
from ask_every_day.renderer import render_svg
from ask_every_day.repository import open_repository

ROOT = Path("/var/lib/aed")
NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
CONFIG = AppConfig(ROOT / "aed.sqlite3", ROOT / "assets", "@ask.every.day")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def initialize():
    assert os.geteuid() == 10001 and sys.version_info[:2] == (3, 13)
    CONFIG.assets_path.mkdir()
    private = ROOT / "credentials"
    private.mkdir(mode=0o700)
    source = Path("/opt/aed/data")
    posts = load_snapshot(
        source / "prompts.csv", source / "taxonomy.csv", source / "hashtags.csv"
    )
    chosen = [next(p for p in posts if p.id == key) for key in ("AED-0395", "AED-0236")]
    (ROOT / "aed.toml").write_text(
        'database_path="aed.sqlite3"\nassets_path="assets"\naccount_handle="@ask.every.day"\n'
    )
    assert load_config(ROOT / "aed.toml") == CONFIG
    with open_repository(CONFIG) as repo:
        for post in chosen:
            repo.create_post(post)
        repo.replace_queue([QueueEntry(p.id, i + 1) for i, p in enumerate(chosen)])
        prepared = prepare_next_asset(repo, CONFIG)
        assert prepared is not None
        first = preview_next(repo, CONFIG, CopyCatalog.load(source))
        assert first is not None and first.asset_sha256 is not None
        before_image = first.asset_sha256
        # Synthetic successful result exercises persistence, not any transport/API.
        service = PublicationService(repo)
        attempt = service.begin_publish(
            first.post.id,
            NOW,
            context=PublicationContext("1001", "ask.every.day", first.asset_sha256),
        )
        steps = CheckpointService(repo)
        steps.create_intent(attempt.id, NOW)
        steps.container_known(attempt.id, "2001", NOW)
        steps.publish_intent(attempt.id, NOW)
        service.record_success(
            attempt.id,
            NOW,
            "3001",
            validate_result=lambda: validate_palette_success(
                repo, CONFIG, first.post.id, first.asset_sha256
            ),
        )
        assert digest(first.asset_path) == before_image
        prepare_next_asset(repo, CONFIG)
        second = preview_next(repo, CONFIG, CopyCatalog.load(source))
        assert second is not None and second.palette_sequence == 2
        assert second.background_color != first.background_color
    store = FileCredentialStore("1001", private)
    store.save(
        InstagramCredential(
            "SYNTHETIC_CONTAINER_TEST_ONLY",
            "1001",
            "1002",
            "1003",
            "ask.every.day",
            SCOPES,
            NOW,
            NOW + timedelta(days=60),
        )
    )
    # Real manager refresh against a fake provider; next container reads the result.
    provider = Mock(spec=InstagramAuthClient)
    provider.config = AuthConfig("1001", REDIRECT_URI)
    from ask_every_day.instagram_auth import _LongLivedToken

    provider.refresh_token.return_value = _LongLivedToken(
        "SYNTHETIC_REFRESHED_ONLY", 5184000
    )
    manager = CredentialManager(
        provider,
        store,
        clock=lambda: NOW + timedelta(days=55),
        identify=lambda _: InstagramIdentity("1003", "ask.every.day", "BUSINESS"),
    )
    assert manager.get_valid_credential().access_token == "SYNTHETIC_REFRESHED_ONLY"
    notifications = NotificationStore(private)
    notifications.configure(
        "https://discord.com/api/webhooks/123/" + "SYNTHETIC_DISCORD_" * 3
    )
    notice = Notice("notification_test", NOW, "a" * 32, dedup_key="smoke-notification")
    assert (
        send_notice(
            notifications,
            notice,
            sender=lambda *_: {"delivery_result": "delivered", "http_status": 204},
        )["delivery_result"]
        == "delivered"
    )
    export_bundle(CONFIG, source, ROOT / "deployment-bundle")
    verify_bundle(ROOT / "deployment-bundle")
    with publication_lock(CONFIG.database_path):
        backup = backup_state(CONFIG, ROOT / "backups")
        shutil.copytree(CONFIG.assets_path, backup / "assets")
    raw = export_jpeg(
        export_png(render_svg(chosen[0], CONFIG.renderer), CONFIG.renderer)
    )
    validate_jpeg(raw)
    (ROOT / "repeat.jpg").write_bytes(raw)
    report = {
        "files": {p.name: digest(p) for p in CONFIG.assets_path.iterdir()},
        "palette_sha256": digest(ledger_path(CONFIG)),
        "backup": str(backup),
        "next_post": second.post.id,
        "next_digest": second.asset_sha256,
    }
    (ROOT / "smoke.json").write_text(json.dumps(report))
    print(
        json.dumps(
            {
                "initialized": True,
                "synthetic_success": True,
                "palette_sequence": 2,
                "non_root": True,
            }
        )
    )


def schedule_protocol():
    """Real subprocess protocol/lock/approval persistence; no publishing engine."""
    from ask_every_day.scheduler_policy import (
        ScheduleConfig,
        authorization_preview,
        load_inputs,
        load_schedule,
        read_authorization,
        save_authorization,
        validate_authorization,
    )

    directory = ROOT / "schedule-smoke"
    directory.mkdir(mode=0o700)
    (directory / "assets").mkdir()
    config_path = directory / "aed.toml"
    config_path.write_text(
        'database_path="aed.sqlite3"\nassets_path="assets"\naccount_handle="@ask.every.day"\n'
    )
    config = load_config(config_path)
    source = Path("/opt/aed/data")
    inputs = load_inputs(source)
    schedule_path = directory / "schedule.toml"
    schedule_path.write_text("hour=19\nminute=0\n")
    now = datetime.now(UTC)
    with open_repository(config) as repo:
        for post in inputs.posts:
            repo.create_post(post)
        repo.replace_queue([QueueEntry(inputs.posts[0].id, 1)])
        original = authorization_preview(
            repo, config, inputs, ScheduleConfig(hour=19), now
        )
        save_authorization(config, original)
    before = digest(config.database_path)
    day = (now + timedelta(days=1)).date().isoformat()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ask_every_day.schedule_change",
            "--config",
            str(config_path),
            "--schedule-config",
            str(schedule_path),
            "--data-dir",
            str(source),
            "--hour",
            "20",
            "--minute",
            "0",
            "--effective-day",
            day,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    ready = json.loads(process.stdout.readline())
    assert ready["phase"] == "ready" and ready["authorization_preserved"]
    schedule_path.write_text(f'hour=20\nminute=0\neffective_from="{day}"\n')
    process.stdin.write("commit\n")
    process.stdin.flush()
    committed = json.loads(process.stdout.readline())
    process.stdin.close()
    process.stdout.close()
    assert committed["phase"] == "committed" and process.wait(timeout=30) == 0
    with open_repository(config, read_only=True) as repo:
        current = read_authorization(config)
        assert current is not None and current.expires_at == original.expires_at
        validate_authorization(
            repo, config, inputs, load_schedule(schedule_path), current, now
        )
        assert len(repo.list_queue()) == 1 and not repo.list_publications()
    assert digest(config.database_path) == before
    # A test approval must not authorize subsequent general smoke commands.
    (directory / "aed.sqlite3.schedule.json").unlink()
    print(
        json.dumps(
            {"schedule_change_protocol_verified": True, "sqlite_unchanged": True}
        )
    )


def verify():
    schedule_protocol()
    report = json.loads((ROOT / "smoke.json").read_text())
    assert digest(ledger_path(CONFIG)) == report["palette_sha256"]
    assert {p.name: digest(p) for p in CONFIG.assets_path.iterdir()} == report["files"]
    source = Path("/opt/aed/data")
    with open_repository(CONFIG, read_only=True) as repo:
        current = snapshot(repo, 1)
        assert (
            current.report()["definitely_completed"] and current.checkpoint is not None
        )
        assert (
            current.checkpoint.container_id == "2001"
            and current.record.publication.external_id == "3001"
        )
        plan = preview_next(repo, CONFIG, CopyCatalog.load(source))
        assert (
            plan is not None
            and plan.post.id == report["next_post"]
            and plan.asset_sha256 == report["next_digest"]
        )
        raw = export_jpeg(
            export_png(
                render_svg(repo.get_post("AED-0395"), CONFIG.renderer), CONFIG.renderer
            )
        )
        assert raw == (ROOT / "repeat.jpg").read_bytes()
    credential = FileCredentialStore("1001", ROOT / "credentials").load()
    assert (
        credential is not None and credential.access_token == "SYNTHETIC_REFRESHED_ONLY"
    )
    notifications = NotificationStore(ROOT / "credentials")
    sender = Mock(side_effect=AssertionError("duplicate send forbidden"))
    notice = Notice("notification_test", NOW, "b" * 32, dedup_key="smoke-notification")
    assert (
        send_notice(notifications, notice, sender=sender)["delivery_result"]
        == "duplicate_suppressed"
    )
    sender.assert_not_called()
    restore_bundle(ROOT / "deployment-bundle", ROOT / "deployment-restored")
    assert not (ROOT / "deployment-restored/aed.sqlite3.schedule.json").exists()
    verified = verify_bundle(ROOT / "deployment-restored")
    assert verified["state"]["history"][0]["checkpoint"] == "completed"
    restored = ROOT / "restored"
    restored.mkdir()
    backup = Path(report["backup"])
    manifest = json.loads((backup / "manifest.json").read_text())
    assert all(
        digest(backup / name) == value for name, value in manifest["files"].items()
    )
    shutil.copyfile(backup / "state.sqlite3", restored / "aed.sqlite3")
    shutil.copyfile(
        backup / "state.sqlite3.palette.json", restored / "aed.sqlite3.palette.json"
    )
    shutil.copytree(backup / "assets", restored / "assets")
    restored_config = AppConfig(
        restored / "aed.sqlite3", restored / "assets", "@ask.every.day"
    )
    with open_repository(restored_config, read_only=True) as repo:
        assert snapshot(repo, 1).report()["definitely_completed"]
        plan = preview_next(repo, restored_config, CopyCatalog.load(source))
        assert plan is not None and plan.asset_sha256 == report["next_digest"]
    with sqlite3.connect(
        CONFIG.database_path.as_uri() + "?mode=ro", uri=True
    ) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
    print(
        json.dumps(
            {
                "recreated_container": True,
                "state_and_palette_persisted": True,
                "canonical_assets_unchanged": True,
                "repeat_render_equal": True,
                "private_credentials_persisted": True,
                "coordinated_restore_valid": True,
                "deployment_bundle_restored": True,
                "refreshed_credential_persisted": True,
                "notification_receipt_persisted": True,
                "duplicate_notification_suppressed": True,
            }
        )
    )


if sys.argv[1] == "initialize":
    initialize()
else:
    verify()
