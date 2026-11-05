"""Offline schedule transactions against real SQLite and synthetic launchd."""

import plistlib
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from ask_every_day import schedule_change as change
from ask_every_day.domain import Publication, PublicationStatus
from ask_every_day.publish_next import PublishError
from ask_every_day.publish_next_cli import publication_lock
from ask_every_day.scheduler_policy import (
    ScheduleConfig,
    ScheduleError,
    load_schedule,
    read_authorization,
    save_authorization,
    state_path,
    validate_authorization,
)
from tests.test_desktop_scheduler import NOW as HOST_NOW
from tests.test_desktop_scheduler import arm, host
from tests.test_desktop_scheduler import deployment as deployment
from tests.test_publish_next import NOW
from tests.test_publish_next import run as run
from tests.test_scheduler import authorize
from tests.test_scheduler import inputs as inputs
from tests.test_scheduler import schedule as schedule


@pytest.fixture
def participant(run, inputs, schedule, monkeypatch, tmp_path):
    policy = tmp_path / "schedule.toml"
    policy.write_text("hour=21\nminute=0\n")
    monkeypatch.setattr(change, "load_config", lambda _: run.config)
    monkeypatch.setattr(change, "load_inputs", lambda _: inputs)

    # Runtime hashing/rendering remain real; content, credentials and provider are synthetic.
    def invoke(receive, *, today=True, clock=lambda: NOW):
        messages = []
        day = NOW.date() + timedelta(days=0 if today else 1)
        change.participate(
            Path("unused"),
            policy,
            tmp_path,
            22,
            15,
            day,
            receive,
            messages.append,
            clock=clock,
        )
        return messages

    return policy, invoke


def write_new(policy, *, today=True):
    day = NOW.date() + timedelta(days=0 if today else 1)
    policy.write_text(f'hour=22\nminute=15\neffective_from="{day}"\n')


def test_time_only_change_preserves_approval_expiry_and_all_publication_state(
    run, inputs, schedule, participant
):
    original = authorize(run, inputs, schedule)
    policy, invoke = participant
    before = run.config.database_path.read_bytes()
    assets = {p.name: p.read_bytes() for p in run.config.assets_path.iterdir()}

    def commit():
        # A manual publisher cannot enter while the host reconfigures launchd.
        with pytest.raises(PublishError, match="another_publisher"):
            with publication_lock(run.config.database_path):
                pass
        write_new(policy)
        return "commit"

    messages = invoke(commit)
    current = read_authorization(run.config)
    assert current is not None
    assert current.policy_key != original.policy_key
    assert replace(current, policy_key=original.policy_key) == original
    validate_authorization(
        run.repo, run.config, inputs, load_schedule(policy), current, NOW
    )
    assert messages[0]["authorization_preserved"] is True
    assert messages[-1]["phase"] == "committed"
    assert run.config.database_path.read_bytes() == before
    assert assets == {p.name: p.read_bytes() for p in run.config.assets_path.iterdir()}
    assert (
        not run.repo.list_publications()
        and tuple(run.repo.list_queue()) == original.queue
    )
    assert run.credentials.mock_calls == run.client.mock_calls == []


def test_aborted_change_and_restart_keep_original_approval(
    run, inputs, schedule, participant
):
    authorize(run, inputs, schedule)
    before = state_path(run.config).read_bytes()
    _, invoke = participant
    assert invoke(lambda: "")[1]["phase"] == "aborted"
    assert state_path(run.config).read_bytes() == before
    assert invoke(lambda: "abort")[0]["authorization_preserved"] is True


@pytest.mark.parametrize("drift", ["expired", "policy", "queue", "disabled", "absent"])
def test_invalid_authorization_never_silently_gains_consent(
    run, inputs, schedule, participant, drift
):
    original = authorize(run, inputs, schedule)
    if drift == "expired":
        save_authorization(
            run.config,
            replace(
                original,
                authorized_at=NOW - timedelta(days=40),
                expires_at=NOW - timedelta(days=1),
            ),
        )
    elif drift == "policy":
        save_authorization(run.config, replace(original, policy_key="f" * 64))
    elif drift == "queue":
        run.repo.replace_queue([])
    elif drift == "disabled":
        save_authorization(run.config, replace(original, enabled=False))
    else:
        state_path(run.config).unlink()
    policy, invoke = participant

    def commit():
        write_new(policy)
        return "commit"

    messages = invoke(commit)
    assert messages[0]["authorization_preserved"] is False
    assert messages[0]["reauthorization_reason"]
    current = read_authorization(run.config)
    assert current is None or not current.enabled
    if current:
        assert current.expires_at <= original.expires_at


