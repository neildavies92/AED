"""Synthetic host/launchd/Docker acceptance; never install a real agent or publish."""

import importlib.util
import json
import plistlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "aed_desktop_scheduler",
    Path(__file__).parents[1] / "deployment/desktop_scheduler.py",
)
assert SPEC and SPEC.loader
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)
IMAGE = "sha256:" + "a" * 64
REVISION = "b" * 40
NOW = datetime(2026, 10, 29, 19, tzinfo=UTC)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    root = tmp_path / "private deployment"
    root.mkdir(mode=0o700)
    for name in ("state", "credentials", "config"):
        (root / name).mkdir(mode=0o700)
    files = {
        "image-id": IMAGE,
        "compose.yaml": "synthetic compose",
        "state/aed.sqlite3": "synthetic database; host must never open SQLite",
        "config/schedule.toml": 'timezone="Europe/London"\nhour=19\nminute=0\nwindow_minutes=30\nauthorization_days=30\n',
        "config/aed.toml": 'database_path="/var/lib/aed/aed.sqlite3"\nassets_path="/var/lib/aed/assets"\naccount_handle="@ask.every.day"\n',
        "config/instagram-auth.toml": 'app_id="1001"\n',
    }
    for name, text in files.items():
        (root / name).write_text(text)
        (root / name).chmod(0o600)
    service = {
        "image": IMAGE,
        "pull_policy": "never",
        "user": "10001:10001",
        "init": True,
        "read_only": True,
        "working_dir": "/opt/aed",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
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
    state = SimpleNamespace(
        root=root,
        service=service,
        enabled=False,
        valid=False,
        daily=None,
        loaded=False,
        disabled=False,
        running=False,
        unresolved=False,
        legacy=False,
        native="",
        containers="",
        fail=None,
        calls=[],
    )

    def query(command, env, code, *, optional=False):
        state.calls.append(command)
        if state.fail == code:
            raise host.Blocked(code)
        if command[:2] == ["/bin/launchctl", "print"]:
            loaded = state.legacy if command[-1].endswith(host.LEGACY) else state.loaded
            return SimpleNamespace(
                returncode=0 if loaded else 3,
                stdout="pid = 123" if state.running else "state = waiting",
            )
        if command[:2] == ["/bin/launchctl", "print-disabled"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f'"{host.LABEL}" => {str(state.disabled).lower()}',
            )
        if command[:2] == ["/bin/ps", "-axo"]:
            return SimpleNamespace(returncode=0, stdout=state.native)
        if "self-check" in command:
            value = {"self_check": True}
        elif "context" in command[2:] and "inspect" in command:
            return SimpleNamespace(returncode=0, stdout="unix:///synthetic/docker.sock")
        elif "image" in command and "inspect" in command:
            value = [
                {
                    "Id": IMAGE,
                    "Config": {
                        "Labels": {"org.opencontainers.image.revision": REVISION}
                    },
                }
            ]
        elif "config" in command and "--format" in command:
            value = {"services": {"publisher": state.service}}
        elif "ps" in command:
            return SimpleNamespace(returncode=0, stdout=state.containers)
        elif "mounts" in command:
            value = {"private_mounts_valid": True, "read_only_check": True}
        elif "ask_every_day.canonical_renderer" in command:
            value = {"approved_editorial_fonts": True}
        elif "publication-status" in command:
            value = {
                "attempts": [{"operator_action_required": True}]
                if state.unresolved
                else []
            }
        elif "schedule-preview" in command:
            value = {"daily_block": state.daily}
        elif "schedule-status" in command:
            value = {"enabled": state.enabled, "authorization_valid": state.valid}
        elif "schedule-check-credentials" in command:
            value = {"credential_ready": True}
        else:
            return SimpleNamespace(returncode=0, stdout="synthetic")
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))

    monkeypatch.setattr(host, "query", query)
    monkeypatch.setattr(host, "host_supported", lambda: None)
    monkeypatch.setattr(host, "host_timezone", lambda: "Europe/London")
    monkeypatch.setattr(
        host, "agent_path", lambda: tmp_path / "LaunchAgents" / f"{host.LABEL}.plist"
    )
    # Use actual executable files but a fake Docker protocol; no Docker call occurs.
    host.prepare(root, Path(sys.executable), Path(sys.executable))
    state.settings = host.load(root)
    monkeypatch.setattr(host, "confirm", Mock())
    state.publish = Mock(
        return_value=(
            0,
            json.dumps(
                {
                    "outcome": "published",
                    "local_day": "2026-10-29",
                    "attempt_id": 4,
                    "post_id": "AED-0066",
                }
            ),
        )
    )
    monkeypatch.setattr(host, "publication", state.publish)
    return state


