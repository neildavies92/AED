"""Recovery commands default to offline/read-only and never use real credentials."""

import json
import sqlite3
import stat
import sys
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from ask_every_day import publication_recovery_cli as cli
from ask_every_day.instagram_client import ContainerStatus, MediaDetails
from ask_every_day.publication_assets import ledger_path
from ask_every_day.publication_recovery import (
    RecoveryError,
    apply_recovery,
    backup_state,
    remote_inspection,
    snapshot,
)
from ask_every_day.recovery_domain import CheckpointPhase
from tests.test_publication_checkpoints import pending
from tests.test_publish_next import NOW, PRIVATE
from tests.test_publish_next import run as run
from tests.test_recovery_migration import legacy


@pytest.fixture
def command(run, monkeypatch):
    monkeypatch.setattr(cli, "load_config", Mock(return_value=run.config))
    monkeypatch.setattr(cli, "configure_logging", Mock())
    monkeypatch.setattr(cli, "credential_manager", Mock(return_value=run.credentials))
    monkeypatch.setattr(cli, "InstagramClient", Mock(return_value=run.client))
    # Inject deterministic clocks without weakening the actual CLI/read/mutation path.
    monkeypatch.setattr(
        cli,
        "remote_inspection",
        lambda *a, **kw: remote_inspection(*a, **kw, clock=lambda: NOW),
    )
    monkeypatch.setattr(
        cli,
        "apply_recovery",
        lambda *a, **kw: apply_recovery(*a, **kw, clock=lambda: NOW),
    )
    run.client.container_status.return_value = ContainerStatus.PUBLISHED
    run.client.media_details.return_value = MediaDetails("105", "103", "IMAGE", NOW)

    def execute(*args, tty=False, answer=None):
        monkeypatch.setattr(sys, "argv", ["recovery", *args])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
        if answer is not None:
            monkeypatch.setattr("builtins.input", answer)
        cli.main()

    return execute


def evidence_file(tmp_path):
    path = tmp_path / "response.json"
    path.write_text(
        json.dumps(
            {
                "attempt_id": 1,
                "account_id": "103",
                "container_id": "104",
                "media_id": "105",
                "source": "captured_publish_response",
            }
        )
    )
    return path


def test_default_status_is_read_only_offline_and_redacted(run, command, capsys):
    pending(run)
    before = run.config.database_path.read_bytes()
    command("status")
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert data["mode"] == "local_read_only"
    assert data["attempts"][0]["checkpoint"] == "publish_intent"
    assert (
        data["attempts"][0]["container_known"]
        and data["attempts"][0]["operator_action_required"]
    )
    assert (
        "103" not in output.out
        and "104" not in output.out
        and PRIVATE not in output.out
    )
    assert run.config.database_path.read_bytes() == before
    cast(Mock, cli.credential_manager).assert_not_called()
    cast(Mock, cli.InstagramClient).assert_not_called()


def test_default_reconciliation_also_has_no_provider_calls_or_mutations(
    run, command, capsys
):
    current = pending(run)
    command("reconcile", "--attempt-id", "1")
    assert json.loads(capsys.readouterr().out)["mode"] == "local_read_only"
    assert snapshot(run.repo, 1) == current
    cast(Mock, cli.credential_manager).assert_not_called()


def test_explicit_remote_inspection_only_reads(run, command, capsys):
    current = pending(run)
    command("reconcile", "--attempt-id", "1", "--remote")
    output = json.loads(capsys.readouterr().out)
    assert output["remote_container_status"] == "PUBLISHED"
    assert output["remote_publication_observed"]
    assert output["remote_outcome_uncertain"] is False
    assert output["published_media_identity_unresolved"]
    assert output["media_owner_verified"] is False
    assert snapshot(run.repo, 1) == current
    assert {call[0] for call in run.client.mock_calls} <= {
        "identify_account",
        "container_status",
    }
    run.credentials.get_valid_credential.assert_not_called()


