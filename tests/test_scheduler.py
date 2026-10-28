"""Network-free daily acceptance with committed SQLite, fakes and injected clocks."""

import json
import os
import plistlib
import socket
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from ask_every_day.domain import Publication, PublicationStatus, QueueEntry
from ask_every_day.instagram_auth import AuthError
from ask_every_day.instagram_client import (
    Container,
    ContainerStatus,
    PublishedMedia,
    TransportError,
)
from ask_every_day.publication_service import PublicationService
from ask_every_day.publish_next import PublishError
from ask_every_day.publish_next_cli import publication_lock
from ask_every_day.repository import open_repository
from ask_every_day.scheduler import run_daily
from ask_every_day.scheduler_launchd import launch_agent
from ask_every_day.scheduler_policy import (
    LONDON,
    ApprovedInputs,
    ScheduleConfig,
    ScheduleError,
    authorization_preview,
    daily_block,
    load_schedule,
    read_authorization,
    save_authorization,
    state_path,
    validate_authorization,
)
from tests.test_publish_next import NOW, PRIVATE
from tests.test_publish_next import run as run


@pytest.fixture(autouse=True)
def synthetic_renderer_boundary(monkeypatch):
    # Scheduler behavior tests inject a synthetic renderer; native tests verify fonts.
    monkeypatch.setattr(
        "ask_every_day.scheduler.verify_editorial_fonts", Mock(return_value=[])
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        socket,
        "create_connection",
        Mock(side_effect=AssertionError("network forbidden")),
    )


@pytest.fixture
def inputs(run):
    return ApprovedInputs(tuple(run.repo.list_posts()), run.catalog, "a" * 64)


@pytest.fixture
def schedule():
    return ScheduleConfig(hour=21, minute=0)


def authorize(run, inputs, schedule, at=NOW):
    with publication_lock(run.config.database_path):
        value = authorization_preview(run.repo, run.config, inputs, schedule, at)
        save_authorization(run.config, value)
    return value


def execute(run, inputs, schedule, *, now=NOW, **kw):
    return run_daily(
        run.config,
        inputs,
        schedule,
        lambda: run.credentials,
        clock=kw.pop("clock", lambda: now),
        client_factory=lambda _: run.client,
        stage=kw.pop("stage", run.stage),
        sleep=Mock(),
        **kw,
    )


def test_default_disabled_has_no_render_credential_staging_or_provider_effects(
    run, inputs, schedule
):
    before = run.config.database_path.read_bytes()
    assert execute(run, inputs, schedule).outcome == "disabled"
    assert not state_path(run.config).exists()
    assert run.credentials.mock_calls == run.client.mock_calls == []
    assert run.events == [] and run.config.database_path.read_bytes() == before