def test_effective_tomorrow_blocks_today_even_with_valid_rebound_approval(
    run, inputs, schedule, participant
):
    authorize(run, inputs, schedule)
    policy, invoke = participant

    def commit():
        write_new(policy, today=False)
        return "commit"

    invoke(commit, today=False)
    new = load_schedule(policy)
    assert not new.in_window(NOW.replace(hour=22, minute=15))
    assert new.in_window((NOW + timedelta(days=1)).replace(hour=22, minute=15))


@pytest.mark.parametrize(
    "state",
    [
        PublicationStatus.PUBLISHED,
        PublicationStatus.FAILED,
        PublicationStatus.PUBLISHING,
    ],
)
def test_existing_publication_or_attempt_blocks_same_day(run, participant, state):
    p = Publication(
        "AED-0001",
        state,
        NOW,
        NOW if state is PublicationStatus.PUBLISHED else None,
        "123" if state is PublicationStatus.PUBLISHED else None,
    )
    run.repo.create_publication(p)
    run.repo.remove_queue_entry("AED-0001")
    receive = Mock(side_effect=AssertionError("must reject before host mutation"))
    with pytest.raises(ScheduleError):
        participant[1](receive)
    receive.assert_not_called()


def test_other_policy_changes_and_past_times_rejected(run, schedule):
    for new in (replace(schedule, window_minutes=60), replace(schedule, hour=20)):
        with pytest.raises(ScheduleError):
            change.validate_change(run.repo, schedule, new, NOW.date(), NOW)


def test_concurrent_publication_and_elapsed_confirmation_block_commit(
    run, inputs, schedule, participant
):
    authorize(run, inputs, schedule)
    policy, invoke = participant
    with publication_lock(run.config.database_path):
        with pytest.raises(PublishError):
            invoke(lambda: "commit")
    before = state_path(run.config).read_bytes()
    stamps = iter([NOW, NOW.replace(hour=22, minute=15)])

    def commit():
        write_new(policy)
        return "commit"

    with pytest.raises(ScheduleError, match="future"):
        invoke(commit, clock=lambda: next(stamps))
    assert state_path(run.config).read_bytes() == before


@pytest.fixture
def host_change(deployment, monkeypatch):
    d = deployment
    arm(d)
    host.install(d.root, d.settings)
    d.loaded, d.disabled = True, False
    d.messages = []
    d.preserve = True

    @contextmanager
    def participant(*args):
        def exchange(message=None):
            d.messages.append(message)
            return {
                "phase": "ready" if message is None else "committed",
                "authorization_preserved": d.preserve,
                "reauthorization_reason": None
                if d.preserve
                else "authorization_inactive",
            }

        yield exchange

    monkeypatch.setattr(host, "change_participant", participant)
    return d


def test_host_updates_shared_policy_trigger_authority_and_diagnostics(host_change):
    d = host_change
    state_before = (d.root / "state/aed.sqlite3").read_bytes()
    result = host.schedule_set(
        d.root, d.settings, "20:15", today=True, clock=lambda: HOST_NOW
    )
    assert result["authorization_preserved"] and result["effective_day"] == "2026-10-29"
    assert d.messages == [None, "commit"]
    assert d.loaded and not d.disabled
    new_settings = host.load(d.root)
    assert host.binding(d.root, new_settings) == host.digest(new_settings)
    assert host.approved(d.root, host.digest(new_settings))
    assert host.read(d.root / "host/activation.json")["binding"] == host.digest(
        new_settings
    )
    assert plistlib.loads(host.agent_path().read_bytes())["StartCalendarInterval"] == {
        "Hour": 20,
        "Minute": 15,
    }
    assert load_schedule(d.root / "config/schedule.toml").hour == 20
    assert (
        host.schedule_show(d.root, new_settings, now=HOST_NOW)["daily_time"] == "20:15"
    )
    assert (
        host.execution_health(
            d.root, {"host_armed": True, "status": "host_trigger_inactive"}, HOST_NOW
        )["next_scheduled_at"]
        == "2026-10-29T20:15:00+00:00"
    )
    assert (d.root / "state/aed.sqlite3").read_bytes() == state_before
    assert not (d.root / "host/schedule-change.json").exists()
    d.publish.assert_not_called()
    assert not any("kickstart" in c for c in d.calls)