def test_resolution_preview_is_read_only_until_exact_interactive_confirmation(
    run, command, capsys, tmp_path
):
    current = pending(run)
    path = evidence_file(tmp_path)
    command(
        "reconcile",
        "--attempt-id",
        "1",
        "--remote",
        "--resolve",
        "published",
        "--evidence",
        str(path),
    )
    output = json.loads(capsys.readouterr().out)
    assert output["proposed_local_state"] == "published" and output[
        "confirmation"
    ].startswith("RECOVER PUBLISHED 1")
    assert snapshot(run.repo, 1) == current
    run.client.publish_container.assert_not_called()


def test_confirm_flag_cannot_trigger_unattended_recovery(
    run, command, capsys, tmp_path
):
    pending(run)
    with pytest.raises(SystemExit) as caught:
        command(
            "reconcile",
            "--attempt-id",
            "1",
            "--remote",
            "--resolve",
            "published",
            "--evidence",
            str(evidence_file(tmp_path)),
            "--confirm",
        )
    assert (
        caught.value.code == 1
        and "interactive_recovery_confirmation_required" in capsys.readouterr().err
    )
    cast(Mock, cli.load_config).assert_not_called()
    cast(Mock, cli.credential_manager).assert_not_called()


def test_complete_mocked_cli_recovery_reopens_verified_result(
    run, command, capsys, tmp_path
):
    pending(run)
    path = evidence_file(tmp_path)
    args = (
        "reconcile",
        "--attempt-id",
        "1",
        "--remote",
        "--resolve",
        "published",
        "--evidence",
        str(path),
    )
    command(*args)
    challenge = json.loads(capsys.readouterr().out)["confirmation"]
    command(*args, "--confirm", tty=True, answer=lambda _: challenge)
    output = capsys.readouterr()
    final = json.loads(output.out.splitlines()[-1])
    assert (
        final["mode"] == "local_recovery_completed" and final["status"] == "Published"
    )
    assert final["checkpoint"] == "completed" and final["evidence_attached"]
    assert PRIVATE not in output.out + output.err
    run.client.create_image_container.assert_not_called()
    run.client.publish_container.assert_not_called()


@pytest.mark.parametrize("answer", ["yes", "", None])
def test_wrong_or_cancelled_confirmation_leaves_attempt_unresolved(
    run, command, capsys, tmp_path, answer
):
    current = pending(run)

    def respond(_):
        if answer is None:
            raise KeyboardInterrupt
        return answer

    with pytest.raises(SystemExit):
        command(
            "reconcile",
            "--attempt-id",
            "1",
            "--remote",
            "--resolve",
            "published",
            "--evidence",
            str(evidence_file(tmp_path)),
            "--confirm",
            tty=True,
            answer=respond,
        )
    assert snapshot(run.repo, 1) == current
    assert PRIVATE not in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ("reconcile",),
        ("reconcile", "--attempt-id", "0"),
        ("status", "--remote"),
        ("reconcile", "--attempt-id", "1", "--confirm"),
        ("reconcile", "--attempt-id", "1", "--resolve", "published"),
        ("reconcile", "--attempt-id", "1", "--remote", "--resolve", "published"),
    ],
)
def test_invalid_recovery_options_never_touch_provider(run, command, args):
    with pytest.raises(SystemExit):
        command(*args, tty=True)
    cast(Mock, cli.credential_manager).assert_not_called()
    assert run.client.mock_calls == []


def test_empty_status_and_unknown_attempt(run, command, capsys):
    command("status")
    assert json.loads(capsys.readouterr().out)["attempts"] == []
    with pytest.raises(SystemExit):
        command("reconcile", "--attempt-id", "999")
    assert "unknown_attempt" in capsys.readouterr().err


