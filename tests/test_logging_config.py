"""Operational JSON schema, safe context and entrypoint stream contracts."""

import io
import json
import logging
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ask_every_day.config import load_config
from ask_every_day.logging_config import JSONFormatter, configure_logging


@pytest.fixture
def application_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger("ask_every_day")
    handlers = logger.handlers[:]
    level, propagate = logger.level, logger.propagate
    logger.handlers = []
    yield logger
    for handler in logger.handlers:
        handler.close()
    logger.handlers = handlers
    logger.setLevel(level)
    logger.propagate = propagate


def record(**extra: object) -> logging.LogRecord:
    result = logging.LogRecord(
        "ask_every_day.example",
        logging.WARNING,
        "",
        0,
        "private message %s",
        ("access-token",),
        None,
    )
    result.created = 1_700_000_000.125
    result.__dict__.update(extra)
    return result


def test_schema_timestamp_context_and_no_message() -> None:
    output = JSONFormatter().format(
        record(
            event="publication_transition_failed",
            post_id="AED-0001",
            publication_id=2,
            error_type="RuntimeError",
            previous_state="Publishing",
            new_state="Failed",
        )
    )
    parsed = json.loads(output)
    assert datetime.fromisoformat(parsed["timestamp"]) == datetime.fromtimestamp(
        1_700_000_000.125, UTC
    )
    assert parsed == {
        "timestamp": "2023-11-14T22:13:20.125Z",
        "level": "WARNING",
        "logger": "ask_every_day.example",
        "event": "publication_transition_failed",
        "post_id": "AED-0001",
        "publication_id": 2,
        "error_type": "RuntimeError",
        "previous_state": "Publishing",
        "new_state": "Failed",
    }
    assert "private" not in output and "access-token" not in output
    assert "\n" not in output


def test_untrusted_fields_and_objects_are_not_serialized() -> None:
    class Dangerous:
        def __str__(self) -> str:
            raise AssertionError("Arbitrary object was serialized")

    event = record(
        event="content_imported",
        prompt="full prompt body",
        highlight_text="private highlight",
        access_token="secret",
        authorization="Bearer secret",
        response={"secret": 1},
        image=b"binary",
        width=Dangerous(),
        output_path=Dangerous(),
        args=(Dangerous(),),
    )
    event.exc_info = (RuntimeError, RuntimeError("provider-token"), None)
    event.stack_info = "secret stack"
    output = JSONFormatter().format(event)
    assert set(json.loads(output)) == {"timestamp", "level", "logger", "event"}
    for value in ("prompt", "highlight", "secret", "binary", "provider", "stack"):
        assert value not in output


@pytest.mark.parametrize("event", [None, {}, "whole prompt with spaces?", "a" * 81])
def test_missing_or_unsafe_event_uses_fixed_fallback(event: object) -> None:
    assert (
        json.loads(JSONFormatter().format(record(event=event)))["event"] == "log_record"
    )


def test_scalar_context_types_are_explicit() -> None:
    parsed = json.loads(
        JSONFormatter().format(
            record(
                event="queue_generated",
                seed=None,
                post_count=400,
                attempts=2,
                category_min_gap=3,
                subcategory_min_gap=5,
                width="unsafe-number",
                height=True,
                template="Classic",
                source_path="a" * 1025,
            )
        )
    )
    assert parsed["seed"] is None
    assert parsed["post_count"] == 400
    assert parsed["attempts"] == 2
    assert (
        "width" not in parsed and "height" not in parsed and "source_path" not in parsed
    )


