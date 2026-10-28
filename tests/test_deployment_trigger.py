"""UTC cron coverage and host overlap/error behavior without Docker operations."""

import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "aed_trigger", Path(__file__).resolve().parents[1] / "deployment/trigger.py"
)
assert spec and spec.loader
trigger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trigger)


@pytest.mark.parametrize(
    "stamp,command,expected",
    [
        ("2026-06-01T20:00:00+00:00", "run", True),
        ("2026-06-01T20:30:00+00:00", "run", False),
        ("2026-12-01T21:00:00+00:00", "run", True),
        ("2026-12-01T20:00:00+00:00", "run", False),
        ("2026-06-01T20:35:00+00:00", "monitor", True),
        ("2026-12-01T21:35:00+00:00", "monitor", True),
        ("2026-10-25T01:30:00+00:00", "run", False),
        ("2026-03-29T01:30:00+00:00", "run", False),
    ],
)
def test_utc_trigger_covers_london_without_cron_tz(stamp, command, expected):
    assert trigger.eligible(command, datetime.fromisoformat(stamp)) is expected


@pytest.fixture
def host(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    (tmp_path / "image-id").write_text("sha256:" + "a" * 64)
    monkeypatch.setattr(trigger, "ROOT", tmp_path)
    monkeypatch.setattr(trigger, "eligible", lambda *args: True)
    process = Mock(
        side_effect=[
            SimpleNamespace(stdout="sha256:" + "a" * 64),
            SimpleNamespace(stdout='{\n  "outcome": "disabled"\n}', returncode=0),
        ]
    )
    monkeypatch.setattr(trigger.subprocess, "run", process)
    return process, tmp_path


def test_trigger_retains_result_and_uses_named_one_shot_persistent_logs(host):
    process, root = host
    assert trigger.invoke("run") == 0
    command = process.call_args_list[1].args[0]
    assert "--rm" in command and "aed-daily" in command
    assert command[-2:] == ["--log-file", "/var/lib/aed/scheduler.jsonl"]
    assert '"outcome": "disabled"' in (root / "logs/host.jsonl").read_text()


def test_overlap_never_starts_container(host, monkeypatch):
    monkeypatch.setattr(trigger.fcntl, "flock", Mock(side_effect=BlockingIOError))
    assert trigger.invoke("run") == 0
    host[0].assert_not_called()


def test_image_drift_fails_closed_and_logs_safe_error(host):
    process, root = host
    process.side_effect = [
        SimpleNamespace(stdout="mutable-tag"),
        SimpleNamespace(returncode=0),
    ]
    assert trigger.invoke("run") == 1
    assert not any("schedule-run" in c.args[0] for c in process.call_args_list)
    assert '"exit_code": 1' in (root / "logs/host.jsonl").read_text()


def test_font_overlay_used_for_verification_and_run(host):
    process, root = host
    (root / "fonts.compose.yaml").write_text("synthetic")
    assert trigger.invoke("run") == 0
    assert all(
        str(root / "fonts.compose.yaml") in call.args[0]
        for call in process.call_args_list
    )
    assert "/var/lib/aed/data" in process.call_args_list[1].args[0]


def test_failed_container_is_not_retried_and_secret_stderr_is_not_logged(host):
    process, root = host
    process.side_effect = [
        SimpleNamespace(stdout="sha256:" + "a" * 64),
        SimpleNamespace(
            stdout='{"outcome":"blocked"}', stderr="PRIVATE_TOKEN", returncode=1
        ),
        SimpleNamespace(returncode=0),
    ]
    assert trigger.invoke("run") == 1
    assert sum("schedule-run" in c.args[0] for c in process.call_args_list) == 1
    text = (root / "logs/host.jsonl").read_text()
    assert "PRIVATE_TOKEN" not in text and '"exit_code": 1' in text
