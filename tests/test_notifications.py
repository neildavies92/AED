"""Only synthetic webhooks and mocked network calls; no live Discord or Meta."""

import json
import logging
import os
import socket
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ask_every_day import container_runtime
from ask_every_day import discord_webhook as discord
from ask_every_day import notifications as n
from ask_every_day.logging_config import JSONFormatter
from ask_every_day.publication_assets import ledger_path
from ask_every_day.publish_next import PublishError
from ask_every_day.publish_next_cli import publication_lock
from tests.test_publish_next import NOW
from tests.test_publish_next import run as run

URL = "https://discord.com/api/webhooks/123456789/" + "SYNTHETIC_PRIVATE_TOKEN_" * 3
NOTICE = n.Notice(
    "notification_test",
    datetime(2026, 11, 2, tzinfo=UTC),
    "a" * 32,
    dedup_key="synthetic",
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        socket,
        "create_connection",
        Mock(side_effect=AssertionError("network forbidden")),
    )
    monkeypatch.setattr(
        n,
        "subprocess",
        SimpleNamespace(
            run=Mock(side_effect=AssertionError("worker forbidden")),
            PIPE=subprocess.PIPE,
            DEVNULL=subprocess.DEVNULL,
        ),
    )


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "credentials"
    root.mkdir(mode=0o700)
    result = n.NotificationStore(root)
    result.configure(URL)
    return result


@pytest.mark.parametrize(
    "url",
    [
        URL,
        URL.replace("/api/", "/api/v10/"),
        URL.replace("discord.com", "ptb.discord.com"),
        URL.replace("discord.com", "canary.discord.com"),
    ],
)
def test_supported_webhook_destinations(url):
    assert discord.validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("https:", "http:"),
        URL + "?wait=false",
        URL + "#fragment",
        URL.replace("discord.com", "discord.com.evil.test"),
        URL.replace("discord.com", "user@discord.com"),
        URL.replace("discord.com", "discord.com:443"),
        URL.replace("discord.com", "127.0.0.1"),
        URL.replace("/webhooks/", "/webhooks/%31/"),
        URL + "\n",
        "PRIVATE",
        None,
    ],
)
def test_invalid_destinations_never_echo_secret(url):
    with pytest.raises(ValueError, match="^invalid_discord_destination$"):
        discord.validate_url(url)


@pytest.mark.parametrize(
    "status,result",
    [
        (200, "delivered"),
        (204, "delivered"),
        (400, "rejected"),
        (401, "rejected"),
        (403, "rejected"),
        (404, "rejected"),
        (429, "rate_limited"),
        (500, "uncertain"),
        (503, "uncertain"),
        (302, "rejected"),
    ],
)
def test_status_classification_has_no_blind_retry(status, result):
    transport = Mock(return_value=(status, None))
    assert discord.deliver(URL, b"{}", transport=transport) == {
        "delivery_result": result,
        "http_status": status,
    }
    assert transport.call_count == 1


@pytest.mark.parametrize("delay", ["2", "999", "nan", "inf", "-1", "PRIVATE", None])
def test_rate_limit_outside_budget_is_not_retried(delay):
    transport, sleep = Mock(return_value=(429, delay)), Mock()
    assert (
        discord.deliver(URL, b"{}", transport=transport, sleep=sleep)["delivery_result"]
        == "rate_limited"
    )
    assert transport.call_count == 1
    sleep.assert_not_called()


def test_explicit_rate_limit_gets_at_most_one_bounded_retry():
    transport, sleep = Mock(side_effect=[(429, "0.25"), (204, None)]), Mock()
    assert (
        discord.deliver(URL, b"{}", transport=transport, sleep=sleep)["delivery_result"]
        == "delivered"
    )
    assert transport.call_count == 2
    sleep.assert_called_once_with(0.25)
    transport = Mock(return_value=(429, "0"))
    assert (
        discord.deliver(URL, b"{}", transport=transport, sleep=Mock())[
            "delivery_result"
        ]
        == "rate_limited"
    )
    assert transport.call_count == 2


@pytest.mark.parametrize("error", [TimeoutError(URL), ConnectionError(URL)])
def test_transport_exception_is_uncertain_and_redacted(error):
    transport = Mock(side_effect=error)
    result = discord.deliver(URL, b"{}", transport=transport)
    assert result == {"delivery_result": "uncertain", "http_status": None}
    assert "PRIVATE" not in str(result) and transport.call_count == 1