def test_one_headless_success_then_repeat_and_restart_are_noops(run, inputs, schedule):
    authorize(run, inputs, schedule)
    summary = execute(run, inputs, schedule)
    assert summary.outcome == "published" and summary.attempt_id == 1
    assert run.events == ["open", "closed"]
    assert (
        run.client.create_image_container.call_count
        == run.client.publish_container.call_count
        == 1
    )
    assert execute(run, inputs, schedule).outcome == "already_published_today"
    with open_repository(run.config, read_only=True) as fresh:
        row = fresh.get_publication(1)
        assert row is not None and row.publication.status is PublicationStatus.PUBLISHED
        checkpoint = fresh.get_checkpoint(1)
        assert checkpoint is not None and checkpoint.phase.value == "completed"
    code = """from pathlib import Path
from ask_every_day.domain import AppConfig
from ask_every_day.repository import open_repository
from ask_every_day.scheduler_policy import daily_block,read_authorization
from datetime import datetime
c=AppConfig(Path(__import__('sys').argv[1]),Path(__import__('sys').argv[2]),'@ask.every.day')
with open_repository(c,read_only=True) as r:
 assert read_authorization(c).enabled
 assert daily_block(r.list_publications(),datetime.fromisoformat(__import__('sys').argv[3]))=='already_published_today'
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(run.config.database_path),
            str(run.config.assets_path),
            NOW.isoformat(),
        ],
        check=True,
        capture_output=True,
    )


def test_successful_consumption_preserves_policy_and_advances_palette_next_day(
    run, inputs, schedule
):
    authorization = authorize(run, inputs, schedule)
    execute(run, inputs, schedule)
    first = run.preview()
    assert first is not None and first.palette_sequence == 2
    run.client.create_image_container.return_value = Container("106")
    run.client.publish_container.return_value = PublishedMedia("107")
    assert (
        execute(run, inputs, schedule, now=NOW + timedelta(days=1)).outcome
        == "published"
    )
    assert read_authorization(run.config) == authorization
    assert run.repo.list_queue() == []
    with sqlite3.connect(run.config.database_path) as c:
        assert c.execute(
            "SELECT COUNT(*) FROM publications WHERE status='Published'"
        ).fetchone() == (2,)
    palette = json.loads(
        run.config.database_path.with_name("db.sqlite.palette.json").read_text()
    )
    colours = [
        value["assignment"]["bag"][value["assignment"]["slot"]]
        for value in palette["assets"].values()
    ]
    assert len(set(colours)) == 2


def test_manual_success_counts_even_when_authorized_after_it(run, inputs, schedule):
    run.publish()
    authorize(run, inputs, schedule)
    assert execute(run, inputs, schedule).outcome == "already_published_today"
    assert run.client.publish_container.call_count == 1


def test_manual_success_consumption_is_an_allowed_prefix(run, inputs, schedule):
    authorization = authorize(run, inputs, schedule)
    run.publish()
    validate_authorization(run.repo, run.config, inputs, schedule, authorization, NOW)
    assert execute(run, inputs, schedule).outcome == "already_published_today"


@pytest.mark.parametrize(
    "change",
    [
        "queue_order",
        "queue_position",
        "remove_queue",
        "posts",
        "copy",
        "sources",
        "palette",
        "renderer",
        "runtime",
        "rendering",
        "schedule",
    ],
)
def test_drift_requires_new_authorization_without_remote_effects(
    run, inputs, schedule, monkeypatch, change
):
    authorize(run, inputs, schedule)
    cfg = run.config
    if change == "queue_order":
        queue = run.repo.list_queue()
        run.repo.replace_queue(
            [QueueEntry(queue[1].post_id, 1), QueueEntry(queue[0].post_id, 2)]
        )
    elif change == "queue_position":
        run.repo.replace_queue(
            [replace(e, position=e.position + 1) for e in run.repo.list_queue()]
        )
    elif change == "remove_queue":
        run.repo.remove_queue_entry(run.repo.list_queue()[0].post_id)
    elif change == "posts":
        post = run.repo.list_posts()[0]
        run.repo.update_post(replace(post, prompt="Changed approved content?"))
    elif change == "copy":
        inputs = replace(inputs, catalog=replace(inputs.catalog, captions=[]))
    elif change == "sources":
        inputs = replace(inputs, source_key="b" * 64)
    elif change == "palette":
        cfg = replace(cfg, palette=replace(cfg.palette, seed=99))
    elif change == "renderer":
        cfg = replace(cfg, renderer=replace(cfg.renderer, width=1000))
    elif change == "runtime":
        monkeypatch.setattr(
            "ask_every_day.scheduler_policy.runtime_key", lambda: "b" * 64
        )
    elif change == "rendering":
        monkeypatch.setattr(
            "ask_every_day.scheduler_policy.rendering_key", lambda _: "b" * 64
        )
    else:
        schedule = replace(schedule, minute=1)
    with pytest.raises(ScheduleError):
        run_daily(
            cfg,
            inputs,
            schedule,
            lambda: run.credentials,
            clock=lambda: NOW,
            client_factory=lambda _: run.client,
            stage=run.stage,
        )
    assert run.client.mock_calls == run.credentials.mock_calls == []
    assert run.events == []


@pytest.mark.parametrize("state", ["expired", "future", "disabled"])
def test_inactive_authorization_never_dispatches(run, inputs, schedule, state):
    value = authorize(run, inputs, schedule)
    if state == "expired":
        value = replace(
            value,
            authorized_at=NOW - timedelta(days=2),
            expires_at=NOW - timedelta(days=1),
        )
    elif state == "future":
        value = replace(
            value,
            authorized_at=NOW + timedelta(days=1),
            expires_at=NOW + timedelta(days=2),
        )
    else:
        value = replace(value, enabled=False)
    save_authorization(run.config, value)
    if state == "disabled":
        assert execute(run, inputs, schedule).outcome == "disabled"
    else:
        with pytest.raises(ScheduleError, match="authorization_inactive"):
            execute(run, inputs, schedule)
    assert run.client.mock_calls == run.credentials.mock_calls == []


def test_outside_window_does_not_load_credential_or_render(run, inputs, schedule):
    authorize(run, inputs, schedule)
    assert (
        execute(run, inputs, schedule, now=NOW + timedelta(hours=1)).outcome
        == "outside_window"
    )
    assert run.credentials.mock_calls == run.client.mock_calls == []


@pytest.mark.parametrize(
    "failure",
    [
        "missing_credential",
        "staging",
        "ambiguous_create",
        "ambiguous_publish",
        "terminal_container",
    ],
)
def test_failures_never_retry_remote_posts_or_consume_palette_slot(
    run, inputs, schedule, failure
):
    authorize(run, inputs, schedule)
    stage = run.stage
    if failure == "missing_credential":
        run.credentials.get_valid_credential.side_effect = AuthError(
            "missing_credential"
        )
    elif failure == "staging":
        stage = Mock(side_effect=OSError(PRIVATE))
    elif failure == "ambiguous_create":
        run.client.create_image_container.side_effect = TransportError(
            "create_image_container", None, True
        )
    elif failure == "ambiguous_publish":
        run.client.publish_container.side_effect = TransportError(
            "publish_container", None, True
        )
    else:
        run.client.container_status.return_value = ContainerStatus.ERROR
    with pytest.raises(PublishError) as caught:
        execute(run, inputs, schedule, stage=stage)
    assert PRIVATE not in str(caught.value)
    creates = run.client.create_image_container.call_count
    publishes = run.client.publish_container.call_count
    if failure in ("ambiguous_create", "ambiguous_publish", "terminal_container"):
        with pytest.raises((ScheduleError, PublishError)):
            execute(run, inputs, schedule)
        assert run.client.create_image_container.call_count == creates
        assert run.client.publish_container.call_count == publishes
    assert not any(
        r.publication.status is PublicationStatus.PUBLISHED
        for r in run.repo.list_publications()
    )
    assert run.client.publish_container.call_count <= 1


def test_legacy_unresolved_attempt_blocks_even_new_day(run, inputs, schedule):
    attempt = PublicationService(run.repo).begin_publish(
        run.repo.list_queue()[0].post_id, NOW
    )
    with pytest.raises(Exception):
        authorize(run, inputs, schedule)
    assert attempt.id == 1 and run.client.mock_calls == []


def test_window_closing_during_polling_leaves_known_container_unresolved(
    run, inputs, schedule
):
    authorize(run, inputs, schedule)
    current = [NOW]

    def status(_):
        current[0] += timedelta(hours=1)
        return ContainerStatus.FINISHED

    run.client.container_status.side_effect = status
    with pytest.raises(PublishError):
        execute(run, inputs, schedule, clock=lambda: current[0])
    run.client.publish_container.assert_not_called()
    checkpoint = run.repo.get_checkpoint(1)
    assert checkpoint is not None and checkpoint.phase.value == "container_known"
    with pytest.raises(ScheduleError):
        execute(run, inputs, schedule, now=NOW + timedelta(days=1))
    assert run.client.create_image_container.call_count == 1


def test_window_closing_during_staging_does_not_begin_attempt(run, inputs, schedule):
    from contextlib import contextmanager

    authorize(run, inputs, schedule)
    current = [NOW]

    @contextmanager
    def stage(*a, **kw):
        with run.stage(*a, **kw) as media:
            current[0] += timedelta(hours=1)
            yield media

    with pytest.raises(PublishError):
        execute(run, inputs, schedule, stage=stage, clock=lambda: current[0])
    assert run.repo.list_publications() == []
    run.client.create_image_container.assert_not_called()


def test_repeated_dst_fold_and_spring_gap_obey_london_day_and_window():
    config = ScheduleConfig(hour=1, minute=30, window_minutes=20)
    first = datetime(2026, 10, 25, 0, 35, tzinfo=UTC)
    second = first + timedelta(hours=1)
    assert first.astimezone(LONDON).fold == 0 and second.astimezone(LONDON).fold == 1
    assert config.in_window(first) and config.in_window(second)
    from ask_every_day.repository import PublicationRecord

    row = PublicationRecord(
        1, Publication("AED-0001", PublicationStatus.PUBLISHED, first, first, "123")
    )
    assert daily_block([row], second) == "already_published_today"
    assert not config.in_window(datetime(2026, 3, 29, 0, 35, tzinfo=UTC))
    assert not config.in_window(datetime(2026, 3, 29, 1, 35, tzinfo=UTC))


def test_manual_publish_crossing_midnight_counts_published_date():
    from ask_every_day.repository import PublicationRecord

    attempted = datetime(2026, 10, 26, 23, 59, tzinfo=UTC)
    published = attempted + timedelta(minutes=2)
    row = PublicationRecord(
        1,
        Publication(
            "AED-0001", PublicationStatus.PUBLISHED, attempted, published, "123"
        ),
    )
    assert daily_block([row], published) == "already_published_today"


@pytest.mark.parametrize(
    "values",
    [
        {"hour": True},
        {"hour": 24},
        {"minute": -1},
        {"window_minutes": 0},
        {"window_minutes": 121},
        {"hour": 23, "minute": 59, "window_minutes": 2},
        {"timezone": "UTC"},
        {"authorization_days": 0},
        {"authorization_days": 367},
        {"unknown": 1},
    ],
)
def test_invalid_schedule_configuration(values, tmp_path):
    path = tmp_path / "schedule.toml"
    path.write_text(
        "\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items())
    )
    with pytest.raises(ScheduleError, match="schedule_configuration"):
        load_schedule(path)


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "permissions",
        "duplicate",
        "version",
        "truncated",
        "extra",
        "bad_history",
    ],
)
def test_corrupt_authorization_fails_closed(run, inputs, schedule, kind, tmp_path):
    authorize(run, inputs, schedule)
    path = state_path(run.config)
    if kind == "symlink":
        other = tmp_path / "other.json"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    elif kind == "permissions":
        path.chmod(0o644)
    elif kind == "duplicate":
        path.write_text('{"version":1,"version":1}')
    elif kind == "truncated":
        path.write_text("{")
    else:
        data = json.loads(path.read_text())
        if kind == "version":
            data["version"] = 2
        elif kind == "extra":
            data["secret"] = PRIVATE
        else:
            data["history"] = [[1]]
        path.write_text(json.dumps(data))
    with pytest.raises(ScheduleError, match="authorization_invalid"):
        execute(run, inputs, schedule)
    assert run.client.mock_calls == []


def test_authorization_write_failure_preserves_previous_complete_policy(
    run, inputs, schedule, monkeypatch
):
    value = authorize(run, inputs, schedule)
    path = state_path(run.config)
    before = path.read_bytes()
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError(PRIVATE)))
    with pytest.raises(OSError):
        save_authorization(run.config, replace(value, enabled=False))
    assert path.read_bytes() == before
    assert list(path.parent.glob(".aed-schedule-*")) == []


def test_independent_process_cannot_run_while_manual_lock_is_held(
    run, inputs, schedule
):
    authorize(run, inputs, schedule)
    code = """from pathlib import Path
