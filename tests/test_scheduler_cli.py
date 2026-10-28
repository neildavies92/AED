"""Process-boundary opt-in, credential injection, revocation and bounded logs."""

import json
import logging
import os
import stat
import sys
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from ask_every_day import scheduler_cli as cli
from ask_every_day.scheduler import run_daily
from ask_every_day.scheduler_policy import (
    authorization_preview,
    read_authorization,
    state_path,
)
from tests.test_publish_next import NOW, PRIVATE
from tests.test_publish_next import run as run
from tests.test_scheduler import inputs as inputs
from tests.test_scheduler import schedule as schedule


@pytest.fixture(autouse=True)
def synthetic_renderer_boundary(monkeypatch):
    # Scheduler behavior tests inject a synthetic renderer; native tests verify fonts.
    monkeypatch.setattr(
        "ask_every_day.scheduler.verify_editorial_fonts", Mock(return_value=[])
    )


@pytest.fixture
def command(run, inputs, schedule, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)

    monkeypatch.setattr(cli, "datetime", Clock)
    config = Mock(return_value=run.config)
    load_inputs = Mock(return_value=inputs)
    load_schedule = Mock(return_value=schedule)
    monkeypatch.setattr(cli, "load_config", config)
    monkeypatch.setattr(cli, "load_inputs", load_inputs)
    monkeypatch.setattr(cli, "load_schedule", load_schedule)
    monkeypatch.setattr(cli, "configure_logging", Mock())
    factory = Mock(return_value=run.credentials)
    monkeypatch.setattr(
        cli,
        "run_daily",
        lambda c, i, s, f, **kwargs: run_daily(
            c,
            i,
            s,
            f,
            clock=lambda: NOW,
            client_factory=lambda _: run.client,
            stage=run.stage,
            sleep=Mock(),
            **kwargs,
        ),
    )

    class Command:
        def __init__(self):
            self.factory = factory
            self.inputs = load_inputs
            self.schedule = load_schedule
            self.config = config

        def __call__(self, *args, tty=False, answer=None):
            monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
            monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
            if answer is not None:
                monkeypatch.setattr("builtins.input", answer)
            cli.main(args, credential_factory=factory)

    return Command()


def answer_for(run, inputs, schedule):
    return authorization_preview(run.repo, run.config, inputs, schedule, NOW).challenge


def test_default_cli_disabled_and_status_are_offline_without_policy_config(
    command, capsys
):
    command("run")
    assert json.loads(capsys.readouterr().out)["outcome"] == "disabled"
    command("status")
    assert json.loads(capsys.readouterr().out)["enabled"] is False
    command.factory.assert_not_called()
    command.inputs.assert_not_called()
    command.schedule.assert_not_called()


def test_enable_requires_tty_before_reading_config(command, capsys, run):
    with pytest.raises(SystemExit) as caught:
        command("enable")
    assert caught.value.code == 1
    assert "interactive_authorization_required" in capsys.readouterr().err
    command.config.assert_not_called()
    assert not state_path(run.config).exists()


@pytest.mark.parametrize("answer", ["yes", "", None])
def test_bad_or_cancelled_opt_in_does_not_authorize(command, run, capsys, answer):
    def respond(_):
        if answer is None:
            raise KeyboardInterrupt
        return answer

    with pytest.raises(SystemExit):
        command("enable", tty=True, answer=respond)
    assert not state_path(run.config).exists()
    assert PRIVATE not in capsys.readouterr().err
    command.factory.assert_not_called()


def test_exact_opt_in_headless_run_and_revocation(
    command, run, inputs, schedule, capsys
):
    expected = answer_for(run, inputs, schedule)
    command("enable", tty=True, answer=lambda _: expected)
    saved = read_authorization(run.config)
    assert saved is not None and saved.enabled
    assert "authorization_enabled" in capsys.readouterr().out
    command("run")
    assert json.loads(capsys.readouterr().out)["outcome"] == "published"
    command("run")
    assert json.loads(capsys.readouterr().out)["outcome"] == "already_published_today"
    assert run.client.publish_container.call_count == 1
    command("disable")
    assert json.loads(capsys.readouterr().out)["enabled"] is False
    command("status")
    assert json.loads(capsys.readouterr().out)["authorization_valid"] is False
    command("run")
    assert json.loads(capsys.readouterr().out)["outcome"] == "disabled"
    assert run.client.publish_container.call_count == 1