def test_native_transport_disables_proxies_redirects_and_waits_for_confirmation(
    monkeypatch,
):
    response = Mock()
    response.code = 204
    response.headers.get.return_value = None
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock()
    opener = Mock()
    opener.open.return_value = response
    build = Mock(return_value=opener)
    monkeypatch.setattr(discord, "build_opener", build)
    assert discord.request(URL, b"{}", 2) == (204, None)
    handlers = build.call_args.args
    assert handlers[0].proxies == {} and isinstance(handlers[1], discord.NoRedirects)
    assert (
        handlers[1].redirect_request(None, None, None, None, None, "https://evil.test")
        is None
    )
    request = opener.open.call_args.args[0]
    assert request.full_url == URL + "?wait=true" and request.method == "POST"
    assert opener.open.call_args.kwargs["timeout"] == 2
    response.read.assert_not_called()


def test_worker_budget_and_secret_channel(monkeypatch):
    execute = Mock(
        return_value=SimpleNamespace(
            returncode=0, stdout=b'{"delivery_result":"delivered","http_status":204}'
        )
    )
    monkeypatch.setattr(n.subprocess, "run", execute)
    assert n.discord_send(URL, NOTICE.payload())["delivery_result"] == "delivered"
    args, kwargs = execute.call_args
    assert "PRIVATE" not in repr(args) and "env" not in kwargs
    assert kwargs["timeout"] == 6 and kwargs["stderr"] == subprocess.DEVNULL
    assert json.loads(kwargs["input"])["url"] == URL
    execute.side_effect = subprocess.TimeoutExpired(["worker"], 6, stderr=URL)
    assert n.discord_send(URL, NOTICE.payload()) == {
        "delivery_result": "uncertain",
        "http_status": None,
    }


def test_payload_is_small_allowlisted_and_never_mentions():
    notice = replace(
        NOTICE,
        post_id="@everyone PRIVATE",
        checkpoint="PRIVATE",
        correlation_id="PRIVATE",
        category="PRIVATE",
        http_status="PRIVATE",
        meta_error_code="PRIVATE",
    )
    payload = notice.payload()
    text = json.dumps(payload)
    assert len(text) < 4096 and "PRIVATE" not in text and "@everyone" not in text
    assert payload["allowed_mentions"] == {
        "parse": [],
        "users": [],
        "roles": [],
        "replied_user": False,
    }
    assert "@ask.every.day" in text
    with pytest.raises(ValueError, match="payload_limit"):
        discord.deliver(URL, b"a" * 4097, transport=Mock())


def test_missing_disabled_and_invalid_config_do_not_send(store, tmp_path):
    sender = Mock()
    store.configure(None)
    assert n.send_notice(store, NOTICE, sender=sender)["delivery_result"] == "disabled"
    fresh = tmp_path / "fresh"
    fresh.mkdir(mode=0o700)
    assert (
        n.send_notice(n.NotificationStore(fresh), NOTICE, sender=sender)[
            "delivery_result"
        ]
        == "disabled"
    )
    store.backend.directory.chmod(0o755)
    assert (
        n.send_notice(store, NOTICE, sender=sender)["delivery_result"]
        == "configuration_error"
    )
    sender.assert_not_called()


@pytest.mark.parametrize(
    "result", ["delivered", "uncertain", "rejected", "rate_limited"]
)
def test_receipt_precedes_send_and_survives_recreation(store, result):
    def send(url, payload):
        raw = store.backend.read(store.service(NOTICE.dedup_key), "credential")
        assert json.loads(raw)["delivery_result"] == "admitted"
        return {"delivery_result": result, "http_status": None}

    sender = Mock(side_effect=send)
    assert n.send_notice(store, NOTICE, sender=sender)["delivery_result"] == result
    reopened = n.NotificationStore(store.backend.directory)
    assert (
        n.send_notice(reopened, NOTICE, sender=sender)["delivery_result"]
        == "duplicate_suppressed"
    )
    assert sender.call_count == 1
    assert all(
        p.stat().st_mode & 0o777 == 0o600 for p in store.backend.directory.iterdir()
    )


def test_interruption_after_admission_never_replays(store):
    assert store.reserve(NOTICE.dedup_key)
    sender = Mock()
    assert (
        n.send_notice(
            n.NotificationStore(store.backend.directory), NOTICE, sender=sender
        )["delivery_result"]
        == "duplicate_suppressed"
    )
    sender.assert_not_called()


def test_notification_lock_contention_never_blocks_or_sends(store):
    import fcntl

    path = store.backend.directory / "discord-notifications.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sender = Mock()
        assert (
            n.send_notice(store, NOTICE, sender=sender)["delivery_result"]
            == "configuration_error"
        )
        sender.assert_not_called()
    finally:
        os.close(fd)


def test_logs_and_receipts_never_contain_secret(store, caplog):
    with caplog.at_level(logging.INFO):
        result = n.send_notice(store, NOTICE, sender=Mock(side_effect=OSError(URL)))
    assert result["delivery_result"] == "uncertain"
    output = "".join(JSONFormatter().format(r) for r in caplog.records)
    assert "PRIVATE" not in output and URL not in output
    assert '"notification_event":"notification_test"' in output
    assert '"delivery_result":"uncertain"' in output
    assert '"correlation_id":' in output
    receipt = store.backend.read(store.service(NOTICE.dedup_key), "credential")
    assert b"PRIVATE" not in receipt