def test_stderr_repeat_configuration_and_level(
    application_logger: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    root = logging.getLogger()
    root_handlers = root.handlers[:]
    configure_logging()
    configure_logging()
    assert len(application_logger.handlers) == 1
    assert root.handlers == root_handlers
    child = logging.getLogger("ask_every_day.example")
    child.info(
        "not the event", extra={"event": "config_loaded", "config_path": "aed.toml"}
    )
    captured = capsys.readouterr()
    assert not captured.out
    assert len(captured.err.splitlines()) == 1
    assert json.loads(captured.err)["event"] == "config_loaded"
    configure_logging("ERROR")
    child.info("ignored", extra={"event": "config_loaded"})
    assert not capsys.readouterr().err


def test_existing_application_handler_is_preserved(
    application_logger: logging.Logger,
) -> None:
    original = logging.StreamHandler(io.StringIO())
    application_logger.addHandler(original)
    configure_logging()
    assert original in application_logger.handlers
    assert len(application_logger.handlers) == 2


def test_library_import_does_not_configure_loggers(tmp_path: Path) -> None:
    code = """import logging
root = logging.getLogger()
app = logging.getLogger('ask_every_day')
before = (root.handlers[:], root.level, app.handlers[:], app.level, app.propagate)
import ask_every_day.logging_config
import ask_every_day.content_import
import ask_every_day.classic_prototype
assert before == (root.handlers[:], root.level, app.handlers[:], app.level, app.propagate)
"""
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True)


def test_real_configuration_event(
    application_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config = tmp_path / "aed.toml"
    config.write_text(
        'database_path = "aed.sqlite3"\nassets_path = "assets"\naccount_handle = "@ask.every.day"\n'
    )
    configure_logging()
    load_config(config)
    event = json.loads(capsys.readouterr().err)
    assert event["event"] == "config_loaded"
    assert event["config_path"] == str(config)


def test_prototype_cli_emits_only_json_logs(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ask_every_day.classic_prototype"],
        cwd=tmp_path,
        check=True,
        text=True,
        capture_output=True,
    )
    assert not result.stdout
    events = [json.loads(line) for line in result.stderr.splitlines()]
    assert [event["event"] for event in events] == [
        "svg_rendered",
        "classic_prototype_written",
    ]
    assert events[-1]["output_path"] == "output/classic-prototype.svg"
    assert all("timestamp" in event for event in events)


def test_import_cli_separates_functional_stdout_from_logs(tmp_path: Path) -> None:
    config = tmp_path / "aed.toml"
    config.write_text(
        'database_path = "aed.sqlite3"\nassets_path = "assets"\naccount_handle = "@ask.every.day"\n'
    )
    data = Path(__file__).parents[1] / "data"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ask_every_day.content_import",
            "--config",
            str(config),
            "--source",
            str(data / "prompts.csv"),
            "--taxonomy",
            str(data / "taxonomy.csv"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "Imported 400; unchanged 0\n"
    events = [json.loads(line) for line in result.stderr.splitlines()]
    imported = next(event for event in events if event["event"] == "content_imported")
    assert imported["imported_count"] == 400 and imported["unchanged_count"] == 0
    with (data / "prompts.csv").open() as stream:
        first_prompt = stream.readlines()[1].split(",")[1]
    assert first_prompt not in result.stderr


def test_prototype_failure_logs_safe_context(tmp_path: Path) -> None:
    (tmp_path / "output").write_text("not a directory")
    result = subprocess.run(
        [sys.executable, "-m", "ask_every_day.classic_prototype"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert not result.stdout
    event = json.loads(result.stderr)
    assert event["event"] == "classic_prototype_failed"
    assert event["error_type"] == "FileExistsError"
    assert event["post_id"] == "AED-0001"
    assert "not a directory" not in result.stderr


def test_import_failure_log_has_safe_type(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ask_every_day.content_import",
            "--source",
            str(tmp_path / "absent.csv"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    lines = result.stderr.splitlines()
    failure = json.loads(lines[0])
    assert failure["event"] == "content_import_failed"
    assert failure["error_type"] == "FileNotFoundError"
    assert "timestamp" in failure
    assert lines[1].startswith("Import failed")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "１２３",
        "104secret",
        "https://example.test/capability",
        "1" * 129,
        104,
        True,
        {"token": "private"},
    ],
)
def test_provider_evidence_ids_accept_only_bounded_ascii_digits(value):
    output = JSONFormatter().format(
        record(event="scheduler_command_failed", container_id=value, media_id=value)
    )
    result = json.loads(output)
    assert "container_id" not in result and "media_id" not in result
    assert "private" not in output and "capability" not in output


def test_provider_evidence_ids_are_safe_operational_context():
    result = json.loads(
        JSONFormatter().format(
            record(event="scheduler_command_failed", container_id="104", media_id="105")
        )
    )
    assert result["container_id"] == "104" and result["media_id"] == "105"
