"""Synthetic migration, checkpoint continuity and offline daily monitoring."""

import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from ask_every_day.config import load_config
from ask_every_day.content_import import load_snapshot
from ask_every_day.deployment import (
    DeploymentError,
    daily_report,
    describe,
    export_bundle,
    file_hash,
    main,
    restore_bundle,
    verify_bundle,
)
from ask_every_day.domain import AppConfig, QueueEntry
from ask_every_day.publication_assets import (
    prepare_next_asset,
    validate_palette_success,
)
from ask_every_day.publication_checkpoints import CheckpointService
from ask_every_day.publication_service import PublicationService
from ask_every_day.publish_next import CopyCatalog, preview_next
from ask_every_day.recovery_domain import PublicationContext
from ask_every_day.repository import open_repository
from ask_every_day.scheduler_policy import digest

DATA = Path(__file__).resolve().parents[1] / "data"
NOW = datetime(2026, 10, 27, 21, tzinfo=UTC)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        socket,
        "create_connection",
        Mock(side_effect=AssertionError("network forbidden")),
    )


@pytest.fixture
def state(tmp_path):
    config = AppConfig(
        tmp_path / "source.sqlite3", tmp_path / "assets", "@ask.every.day"
    )
    config.assets_path.mkdir()
    posts = load_snapshot(
        DATA / "prompts.csv", DATA / "taxonomy.csv", DATA / "hashtags.csv"
    )
    chosen = [
        next(p for p in posts if p.id == key)
        for key in ("AED-0395", "AED-0289", "AED-0236")
    ]
    with open_repository(config) as repo:
        for post in chosen:
            repo.create_post(post)
        repo.replace_queue([QueueEntry(p.id, i + 1) for i, p in enumerate(chosen)])
        for i in range(2):
            prepare_next_asset(repo, config)
            plan = preview_next(repo, config, CopyCatalog.load(DATA))
            assert plan is not None and plan.asset_sha256 is not None
            at = NOW - timedelta(days=1 - i)
            service = PublicationService(repo)
            attempt = service.begin_publish(
                plan.post.id,
                at,
                context=PublicationContext("1001", "ask.every.day", plan.asset_sha256),
            )
            checkpoints = CheckpointService(repo)
            checkpoints.create_intent(attempt.id, at)
            checkpoints.container_known(attempt.id, str(2001 + i), at)
            checkpoints.publish_intent(attempt.id, at)
            service.record_success(
                attempt.id,
                at,
                str(3001 + i),
                validate_result=lambda plan=plan: validate_palette_success(
                    repo, config, plan.post.id, plan.asset_sha256
                ),
            )
        prepare_next_asset(repo, config)
    return config


def test_coordinated_bundle_restore_preserves_history_queue_assets_and_no_active_auth(
    state, tmp_path
):
    original = describe(state, DATA)
    state.database_path.with_suffix(".sqlite3.schedule.json").write_text(
        '{"enabled": true}'
    )
    bundle, restored = tmp_path / "bundle", tmp_path / "restored"
    before = file_hash(state.database_path)
    export_bundle(state, DATA, bundle)
    assert file_hash(state.database_path) == before
    manifest = verify_bundle(bundle)
    assert digest(manifest["state"]) == digest(original)
    restore_bundle(bundle, restored)
    assert not (restored / "aed.sqlite3.schedule.json").exists()
    assert (restored / "evidence/source.sqlite3.schedule.json").exists()
    after = describe(load_config(restored / "aed.toml"), restored / "data")
    assert digest(after) == digest(original)
    assert [(a["post_id"], a["status"], a["checkpoint"]) for a in after["history"]] == [
        ("AED-0395", "Published", "completed"),
        ("AED-0289", "Published", "completed"),
    ]
    assert after["next"]["sequence"] == 3
    assert after["next"]["post_id"] == "AED-0236"
    assert all(
        p.stat().st_mode & 0o777 == (0o700 if p.is_dir() else 0o600)
        for p in [restored, *restored.rglob("*")]
    )