def arm(d):
    fingerprint = host.digest(d.settings)
    host.save(d.root / "host/authority.json", {"binding": fingerprint})
    host.save(
        d.root / "host/activation.json",
        {"binding": fingerprint, "first_day": "2026-10-29"},
    )
    d.enabled = d.valid = True


def test_preparation_and_installation_do_not_authorize_or_run(deployment):
    d = deployment
    payload = plistlib.loads((d.root / "host" / f"{host.LABEL}.plist").read_bytes())
    assert payload["StartCalendarInterval"] == {"Hour": 19, "Minute": 0}
    assert payload["Disabled"] and not payload["RunAtLoad"] and not payload["KeepAlive"]
    assert payload["ExitTimeOut"] == 0 and payload["Label"] != host.LEGACY
    assert payload["ProgramArguments"][1] == "-I"
    assert not (d.root / "host/activation.json").exists()
    with pytest.raises(host.Blocked, match="authority"):
        host.install(d.root, d.settings)
    host.save(d.root / "host/authority.json", {"binding": host.digest(d.settings)})
    result = host.install(d.root, d.settings)
    assert result == {"installed": True, "enabled": False, "loaded": False}
    assert not any("bootstrap" in c or "schedule-run" in c for c in d.calls)
    assert not (d.root / "host/activation.json").exists()
    d.publish.assert_not_called()


def test_activation_requires_both_owner_authorities_and_future_time(deployment):
    d = deployment
    host.save(d.root / "host/authority.json", {"binding": host.digest(d.settings)})
    host.install(d.root, d.settings)
    with pytest.raises(host.Blocked, match="authorization"):
        host.activate(d.root, d.settings, NOW)
    d.enabled = d.valid = True
    result = host.activate(d.root, d.settings, NOW)
    assert (
        result["first_eligible_day"] == "2026-10-30"
        and not result["publication_started"]
    )
    assert any("bootstrap" in c for c in d.calls)
    d.publish.assert_not_called()


@pytest.mark.parametrize(
    "stamp,eligible",
    [
        ("2026-10-29T18:59:59+00:00", False),
        ("2026-10-29T19:00:00+00:00", True),
        ("2026-10-29T19:00:59+00:00", True),
        ("2026-10-29T19:01:00+00:00", False),
        ("2026-10-29T19:29:59+00:00", False),
        ("2026-10-29T19:30:00+00:00", False),
        ("2027-03-28T18:00:00+00:00", True),
        ("2027-03-28T19:00:00+00:00", False),
        ("2027-10-31T19:00:00+00:00", True),
        ("2027-10-31T18:00:00+00:00", False),
        ("2027-10-31T01:30:00+00:00", False),
    ],
)
def test_host_admits_only_start_minute_and_never_wake_backfill(
    deployment, stamp, eligible
):
    d = deployment
    arm(d)
    now = datetime.fromisoformat(stamp)
    d.publish.return_value = (
        0,
        json.dumps(
            {
                "outcome": "published",
                "local_day": now.astimezone(host.LONDON).date().isoformat(),
                "attempt_id": 4,
                "post_id": "AED-0066",
            }
        ),
    )
    result, code = host.run_once(d.root, d.settings, clock=lambda: now)
    assert code == 0
    assert (result["outcome"] == "published") is eligible
    assert d.publish.call_count == int(eligible)


def test_restart_duplicate_and_manual_publication_do_not_dispatch_again(deployment):
    d = deployment
    arm(d)
    d.daily = "already_published_today"
    assert (
        host.run_once(d.root, d.settings, clock=lambda: NOW)[0]["outcome"]
        == "daily_guard"
    )
    d.daily = None
    settings = host.load(d.root)
    assert (
        host.run_once(d.root, settings, clock=lambda: NOW)[0]["outcome"]
        == "already_invoked_today"
    )
    d.publish.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        "docker_daemon_unavailable",
        "compose_unavailable",
        "reviewed_image_unavailable",
        "container_mount_permissions_invalid",
        "canonical_font_invalid",
        "credential_configuration_or_store_invalid",
        "scheduler_authorization_invalid",
        "state_content_or_palette_invalid",
    ],
)
def test_preflight_failure_is_actionable_and_never_retried_that_date(
    deployment, failure
):
    d = deployment
    arm(d)
    d.fail = failure
    result, code = host.run_once(d.root, d.settings, clock=lambda: NOW)
    assert code == 1 and result["reason"] == failure
    d.fail = None
    assert (
        host.run_once(d.root, d.settings, clock=lambda: NOW)[0]["outcome"]
        == "already_invoked_today"
    )
    d.publish.assert_not_called()