def test_backup_and_migration_copy_sqlite_and_palette_without_changing_history(
    tmp_path, monkeypatch, capsys
):
    from ask_every_day.domain import AppConfig

    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        legacy(connection, 2)
        before = connection.execute("SELECT * FROM publications").fetchall()
    palette = path.with_name(path.name + ".palette.json")
    palette.write_text('{"synthetic_palette_backup":true}')
    config = AppConfig(path, tmp_path / "assets", "@ask.every.day")
    monkeypatch.setattr(cli, "load_config", Mock(return_value=config))
    monkeypatch.setattr(cli, "configure_logging", Mock())
    manager = Mock(side_effect=AssertionError("Must not use credentials"))
    monkeypatch.setattr(cli, "credential_manager", manager)
    monkeypatch.setattr(
        sys, "argv", ["recovery", "migrate", "--backup-dir", str(tmp_path / "backups")]
    )
    cli.main()
    output = json.loads(capsys.readouterr().out)
    backup = Path(output["backup"])
    assert (backup / "state.sqlite3.palette.json").read_bytes() == palette.read_bytes()
    with sqlite3.connect(backup / "state.sqlite3") as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (2,)
        assert saved.execute("SELECT * FROM publications").fetchall() == before
    with sqlite3.connect(path) as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone() == (3,)
        assert migrated.execute("SELECT * FROM publications").fetchall() == before
        assert migrated.execute(
            "SELECT count(*) FROM publication_checkpoints"
        ).fetchone() == (0,)
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
    manager.assert_not_called()


def test_backup_failure_does_not_migrate_or_touch_source(tmp_path, monkeypatch, capsys):
    from ask_every_day.domain import AppConfig

    path = tmp_path / "source.sqlite"
    with sqlite3.connect(path) as connection:
        legacy(connection, 2)
    from ask_every_day.publication_recovery import RecoveryError

    monkeypatch.setattr(
        cli,
        "load_config",
        Mock(return_value=AppConfig(path, tmp_path, "@ask.every.day")),
    )
    monkeypatch.setattr(cli, "configure_logging", Mock())
    monkeypatch.setattr(
        cli,
        "backup_state",
        Mock(side_effect=RecoveryError("publication_state_backup_failed")),
    )
    monkeypatch.setattr(sys, "argv", ["recovery", "migrate"])
    with pytest.raises(SystemExit):
        cli.main()
    with sqlite3.connect(path) as source:
        assert source.execute("PRAGMA user_version").fetchone() == (2,)
    assert "publication_state_backup_failed" in capsys.readouterr().err


def test_remote_prepared_inspection_retains_proven_no_dispatch(run, command, capsys):
    current = pending(run, CheckpointPhase.PREPARED)
    command("reconcile", "--attempt-id", "1", "--remote")
    output = json.loads(capsys.readouterr().out)
    assert output["remote_outcome_uncertain"] is False
    assert output["remote_publication_observed"] is False
    assert output["operator_action_required"]
    assert snapshot(run.repo, 1) == current
    run.client.container_status.assert_not_called()
    run.client.create_image_container.assert_not_called()
    run.client.publish_container.assert_not_called()


@pytest.mark.parametrize("symlink", [False, True])
def test_backup_handles_absent_palette_and_rejects_symlink_without_source_changes(
    run, tmp_path, symlink
):
    palette = ledger_path(run.config)
    palette.unlink()
    other = tmp_path / "other.json"
    if symlink:
        other.write_text("synthetic-private-payload")
        palette.symlink_to(other)
    before = run.config.database_path.read_bytes()
    destination = tmp_path / "backups"
    with cli.publication_lock(run.config.database_path):
        if symlink:
            with pytest.raises(RecoveryError, match="publication_state_backup_failed"):
                backup_state(run.config, destination)
            assert list(destination.iterdir()) == []
            assert other.read_text() == "synthetic-private-payload"
        else:
            saved = backup_state(run.config, destination)
            manifest = json.loads((saved / "manifest.json").read_text())
            assert manifest["palette_present"] is False
            assert manifest["schema_version"] == 3
            assert set(manifest["files"]) == {"state.sqlite3"}
            assert stat.S_IMODE(saved.stat().st_mode) == 0o700
            assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in saved.iterdir())
    assert run.config.database_path.read_bytes() == before