def test_preview_neither_authorizes_nor_loads_credentials(command, run, capsys):
    before = run.config.database_path.read_bytes()
    command("preview")
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "local_read_only" and not data["enabled"]
    assert data["timezone"] == "Europe/London"
    assert "Question" not in str(data) and PRIVATE not in str(data)
    assert run.config.database_path.read_bytes() == before
    assert not state_path(run.config).exists()
    command.factory.assert_not_called()


def test_local_credential_check_never_refreshes_or_calls_provider(command, run, capsys):
    command("check-credentials")
    data = json.loads(capsys.readouterr().out)
    assert data["credential_ready"] and data["mode"] == "local_credential_check"
    assert PRIVATE not in str(data)
    run.credentials.local_credential.assert_called_once_with()
    run.credentials.get_valid_credential.assert_not_called()
    assert run.client.mock_calls == []
    command.config.assert_not_called()


def test_confirmation_rechecks_configuration_before_state_write(
    command, run, inputs, schedule
):
    from dataclasses import replace

    def drift(_):
        command.inputs.return_value = replace(inputs, source_key="b" * 64)
        return answer_for(run, inputs, schedule)

    with pytest.raises(SystemExit):
        command("enable", tty=True, answer=drift)
    assert not state_path(run.config).exists()


def test_bounded_log_rotation_retains_private_permissions(tmp_path):
    path = tmp_path / "schedule.jsonl"
    previous = os.umask(0o022)
    application = logging.getLogger("ask_every_day")
    saved_handlers = list(application.handlers)
    try:
        cli._bounded_log(path)
        handler = application.handlers[-1]
        assert isinstance(handler, cli._PrivateRotatingHandler)
        handler.maxBytes = 256
        for _ in range(40):
            handler.handle(
                logging.LogRecord(
                    "ask_every_day.scheduler", logging.INFO, "", 1, PRIVATE, (), None
                )
            )
        handler.close()
        files = list(tmp_path.iterdir())
        assert len(files) <= 4
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)
        assert all(PRIVATE not in p.read_text() for p in files)
    finally:
        application.handlers = saved_handlers
        os.umask(previous)


def test_bounded_log_rejects_symlink_or_public_file(tmp_path):
    other = tmp_path / "other"
    other.write_text(PRIVATE)
    link = tmp_path / "log"
    link.symlink_to(other)
    with pytest.raises(OSError):
        cli._bounded_log(link)
    assert other.read_text() == PRIVATE
    link.unlink()
    link.write_text("")
    link.chmod(0o644)
    with pytest.raises(Exception):
        cli._bounded_log(link)


def test_failure_preserves_only_safe_known_response_ids(
    command, run, inputs, schedule, capsys, monkeypatch
):
    from ask_every_day.publish_next import PublishError
    from tests.test_scheduler import authorize

    authorize(run, inputs, schedule)
    monkeypatch.setattr(
        cli,
        "run_daily",
        Mock(
            side_effect=PublishError(
                "record_success_failed",
                attempt_id=1,
                container_id="104",
                media_id="105",
            )
        ),
    )
    with pytest.raises(SystemExit):
        command("run")
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "blocked"
    assert (
        result["publication_id"] == 1
        and result["container_id"] == "104"
        and result["media_id"] == "105"
    )
    monkeypatch.setattr(
        cli,
        "run_daily",
        Mock(
            side_effect=PublishError(
                "record_success_failed",
                attempt_id=True,
                container_id=PRIVATE,
                media_id="https://example.test/private",
            )
        ),
    )
    with pytest.raises(SystemExit):
        command("run")
    result = json.loads(capsys.readouterr().out)
    assert (
        result["publication_id"] is result["container_id"] is result["media_id"] is None
    )
    assert PRIVATE not in str(result)


def test_bounded_log_rejects_fifo_without_blocking(tmp_path):
    path = tmp_path / "log"
    os.mkfifo(path, 0o600)
    with pytest.raises(OSError):
        cli._bounded_log(path)