@pytest.mark.parametrize(
    "drift", ["image", "mount", "policy", "database", "authorization", "authority"]
)
def test_changed_runtime_or_authority_fails_closed(deployment, drift):
    d = deployment
    arm(d)
    if drift == "image":
        d.service["image"] = "mutable:tag"
    elif drift == "mount":
        d.service["volumes"][1]["source"] = str(d.root / "other-state")
    elif drift == "policy":
        p = d.root / "config/schedule.toml"
        p.write_text(p.read_text().replace("hour=19", "hour=20"))
    elif drift == "database":
        p = d.root / "state/aed.sqlite3"
        p.rename(p.with_suffix(".old"))
        p.write_text("different database")
        p.chmod(0o600)
    elif drift == "authorization":
        d.valid = False
    else:
        (d.root / "host/authority.json").unlink()
    assert host.run_once(d.root, d.settings, clock=lambda: NOW)[1] == 1
    d.publish.assert_not_called()


def test_overlap_does_not_call_docker(deployment):
    d = deployment
    arm(d)
    calls = len(d.calls)
    with host.host_lock(d.root), pytest.raises(host.Blocked, match="overlap"):
        host.run_once(d.root, d.settings, clock=lambda: NOW)
    assert len(d.calls) == calls
    d.publish.assert_not_called()


@pytest.mark.parametrize("kind", ["legacy", "native", "container"])
def test_competing_publishers_block(deployment, kind):
    d = deployment
    arm(d)
    if kind == "legacy":
        d.legacy = True
    elif kind == "native":
        d.native = "python -m ask_every_day.publish_next_cli --confirm"
    else:
        d.containers = json.dumps({"Names": host.CONTAINER, "Labels": ""})
    assert host.run_once(d.root, d.settings, clock=lambda: NOW)[1] == 1
    d.publish.assert_not_called()


def test_failed_or_uncertain_container_never_replays_and_redacts(deployment):
    d = deployment
    arm(d)
    d.publish.return_value = (
        1,
        "SYNTHETIC_TOKEN_PRIVATE_RESPONSE https://private-capability.invalid",
    )
    result, code = host.run_once(d.root, d.settings, clock=lambda: NOW)
    assert code == 1 and "unverified" in result["reason"]
    assert (
        host.run_once(d.root, host.load(d.root), clock=lambda: NOW)[0]["outcome"]
        == "already_invoked_today"
    )
    assert d.publish.call_count == 1
    assert "SYNTHETIC_TOKEN" not in (d.root / "host/host.jsonl").read_text()


@pytest.mark.parametrize(
    "fields", [{}, {"attempt_id": True, "post_id": "AED-0066"}, {"attempt_id": 4}]
)
def test_success_without_durable_result_identifiers_is_uncertain(deployment, fields):
    d = deployment
    arm(d)
    d.publish.return_value = (
        0,
        json.dumps({"outcome": "published", "local_day": "2026-10-29", **fields}),
    )
    result, code = host.run_once(d.root, d.settings, clock=lambda: NOW)
    assert (
        code == 1
        and result["reason"] == "container_result_unverified_inspect_checkpoint"
    )
    assert host.run_once(d.root, d.settings, clock=lambda: NOW)[0]["outcome"] == (
        "already_invoked_today"
    )


def test_nonpublishing_diagnose_does_not_read_credentials_by_default(deployment):
    d = deployment
    report, code = host.diagnose(d.root, d.settings)
    assert code == 0 and report["authority"] == "mac_until_explicit_cutover"
    assert report["credential_check"] == "not_requested"
    assert not any(
        "schedule-check-credentials" in c or "schedule-run" in c for c in d.calls
    )
    d.publish.assert_not_called()
    assert all(
        "offline.compose.json" in " ".join(c) for c in d.calls if "publisher" in c
    )