def reporter(run, store, monkeypatch, source="manual", sender=None):
    monkeypatch.setattr("ask_every_day.config.load_config", lambda _: run.config)
    return n.PublicationNotifier(
        Path("synthetic.toml"),
        store.backend.directory,
        source,
        sender=sender
        or Mock(return_value={"delivery_result": "delivered", "http_status": 204}),
    )


def test_success_requires_completed_checkpoint_and_survives_restart(
    run, store, monkeypatch
):
    result = run.publish()
    callback = reporter(run, store, monkeypatch)
    outcome = {
        "outcome": "published",
        "attempt_id": result.attempt_id,
        "post_id": result.post_id,
    }
    notice = callback.describe(outcome)
    assert notice.event == "publication_succeeded" and notice.checkpoint == "completed"
    assert notice.timestamp == NOW and notice.attempt_created == "yes"
    before = run.config.database_path.read_bytes(), ledger_path(run.config).read_bytes()
    callback(outcome)
    resumed = reporter(run, store, monkeypatch, source="scheduled")
    resumed(outcome)
    resumed.sender.assert_not_called()
    assert before == (
        run.config.database_path.read_bytes(),
        ledger_path(run.config).read_bytes(),
    )
    assert callback.sender.call_count == 1


def test_exit_success_without_durable_checkpoint_is_not_a_success_notice(
    run, store, monkeypatch
):
    callback = reporter(run, store, monkeypatch)
    for outcome in [
        {"outcome": "published"},
        {"outcome": "published", "attempt_id": 999},
    ]:
        assert callback.describe(outcome).event == "publication_reconciliation_required"


def test_preflight_and_uncertain_outcomes_use_existing_state(run, store, monkeypatch):
    callback = reporter(run, store, monkeypatch)
    notice = callback.describe(
        {
            "outcome": "blocked",
            "publication_state": "no_attempt",
            "post_id": "AED-0001",
            "diagnostics": {
                "failure_category": "authentication",
                "http_status": 401,
                "meta_error_code": 190,
                "message": URL,
            },
        }
    )
    assert notice.event == "publication_failed" and notice.attempt_created == "no"
    assert "PRIVATE" not in json.dumps(notice.payload())
    run.client.publish_container.side_effect = TimeoutError("PRIVATE")
    with pytest.raises(PublishError) as caught:
        run.publish()
    notice = callback.describe(
        {
            "attempt_id": caught.value.attempt_id,
            "publication_state": caught.value.publication_state,
        }
    )
    assert notice.event == "publication_reconciliation_required"
    assert notice.checkpoint == "publish_intent" and notice.attempt_created == "yes"
    assert "Never retry" in json.dumps(notice.payload())


def test_disabled_notifier_never_opens_database(store, monkeypatch):
    store.configure(None)
    reader = Mock(side_effect=AssertionError)
    monkeypatch.setattr("ask_every_day.repository.open_repository", reader)
    n.PublicationNotifier(Path("absent"), store.backend.directory, "scheduled")(
        {"outcome": "published"}
    )
    reader.assert_not_called()


def test_notification_test_cli_does_not_touch_instagram_or_publication_state(
    store, monkeypatch, capsys
):
    monkeypatch.setattr(container_runtime, "configure_logging", Mock())
    config, credentials = (
        Mock(side_effect=AssertionError),
        Mock(side_effect=AssertionError),
    )
    monkeypatch.setattr(container_runtime, "load_config", config)
    monkeypatch.setattr(container_runtime, "credential_factory", credentials)
    send = Mock(return_value={"delivery_result": "delivered", "http_status": 204})
    monkeypatch.setattr(container_runtime, "send_notice", send)
    container_runtime.main(
        ["notification-test", "--credential-dir", str(store.backend.directory)]
    )
    assert json.loads(capsys.readouterr().out)["delivery_result"] == "delivered"
    assert send.call_args.args[1].event == "notification_test"
    config.assert_not_called()
    credentials.assert_not_called()


def test_hidden_configuration_requires_tty_and_never_prints_url(
    store, monkeypatch, capsys
):
    monkeypatch.setattr(container_runtime, "configure_logging", Mock())
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    prompt = Mock(return_value=URL)
    monkeypatch.setattr(container_runtime.getpass, "getpass", prompt)
    with pytest.raises(SystemExit):
        container_runtime.main(
            ["notification-configure", "--credential-dir", str(store.backend.directory)]
        )
    prompt.assert_not_called()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    container_runtime.main(
        ["notification-configure", "--credential-dir", str(store.backend.directory)]
    )
    assert "PRIVATE" not in str(capsys.readouterr())
    assert store.destination() == URL