from ask_every_day.publish_next_cli import publication_lock
from ask_every_day.publish_next import PublishError
try:
 with publication_lock(Path(__import__('sys').argv[1])): raise AssertionError('lock bypass')
except PublishError as e:
 assert e.reason=='another_publisher_running'
"""
    with publication_lock(run.config.database_path):
        subprocess.run(
            [sys.executable, "-c", code, str(run.config.database_path)],
            check=True,
            capture_output=True,
        )
    assert run.client.mock_calls == []


def test_launchagent_is_inactive_absolute_controlled_and_timezone_independent(tmp_path):
    python = Path(sys.executable).absolute()
    payload = plistlib.loads(
        launch_agent(
            python,
            tmp_path,
            tmp_path / "aed.toml",
            tmp_path / "schedule.toml",
            tmp_path / "auth.toml",
            tmp_path / "data",
            tmp_path / "scheduler.jsonl",
            ScheduleConfig(),
        )
    )
    assert payload["StartInterval"] == 300
    assert "RunAtLoad" not in payload and "KeepAlive" not in payload
    assert payload["ProgramArguments"][:4] == [
        "/bin/sh",
        str(tmp_path / "scripts/with-cairo.sh"),
        str(python),
        "-m",
    ]
    assert payload["WorkingDirectory"] == str(tmp_path)
    assert payload["EnvironmentVariables"]["TZ"] == "Europe/London"
    assert "StandardOutPath" not in payload and "StandardErrorPath" not in payload
    assert PRIVATE not in str(payload)


def test_source_reload_drift_during_staging_blocks_post(run, inputs, schedule):
    from contextlib import contextmanager

    authorize(run, inputs, schedule)
    current = [inputs]

    @contextmanager
    def stage(*a, **kw):
        with run.stage(*a, **kw) as media:
            current[0] = replace(inputs, source_key="b" * 64)
            yield media

    with pytest.raises(PublishError, match="policy_reapproval_required"):
        execute(
            run,
            inputs,
            schedule,
            stage=stage,
            reload_policy=lambda: (run.config, current[0], schedule),
        )
    assert run.repo.list_publications() == []
    run.client.create_image_container.assert_not_called()


def test_clock_reversal_fails_closed():
    from ask_every_day.repository import PublicationRecord

    future = NOW + timedelta(days=1)
    row = PublicationRecord(
        1,
        Publication(
            "AED-0001", PublicationStatus.FAILED, future, error="container_error"
        ),
    )
    with pytest.raises(ScheduleError, match="clock_precedes_publication_history"):
        daily_block([row], NOW)


def test_launchagent_cadence_is_shorter_than_narrow_execution_window(tmp_path):
    payload = plistlib.loads(
        launch_agent(
            Path(sys.executable).absolute(),
            tmp_path,
            tmp_path / "aed.toml",
            tmp_path / "schedule.toml",
            tmp_path / "auth.toml",
            tmp_path / "data",
            tmp_path / "scheduler.jsonl",
            ScheduleConfig(window_minutes=1),
        )
    )
    assert payload["StartInterval"] == 30


def test_two_full_scheduled_invocations_share_the_publication_lock(
    run, inputs, schedule
):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event

    from ask_every_day.media_staging import StagedMedia
    from tests.test_publish_next import URL

    authorize(run, inputs, schedule)
    ready, release = Event(), Event()

    @contextmanager
    def stage(repository, config, post_id, *, expected_sha256=None):
        ready.set()
        assert release.wait(5)
        yield StagedMedia(post_id, URL)

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(execute, run, inputs, schedule, stage=stage)
        try:
            assert ready.wait(5)
            with pytest.raises(PublishError, match="another_publisher_running"):
                execute(run, inputs, schedule, stage=stage)
        finally:
            release.set()
        assert first.result(timeout=10).outcome == "published"
    assert run.client.create_image_container.call_count == 1
    assert run.client.publish_container.call_count == 1
    assert len(run.repo.list_publications()) == 1