def test_status_honours_launchd_override_and_exact_activation(deployment):
    d = deployment
    arm(d)
    host.install(d.root, d.settings)
    d.loaded = True
    assert host.diagnose(d.root, d.settings, credentials=True)[0]["status"] == (
        "ready_for_next_1900"
    )
    d.disabled = True
    assert host.diagnose(d.root, d.settings, credentials=True)[0]["status"] == (
        "host_trigger_inactive"
    )
    d.disabled = False
    host.save(d.root / "host/activation.json", {"binding": "stale"})
    assert host.diagnose(d.root, d.settings, credentials=True)[0]["status"] == (
        "host_trigger_inactive"
    )


def test_unresolved_checkpoint_blocks_before_credentials_or_dispatch(deployment):
    d = deployment
    arm(d)
    d.unresolved = True
    report, code = host.diagnose(d.root, d.settings, credentials=True)
    assert code == 1 and report["reason"] == "unresolved_publication_reconcile_required"
    assert not any("schedule-check-credentials" in c for c in d.calls)
    d.publish.assert_not_called()


def test_new_authority_cannot_coexist_with_installed_agent(deployment, capsys):
    d = deployment
    arm(d)
    host.install(d.root, d.settings)
    assert host.main(["accept-authority", "--directory", str(d.root)]) == 1
    assert "uninstall_previous_docker_agent" in capsys.readouterr().out
    host.confirm.assert_not_called()


def test_disable_preserves_active_work_and_uninstall_preserves_all_state(deployment):
    d = deployment
    arm(d)
    host.install(d.root, d.settings)
    original = (d.root / "state/aed.sqlite3").read_bytes()
    d.loaded = d.running = True
    report = host.disable(d.root, d.settings)
    assert report["active_operation_left_running"]
    assert not (d.root / "host/activation.json").exists()
    with pytest.raises(host.Blocked, match="wait"):
        host.disable(d.root, d.settings, uninstall=True)
    assert not any("bootout" in c for c in d.calls)
    d.running = False
    host.disable(d.root, d.settings, uninstall=True)
    assert (d.root / "state/aed.sqlite3").read_bytes() == original
    assert (d.root / "credentials").is_dir() and (
        d.root / "host/authority.json"
    ).exists()
    assert not host.agent_path().exists()
    assert not any("kill" in c or "schedule-enable" in c for c in d.calls)


def test_host_timezone_mismatch_is_rejected(monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "darwin")
    monkeypatch.setattr(host.os, "getuid", lambda: 501)
    monkeypatch.setattr(host, "host_timezone", lambda: "other")
    with pytest.raises(host.Blocked, match="timezone"):
        host.host_supported()


def test_preflight_cannot_drift_past_start_minute(deployment):
    d = deployment
    arm(d)
    values = iter([NOW, NOW.replace(minute=1)])
    assert host.run_once(d.root, d.settings, clock=lambda: next(values))[1] == 1
    d.publish.assert_not_called()


def test_real_isolated_python_runs_installed_script_without_shell(deployment):
    d = deployment
    result = subprocess.run(
        [
            d.settings["python"],
            "-I",
            str(d.root / "host/desktop_scheduler.py"),
            "self-check",
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    assert json.loads(result.stdout) == {"self_check": True}


def test_logs_rotate_and_remain_private(deployment):
    d = deployment
    with host.event_log(d.root) as event:
        for _ in range(7000):
            event("synthetic_retention_probe", reason="x" * 512)
    logs = list((d.root / "host").glob("host.jsonl*"))
    assert 2 <= len(logs) <= 4
    assert all(p.stat().st_size <= 1_048_576 for p in logs)
    assert all(p.stat().st_mode & 0o077 == 0 for p in logs)


def test_publication_process_has_no_timeout_or_force_kill(monkeypatch):
    process = Mock(returncode=0)
    process.communicate.return_value = ('{"outcome":"disabled"}', None)
    start = Mock(return_value=process)
    monkeypatch.setattr(host.subprocess, "Popen", start)
    assert host.publication(["docker", "schedule-run"], {})[0] == 0
    assert "timeout" not in start.call_args.kwargs
    assert start.call_args.kwargs["start_new_session"]
    process.kill.assert_not_called()
    process.terminate.assert_not_called()