def test_success_notification_waits_until_cli_releases_publication_lock(
    run, store, monkeypatch, capsys
):
    from ask_every_day import publish_next_cli
    from tests.test_canonical_manual import setup_cli

    setup_cli(run, monkeypatch)
    monkeypatch.setattr(publish_next_cli, "verify_editorial_fonts", lambda: [])
    monkeypatch.setattr(publish_next_cli, "canonical_asset_valid", lambda *args: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: run.preview().challenge)
    original = publish_next_cli.publish_approved
    monkeypatch.setattr(
        publish_next_cli,
        "publish_approved",
        lambda *args, **kwargs: original(
            *args,
            **kwargs,
            client_factory=lambda _: run.client,
            stage=run.stage,
            clock=lambda: NOW,
            sleep=Mock(),
        ),
    )
    sent = []

    def send(url, payload):
        with publication_lock(run.config.database_path):
            sent.append(
                (
                    run.config.database_path.read_bytes(),
                    ledger_path(run.config).read_bytes(),
                )
            )
        assert "publication_succeeded" in json.dumps(payload)
        raise TimeoutError(URL)

    callback = reporter(run, store, monkeypatch, sender=send)
    publish_next_cli.main(
        ["--confirm"],
        credential_factory=lambda _: run.credentials,
        canonical_assets=True,
        outcome_callback=callback,
    )
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["state"] == "Published"
    assert sent == [
        (run.config.database_path.read_bytes(), ledger_path(run.config).read_bytes())
    ]
    assert run.repo.get_checkpoint(result["attempt_id"]).phase.value == "completed"
    assert run.client.publish_container.call_count == 1


def test_container_preflight_reports_local_infrastructure(store, monkeypatch):
    monkeypatch.setattr(container_runtime, "configure_logging", Mock())
    monkeypatch.setattr(
        "ask_every_day.canonical_renderer.verify_editorial_fonts",
        Mock(side_effect=ValueError("PRIVATE")),
    )
    notify = Mock()
    monkeypatch.setattr(
        container_runtime, "PublicationNotifier", Mock(return_value=notify)
    )
    with pytest.raises(SystemExit):
        container_runtime.main(
            ["schedule-run", "--credential-dir", str(store.backend.directory)]
        )
    notify.assert_called_once_with(
        {"outcome": "blocked", "publication_state": "no_attempt"}
    )


def test_durable_success_is_not_downgraded_by_cleanup_failure(run, store, monkeypatch):
    result = run.publish()
    notice = reporter(run, store, monkeypatch).describe(
        {
            "outcome": "blocked",
            "publication_state": "reconciliation_required",
            "attempt_id": result.attempt_id,
        }
    )
    assert notice.event == "publication_succeeded"


def test_symlink_configuration_is_rejected_without_reading_target(store, tmp_path):
    target = tmp_path / "secret"
    target.write_text("PRIVATE")
    config = store.backend.directory / store.backend._name(n._SERVICE, "credential")
    config.unlink()
    config.symlink_to(target)
    sender = Mock()
    assert (
        n.send_notice(store, NOTICE, sender=sender)["delivery_result"]
        == "configuration_error"
    )
    sender.assert_not_called()
    assert target.read_text() == "PRIVATE"


def test_payload_london_timestamp_changes_with_dst():
    summer = replace(NOTICE, timestamp=datetime(2027, 7, 1, 18, tzinfo=UTC))
    fields = {f["name"]: f["value"] for f in summer.payload()["embeds"][0]["fields"]}
    assert fields["London time"] == "2027-07-01T19:00:00+01:00"


def test_known_terminal_provider_failure_is_not_labelled_uncertain(
    run, store, monkeypatch
):
    from ask_every_day.instagram_client import ContainerStatus

    run.client.container_status.return_value = ContainerStatus.ERROR
    with pytest.raises(PublishError) as caught:
        run.publish()
    notice = reporter(run, store, monkeypatch).describe(
        {
            "attempt_id": caught.value.attempt_id,
            "publication_state": caught.value.publication_state,
        }
    )
    assert notice.event == "publication_failed" and notice.checkpoint == "closed"
    assert notice.category == "api_rejected"
    assert notice.attempt_created == "yes"
    run.client.publish_container.assert_not_called()


def test_notification_failures_and_invalid_config_cannot_escape_callback(
    store, monkeypatch, caplog
):
    store.backend.directory.chmod(0o755)
    callback = n.PublicationNotifier(Path("unused"), store.backend.directory, "manual")
    n.emit(callback, {"outcome": "blocked", "publication_state": "no_attempt"})
    assert any(
        getattr(r, "event", None) == "notification_callback_failed"
        for r in caplog.records
    )
    assert "PRIVATE" not in "".join(JSONFormatter().format(r) for r in caplog.records)