def test_default_tomorrow_and_invalid_approval_leaves_host_disabled(host_change):
    d = host_change
    d.preserve = False
    result = host.schedule_set(d.root, d.settings, "20:00", clock=lambda: HOST_NOW)
    assert result["effective_day"] == "2026-10-30"
    assert result["reauthorization_reason"] == "authorization_inactive"
    assert not (d.root / "host/activation.json").exists()
    assert not d.loaded and d.disabled
    assert host.policy(d.root)["effective_from"] == "2026-10-30"


def test_schedule_does_not_activate_previously_disabled_host(host_change):
    d = host_change
    d.loaded, d.disabled = False, True
    (d.root / "host/activation.json").unlink()
    host.schedule_set(d.root, d.settings, "20:00", clock=lambda: HOST_NOW)
    assert not d.loaded and d.disabled
    assert not (d.root / "host/activation.json").exists()


@pytest.mark.parametrize(
    "code",
    [
        "launchagent_disable_failed",
        "launchagent_unload_failed",
        "launchagent_enable_failed",
        "launchagent_bootstrap_failed",
    ],
)
def test_launchd_failure_restores_previous_files_and_activation(
    host_change, monkeypatch, code
):
    d = host_change
    before = host.change_snapshot(d.root)
    query = host.query
    failed = False

    def fail_once(command, env, reason, **kw):
        nonlocal failed
        if reason == code and not failed:
            failed = True
            raise host.Blocked(code)
        return query(command, env, reason, **kw)

    monkeypatch.setattr(host, "query", fail_once)
    with pytest.raises(host.Blocked, match=code):
        host.schedule_set(
            d.root, d.settings, "20:00", today=True, clock=lambda: HOST_NOW
        )
    assert failed and host.change_snapshot(d.root) == before
    assert d.messages == [None]
    assert d.loaded and not d.disabled
    assert not (d.root / "config/schedule-change.json").exists()
    assert "schedule_change_rolled_back" in (d.root / "host/host.jsonl").read_text()


def test_failed_rollback_stays_blocked_and_explicit_recovery_leaves_disabled(
    host_change,
):
    d = host_change
    d.fail = "launchagent_bootstrap_failed"
    with pytest.raises(host.Blocked, match="rollback_incomplete"):
        host.schedule_set(
            d.root, d.settings, "20:00", today=True, clock=lambda: HOST_NOW
        )
    assert (d.root / "config/schedule-change.json").exists()
    assert host.run_once(d.root, d.settings, clock=lambda: HOST_NOW)[1] == 1
    d.fail = None
    host.schedule_recover(d.root, host.load(d.root))
    assert host.policy(d.root)["hour"] == 19
    assert not d.loaded and d.disabled
    assert not (d.root / "host/activation.json").exists()
    d.publish.assert_not_called()


@pytest.mark.parametrize("outcome", ["admitted", "blocked", "published", "disabled"])
def test_host_admission_cannot_be_reset_by_same_day_edit(host_change, outcome):
    d = host_change
    path = d.root / "host/last-invocation.json"
    host.save(path, {"local_day": HOST_NOW.date().isoformat(), "outcome": outcome})
    before = path.read_bytes()
    with pytest.raises(host.Blocked):
        host.schedule_set(
            d.root, d.settings, "20:00", today=True, clock=lambda: HOST_NOW
        )
    assert path.read_bytes() == before and d.messages == []


