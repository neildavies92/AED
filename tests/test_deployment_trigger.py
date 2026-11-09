"""Hosted slot admission and independent monitoring with synthetic Docker results."""

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "aed_trigger", Path(__file__).resolve().parents[1] / "deployment/trigger.py"
)
assert spec and spec.loader
trigger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trigger)

NOW = datetime(2026, 11, 6, 20, tzinfo=UTC)
SCHEDULE = {"hour": 20, "minute": 0}
IMAGE = "sha256:" + "a" * 64


def compose_model(root):
    return json.dumps(
        {
            "services": {
                "publisher": {
                    "image": IMAGE,
                    "pull_policy": "never",
                    "user": "10001:10001",
                    "read_only": True,
                    "volumes": [
                        {
                            "type": "bind",
                            "source": str(root / name),
                            "target": target,
                            "read_only": readonly,
                            "bind": {"create_host_path": False},
                        }
                        for name, target, readonly in (
                            ("config", "/etc/aed", True),
                            ("state", "/var/lib/aed", False),
                            ("credentials", "/var/lib/aed-credentials", False),
                        )
                    ],
                }
            }
        }
    )


@pytest.mark.parametrize(
    "stamp,schedule,expected",
    [
        ("2026-06-01T19:00:00+00:00", SCHEDULE, True),
        ("2026-06-01T19:01:00+00:00", SCHEDULE, False),
        ("2026-12-01T20:00:00+00:00", SCHEDULE, True),
        ("2026-12-01T19:00:00+00:00", SCHEDULE, False),
        ("2026-10-25T00:30:00+00:00", {"hour": 1, "minute": 30}, True),
        ("2026-10-25T01:30:00+00:00", {"hour": 1, "minute": 30}, False),
        ("2026-03-29T01:30:00+00:00", {"hour": 1, "minute": 30}, False),
        (
            "2026-12-01T20:00:00+00:00",
            {**SCHEDULE, "effective_from": "2026-12-02"},
            False,
        ),
    ],
)
def test_slot_uses_london_time_with_no_catchup(stamp, schedule, expected):
    assert trigger.eligible(datetime.fromisoformat(stamp), schedule) is expected


@pytest.fixture
def host(tmp_path, monkeypatch):
    (tmp_path / "host").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config/aed.toml").write_text(
        'database_path="/var/lib/aed/aed.sqlite3"\n'
        'assets_path="/var/lib/aed/assets"\naccount_handle="@ask.every.day"\n'
    )
    (tmp_path / "image-id").write_text(IMAGE)
    monkeypatch.setattr(trigger, "ROOT", tmp_path)
    monkeypatch.setattr(trigger, "binding", lambda: "reviewed")
    monkeypatch.setattr(trigger, "policy", lambda: SCHEDULE)
    trigger.record("authority.json", {"binding": "reviewed", "first_day": "2026-11-06"})
    process = Mock(
        side_effect=[
            CompletedProcess([], 0, compose_model(trigger.ROOT)),
            CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "schema_version": 1,
                        "local_day": "2026-11-06",
                        "outcome": "published",
                    }
                ),
            ),
        ]
    )
    monkeypatch.setattr(trigger.subprocess, "run", process)
    return process


def test_receipt_is_durable_before_dispatch_and_suppresses_second_attempt(host):
    dispatch = host.side_effect

    def intercept(*args, **kwargs):
        if "schedule-run" in args[0]:
            assert trigger.read("last-invocation.json")["outcome"] == "admitted"
            assert "aed-daily" in args[0]
            assert "timeout" not in kwargs
        return next(dispatch)

    host.side_effect = intercept
    assert trigger.run(SCHEDULE, clock=lambda: NOW) == 0
    assert trigger.run(SCHEDULE, clock=lambda: NOW) == 0
    assert host.call_count == 2
    assert trigger.read("last-invocation.json")["outcome"] == "published"


@pytest.mark.parametrize("outcome", ["admitted", "preflight_failed", "blocked"])
def test_imported_mac_receipt_blocks_another_run(host, outcome):
    trigger.record(
        "last-invocation.json", {"local_day": "2026-11-06", "outcome": outcome}
    )
    assert trigger.run(SCHEDULE, clock=lambda: NOW) == 0
    host.assert_not_called()


def test_overlap_never_starts_container(host, monkeypatch):
    monkeypatch.setattr(trigger.fcntl, "flock", Mock(side_effect=BlockingIOError))
    assert trigger.run(SCHEDULE, clock=lambda: NOW) == 0
    host.assert_not_called()


def test_host_drift_requires_new_attestation(host, monkeypatch):
    monkeypatch.setattr(trigger, "binding", lambda: "changed")
    assert trigger.run(SCHEDULE, clock=lambda: NOW) == 1
    host.assert_not_called()


def test_bad_image_consumes_slot_without_dispatch(host):
    host.side_effect = None
    host.return_value = CompletedProcess([], 0, "mutable")
    assert trigger.run(SCHEDULE, clock=lambda: NOW) == 1
    assert trigger.run(SCHEDULE, clock=lambda: NOW) == 0
    assert host.call_count == 1
    assert (
        trigger.read("last-invocation.json")["outcome"] == "infrastructure_unavailable"
    )


def test_delayed_preflight_consumes_slot_without_dispatch(host):
    stamps = iter([NOW, NOW.replace(minute=1)])
    assert trigger.run(SCHEDULE, clock=lambda: next(stamps)) == 1
    assert trigger.read("last-invocation.json")["outcome"] == "preflight_failed"
    assert host.call_count == 1


def test_watchdog_runs_without_publisher_receipt_and_can_alert(host, monkeypatch):
    host.side_effect = [
        CompletedProcess([], 0, compose_model(trigger.ROOT)),
        CompletedProcess(
            [],
            0,
            '{"classification":"scheduler_never_triggered","alert_delivered":true}',
        ),
    ]
    ping = Mock()
    monkeypatch.setattr(trigger, "ping", ping)
    assert trigger.monitor(SCHEDULE, NOW.replace(minute=35)) == 1
    assert host.call_args.args[0][-1] == "missing"
    assert "schedule-run" not in host.call_args.args[0]
    ping.assert_called_once_with("scheduler_never_triggered", True)


def test_watchdog_reports_docker_failure_to_external_monitor(host, monkeypatch, capsys):
    host.side_effect = OSError("PRIVATE")
    ping = Mock()
    monkeypatch.setattr(trigger, "ping", ping)
    assert trigger.monitor(SCHEDULE, NOW.replace(minute=35)) == 1
    ping.assert_called_once_with("infrastructure_unavailable", False)
    assert "PRIVATE" not in capsys.readouterr().out


def test_watchdog_attributes_late_slot_to_previous_day():
    now = datetime(2026, 11, 7, 0, 5, tzinfo=UTC)
    assert (
        trigger.watch_day(now, {"hour": 23, "minute": 30}).isoformat() == "2026-11-06"
    )
