"""Native production entrypoints fail before credentials, staging or media POSTs."""

import sys
from unittest.mock import Mock

import pytest

from ask_every_day import (
    media_staging,
    production_runtime,
    publish_next_cli,
    scheduler_cli,
)
from ask_every_day.instagram_client import InstagramClient, urllib_transport
from ask_every_day.production_runtime import DockerPublisherRequired
from ask_every_day.scheduler import run_daily
from tests.test_publish_next import run as run
from tests.test_scheduler import inputs as inputs
from tests.test_scheduler import schedule as schedule


def native(monkeypatch):
    monkeypatch.setattr(production_runtime, "docker_runtime", lambda: False)


def test_direct_orchestrator_blocks_before_credentials_staging_or_attempt(
    run, monkeypatch
):
    native(monkeypatch)
    with pytest.raises(DockerPublisherRequired, match="docker_publisher_required"):
        run.publish()
    run.credentials.get_valid_credential.assert_not_called()
    assert run.events == [] and run.repo.list_publications() == []


def test_direct_scheduler_blocks_before_lock_or_credentials(
    run, inputs, schedule, monkeypatch
):
    native(monkeypatch)
    factory = Mock()
    with pytest.raises(DockerPublisherRequired):
        run_daily(run.config, inputs, schedule, factory)
    factory.assert_not_called()
    assert not run.repo.list_publications()


@pytest.mark.parametrize("operation", ["create_image_container", "publish_container"])
def test_low_level_media_post_is_blocked_with_injected_transport(
    monkeypatch, operation
):
    native(monkeypatch)
    transport = Mock()
    client = InstagramClient("SYNTHETIC", transport=transport)
    with pytest.raises(DockerPublisherRequired):
        if operation == "create_image_container":
            client.create_image_container("123", "https://example.test/image.jpg")
        else:
            client.publish_container("123", "456")
    transport.assert_not_called()


def test_read_only_instagram_identity_remains_available(monkeypatch):
    native(monkeypatch)
    transport = Mock(
        return_value=(
            200,
            b'{"user_id":"123","username":"ask.every.day","account_type":"BUSINESS"}',
        )
    )
    assert (
        InstagramClient("SYNTHETIC", transport=transport).identify_account().user_id
        == "123"
    )
    assert transport.call_args.args[0] == "GET"


@pytest.mark.parametrize("method", ["POST", "post"])
def test_direct_transport_blocks_media_post_before_request(monkeypatch, method):
    native(monkeypatch)
    request = Mock(side_effect=AssertionError("request must not be constructed"))
    monkeypatch.setattr("ask_every_day.instagram_client.Request", request)
    with pytest.raises(DockerPublisherRequired):
        urllib_transport(method, "https://example.test/media", {}, b"", 1.0)
    request.assert_not_called()


@pytest.mark.parametrize(
    "entry,args", [(publish_next_cli, ["--confirm"]), (scheduler_cli, ["run"])]
)
def test_native_cli_fails_before_configuration_and_credentials(
    monkeypatch, capsys, entry, args
):
    native(monkeypatch)
    monkeypatch.setattr(entry, "configure_logging", Mock())
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    config, factory = Mock(), Mock()
    monkeypatch.setattr(entry, "load_config", config)
    with pytest.raises(SystemExit):
        entry.main(args, credential_factory=factory)
    captured = capsys.readouterr()
    assert "docker_publisher_required" in captured.out + captured.err
    config.assert_not_called()
    factory.assert_not_called()


def test_native_staging_cli_fails_before_database_or_tunnel(monkeypatch, capsys):
    native(monkeypatch)
    monkeypatch.setattr(media_staging, "configure_logging", Mock())
    monkeypatch.setattr(sys, "argv", ["stage-media", "AED-0001"])
    stage = Mock()
    monkeypatch.setattr(media_staging, "stage_media", stage)
    with pytest.raises(SystemExit):
        media_staging.main()
    assert "docker_publisher_required" in capsys.readouterr().err
    stage.assert_not_called()


def test_native_state_status_remains_read_only(run, monkeypatch, capsys):
    native(monkeypatch)
    monkeypatch.setattr(scheduler_cli, "configure_logging", Mock())
    monkeypatch.setattr(scheduler_cli, "load_config", lambda _: run.config)
    before = run.config.database_path.read_bytes()
    scheduler_cli.main(["status"], credential_factory=Mock(side_effect=AssertionError))
    assert '"enabled": false' in capsys.readouterr().out
    assert run.config.database_path.read_bytes() == before