@pytest.mark.parametrize(
    "value", ["9:00", "20:0", "24:00", "23:31", "20:60", "20:00:00", " 20:00", "noon"]
)
def test_time_format_and_midnight_boundary(host_change, value):
    with pytest.raises(host.Blocked):
        host.schedule_set(
            host_change.root, host_change.settings, value, clock=lambda: HOST_NOW
        )
    assert host_change.messages == []


def test_concurrent_edits_and_lifecycle_mutations_are_serialized(host_change):
    d = host_change
    with host.host_lock(d.root):
        with pytest.raises(host.Blocked, match="overlap"):
            host.schedule_set(d.root, d.settings, "20:00", clock=lambda: HOST_NOW)
    with host.host_lock(d.root, "configuration.lock"):
        for action in (
            lambda: host.disable(d.root, d.settings),
            lambda: host.activate(d.root, d.settings, HOST_NOW),
            lambda: host.schedule_set(
                d.root, d.settings, "20:00", clock=lambda: HOST_NOW
            ),
        ):
            with pytest.raises(host.Blocked, match="overlap"):
                action()


@pytest.mark.parametrize(
    "hour,minute,stamp,expected",
    [
        (20, 0, "2027-03-27T20:01:00+00:00", "2027-03-28T20:00:00+01:00"),
        (20, 0, "2027-10-30T19:01:00+00:00", "2027-10-31T20:00:00+00:00"),
        (1, 30, "2027-03-28T00:00:00+00:00", "2027-03-29T01:30:00+01:00"),
        (1, 30, "2027-10-31T00:31:00+00:00", "2027-11-01T01:30:00+00:00"),
    ],
)
def test_next_slot_dst_and_missed_minute(hour, minute, stamp, expected):
    value = host.next_slot(
        {"hour": hour, "minute": minute}, datetime.fromisoformat(stamp)
    )
    assert value.isoformat() == expected


def test_spring_gap_and_second_fall_occurrence_never_admit():
    policy = ScheduleConfig(hour=1, minute=45)
    assert not policy.in_window(datetime(2027, 3, 28, 1, 0, tzinfo=UTC))
    assert not policy.in_window(datetime(2027, 10, 31, 1, 45, tzinfo=UTC))
    assert policy.in_window(datetime(2027, 10, 31, 0, 45, tzinfo=UTC))


def test_host_and_application_reject_drift_and_interrupted_change(host_change):
    d = host_change
    path = d.root / "config/schedule.toml"
    path.write_text(path.read_text().replace("hour=19", "hour=20"))
    with pytest.raises(host.Blocked, match="deployment_changed"):
        host.schedule_set(d.root, d.settings, "21:00", clock=lambda: HOST_NOW)
    assert host.run_once(d.root, d.settings, clock=lambda: HOST_NOW)[1] == 1
    host.save(d.root / "config/schedule-change.json", {"in_progress": True})
    with pytest.raises(host.Blocked, match="recover_required"):
        host.schedule_show(d.root, d.settings)


@pytest.mark.parametrize("bad", ["today", "2027-02-30", "20270101", 5, True])
def test_validated_schedule_default_is_2000_and_effective_date_is_strict(bad):
    assert (ScheduleConfig().hour, ScheduleConfig().minute) == (20, 0)
    with pytest.raises(ScheduleError):
        ScheduleConfig(effective_from=bad)


def test_rebound_schedule_publishes_at_new_time_once_using_existing_engine(
    run, inputs, schedule, participant, monkeypatch
):
    from tests.test_scheduler import execute

    monkeypatch.setattr(
        "ask_every_day.scheduler.verify_editorial_fonts", Mock(return_value=[])
    )
    authorize(run, inputs, schedule)
    policy, invoke = participant

    def commit():
        write_new(policy)
        return "commit"

    invoke(commit)
    new = load_schedule(policy)
    assert execute(run, inputs, new, now=NOW).outcome == "outside_window"
    slot = NOW.replace(hour=22, minute=15)
    assert execute(run, inputs, new, now=slot).outcome == "published"
    assert execute(run, inputs, new, now=slot).outcome == "already_published_today"
    assert len(run.repo.list_publications()) == 1
    assert len(run.repo.list_queue()) == 1
    assert run.client.publish_container.call_count == 1