@pytest.mark.parametrize(
    "filename",
    [
        "aed.sqlite3",
        "aed.sqlite3.palette.json",
        "assets/AED-0236.jpg",
        "data/prompts.csv",
    ],
)
def test_corrupt_bundle_refuses_restore(state, tmp_path, filename):
    bundle = export_bundle(state, DATA, tmp_path / "bundle")
    with (bundle / filename).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(DeploymentError, match="bundle_checksum"):
        restore_bundle(bundle, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_existing_destination_is_never_overwritten(state, tmp_path):
    bundle = export_bundle(state, DATA, tmp_path / "bundle")
    with pytest.raises(DeploymentError, match="destination_exists"):
        export_bundle(state, DATA, bundle)
    with pytest.raises(DeploymentError, match="destination_exists"):
        restore_bundle(bundle, bundle)


def test_unresolved_checkpoint_blocks_migration_and_monitor(state, tmp_path):
    with open_repository(state) as repo:
        plan = preview_next(repo, state, CopyCatalog.load(DATA))
        assert plan is not None and plan.asset_sha256 is not None
        PublicationService(repo).begin_publish(
            plan.post.id,
            NOW + timedelta(days=1),
            context=PublicationContext("1001", "ask.every.day", plan.asset_sha256),
        )
    with pytest.raises(DeploymentError, match="unresolved_publication"):
        export_bundle(state, DATA, tmp_path / "bundle")
    assert not daily_report(state, NOW + timedelta(days=1, hours=1), 1290)["healthy"]


def test_next_asset_tampering_blocks_export(state, tmp_path):
    (state.assets_path / "AED-0236.jpg").write_bytes(b"tampered")
    with pytest.raises(DeploymentError, match="canonical_asset_changed"):
        export_bundle(state, DATA, tmp_path / "bundle")


def test_symlink_bundle_and_assets_refused(state, tmp_path):
    link = state.assets_path / "AED-9999.jpg"
    link.symlink_to(state.assets_path / "AED-0236.jpg")
    with pytest.raises(DeploymentError, match="non_regular_file"):
        export_bundle(state, DATA, tmp_path / "bundle")


def test_source_changed_while_copying_is_detected(state, tmp_path, monkeypatch):
    from ask_every_day import deployment

    original = deployment.backup_state

    def changing(config, target):
        saved = original(config, target)
        with open_repository(config) as repo:
            repo.replace_queue([QueueEntry("AED-0236", 99)])
        return saved

    monkeypatch.setattr(deployment, "backup_state", changing)
    with pytest.raises(DeploymentError, match="source_changed"):
        export_bundle(state, DATA, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
    assert not list(tmp_path.glob(".aed-transfer-*"))


def test_monitor_reports_completed_day_and_missing_next_day(state):
    assert daily_report(state, NOW + timedelta(minutes=35), 1290)["healthy"]
    later = daily_report(state, NOW + timedelta(days=1, minutes=35), 1290)
    assert not later["healthy"] and later["window_ended"]
    assert daily_report(state, NOW + timedelta(days=1, minutes=10), 1290)["healthy"]


@pytest.mark.parametrize(
    "stamp", ["2026-06-01T20:35:00+00:00", "2026-12-01T21:35:00+00:00"]
)
def test_monitor_uses_london_end_in_both_seasons(state, stamp):
    assert daily_report(state, datetime.fromisoformat(stamp), 1290)["window_ended"]


def test_cli_failure_redacts_private_path_and_contents(tmp_path, capsys):
    with pytest.raises(SystemExit) as caught:
        main(["verify", "--bundle", str(tmp_path / "PRIVATE_SECRET")])
    assert caught.value.code == 1
    assert "PRIVATE_SECRET" not in capsys.readouterr().out


def test_export_refuses_destination_inside_assets(state):
    with pytest.raises(DeploymentError, match="destination_inside_source"):
        export_bundle(state, DATA, state.assets_path / "bundle")


def test_unexpected_asset_file_cannot_create_unrestorable_bundle(state, tmp_path):
    (state.assets_path / "unexpected.txt").write_text("not an asset")
    with pytest.raises(DeploymentError, match="unexpected_bundle_file"):
        export_bundle(state, DATA, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