def test_new_host_time_misses_old_trigger_and_receipt_survives_restart(host_change):
    d = host_change
    host.schedule_set(d.root, d.settings, "20:15", today=True, clock=lambda: HOST_NOW)
    fresh = host.load(d.root)
    assert (
        host.run_once(d.root, fresh, clock=lambda: HOST_NOW)[0]["outcome"]
        == "missed_start_no_backfill"
    )
    slot = HOST_NOW.replace(hour=20, minute=15)
    assert host.run_once(d.root, fresh, clock=lambda: slot)[0]["outcome"] == "published"
    assert (
        host.run_once(d.root, host.load(d.root), clock=lambda: slot)[0]["outcome"]
        == "already_invoked_today"
    )
    assert d.publish.call_count == 1
    with pytest.raises(host.Blocked, match="admitted_today"):
        host.schedule_set(
            d.root, host.load(d.root), "21:00", today=True, clock=lambda: slot
        )


def test_past_same_day_time_and_unconfirmed_prompt_cannot_change_files(
    host_change, monkeypatch
):
    d = host_change
    before = host.change_snapshot(d.root)
    with pytest.raises(host.Blocked, match="future"):
        host.schedule_set(
            d.root, d.settings, "18:00", today=True, clock=lambda: HOST_NOW
        )
    monkeypatch.setattr(
        host, "confirm", Mock(side_effect=host.Blocked("owner_confirmation_mismatch"))
    )
    with pytest.raises(host.Blocked, match="confirmation"):
        host.schedule_set(
            d.root, d.settings, "20:00", today=True, clock=lambda: HOST_NOW
        )
    assert host.change_snapshot(d.root) == before and not d.messages


def test_publication_transport_is_blocked_by_interrupted_schedule(
    monkeypatch, tmp_path
):
    from ask_every_day import production_runtime as runtime

    marker = tmp_path / "schedule-change.json"
    marker.write_text("{}")
    monkeypatch.setattr(runtime, "Path", lambda _: marker)
    monkeypatch.setattr(runtime, "docker_runtime", lambda: True)
    with pytest.raises(runtime.DockerPublisherRequired, match="recover_required"):
        runtime.require_docker_publisher()


def test_owner_can_explicitly_move_tomorrow_slot_to_unused_today(host_change):
    d = host_change
    host.schedule_set(d.root, d.settings, "20:00", clock=lambda: HOST_NOW)
    assert host.read(d.root / "host/activation.json")["first_day"] == "2026-10-30"
    host.schedule_set(
        d.root, host.load(d.root), "20:00", today=True, clock=lambda: HOST_NOW
    )
    assert host.policy(d.root)["effective_from"] == "2026-10-29"
    assert host.read(d.root / "host/activation.json")["first_day"] == "2026-10-29"
    d.publish.assert_not_called()


@pytest.mark.parametrize(
    "stamp,policy",
    [
        ("2026-10-26T23:00:00+00:00", ScheduleConfig(effective_from="2026-10-27")),
        ("2027-03-28T03:00:00+00:00", ScheduleConfig(hour=1, minute=30)),
    ],
)
def test_monitor_does_not_expect_publication_before_effective_day_or_in_dst_gap(
    run, stamp, policy
):
    from ask_every_day.deployment import daily_report

    report = daily_report(
        run.config,
        datetime.fromisoformat(stamp),
        policy.hour * 60 + policy.minute + policy.window_minutes,
        schedule=policy,
    )
    assert report["healthy"] and not report["window_ended"]


def test_installed_cli_wrapper_preserves_paths_and_explicit_today(
    host_change, monkeypatch
):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "aed_schedule_wrapper",
        Path(__file__).parents[1] / "deployment/desktop_schedule.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    call = Mock(return_value=type("Result", (), {"returncode": 0})())
    monkeypatch.setattr(module.subprocess, "run", call)
    monkeypatch.setattr(
        sys,
        "argv",
        ["schedule", "--directory", str(host_change.root), "set", "20:00", "--today"],
    )
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 0
    command = call.call_args.args[0]
    assert command == [
        host_change.settings["python"],
        "-I",
        str(host_change.root / "host/desktop_scheduler.py"),
        "schedule-set",
        "--directory",
        str(host_change.root),
        "20:00",
        "--today",
    ]
