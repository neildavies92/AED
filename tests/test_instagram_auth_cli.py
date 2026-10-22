"""Operator commands expose metadata only, with no live OAuth or Keychain access."""

import json
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from ask_every_day import instagram_auth_cli as cli
from ask_every_day.instagram_auth import SCOPES, AuthError, InstagramCredential


@pytest.fixture
def setup(tmp_path, monkeypatch):
    config = tmp_path / "auth.toml"
    config.write_text(
        'app_id="100000000000001"\n'
        'redirect_uri="https://localhost:8765/oauth/instagram/callback"\n'
    )
    store = Mock()
    store.load.return_value = None
    monkeypatch.setattr(cli, "KeychainCredentialStore", Mock(return_value=store))
    monkeypatch.setattr(cli, "configure_logging", Mock())
    return config, store


def run(monkeypatch, config, command):
    monkeypatch.setattr(sys, "argv", ["auth", command, "--config", str(config)])
    cli.main()


def test_login_runs_orchestration_and_prints_only_safe_metadata(
    setup, monkeypatch, capsys
):
    config, store = setup
    connected = Mock(expires_at=datetime(2026, 12, 1, tzinfo=UTC))
    operation = Mock(return_value=connected)
    monkeypatch.setattr(cli, "login", operation)
    run(monkeypatch, config, "login")
    assert operation.call_args.args[1] is store
    assert json.loads(capsys.readouterr().out) == {
        "connected": True,
        "account": "@ask.every.day",
        "credential_status": "valid",
        "expires_at": "2026-12-01T00:00:00+00:00",
    }


@pytest.mark.parametrize(
    "error", [AuthError("localhost_certificate"), KeyboardInterrupt()]
)
def test_login_setup_and_cancellation_are_safe(setup, monkeypatch, capsys, error):
    config, _ = setup
    monkeypatch.setattr(cli, "login", Mock(side_effect=error))
    with pytest.raises(SystemExit) as caught:
        run(monkeypatch, config, "login")
    assert caught.value.code == 1
    assert "docs/instagram-auth.md" in capsys.readouterr().err


def test_missing_status_is_safe_and_offline(setup, monkeypatch, capsys):
    config, store = setup
    run(monkeypatch, config, "status")
    assert json.loads(capsys.readouterr().out) == {
        "connected": False,
        "credential_valid": False,
    }
    assert store.mock_calls == [("load", (), {})]


@pytest.mark.parametrize("expired", [False, True])
def test_status_contains_no_token_id_or_redirect(setup, monkeypatch, capsys, expired):
    config, store = setup
    now = datetime.now(UTC)
    store.load.return_value = InstagramCredential(
        "SYNTHETIC_PRIVATE_TOKEN",
        "100000000000001",
        "100000000000002",
        "100000000000003",
        "ask.every.day",
        SCOPES,
        now - timedelta(days=59),
        now + timedelta(days=1) if not expired else now - timedelta(seconds=1),
    )
    run(monkeypatch, config, "status")
    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["connected"] and data["credential_valid"] is (not expired)
    assert data["account"] == "@ask.every.day" and data["refresh_due"]
    for private in ("SYNTHETIC_PRIVATE_TOKEN", "100000000000001", "localhost"):
        assert private not in output


def test_bootstrap_requires_hidden_interactive_input(setup, monkeypatch, capsys):
    config, store = setup
    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=Mock(return_value=False)))
    prompt = Mock(return_value="SYNTHETIC_APP_SECRET")
    monkeypatch.setattr(cli.getpass, "getpass", prompt)
    with pytest.raises(SystemExit):
        run(monkeypatch, config, "bootstrap")
    prompt.assert_not_called()
    store.set_app_secret.assert_not_called()
    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=Mock(return_value=True)))
    run(monkeypatch, config, "bootstrap")
    store.set_app_secret.assert_called_once_with("SYNTHETIC_APP_SECRET")
    output = capsys.readouterr()
    assert "SYNTHETIC_APP_SECRET" not in output.out + output.err
    assert json.loads(output.out) == {"app_secret_stored": True}


def test_logout_removes_both_local_secrets_without_provider_call(
    setup, monkeypatch, capsys
):
    config, store = setup
    run(monkeypatch, config, "logout")
    assert store.mock_calls == [("delete", (), {}), ("delete_app_secret", (), {})]
    assert json.loads(capsys.readouterr().out) == {
        "connected": False,
        "app_secret_stored": False,
    }


def test_cli_error_never_prints_raw_exception(setup, monkeypatch, capsys, caplog):
    config, store = setup
    store.load.side_effect = RuntimeError("SYNTHETIC_PRIVATE_BODY_TOKEN")
    with pytest.raises(SystemExit):
        run(monkeypatch, config, "status")
    output = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE" not in output.out + output.err + caplog.text


@pytest.mark.parametrize(
    "text",
    [
        'app_id="123"\napp_secret="SYNTHETIC_PRIVATE_SECRET"',
        'app_id="123"\nredirect_uri="http://127.0.0.1/callback"',
        'app_id="123"\nredirect_uri="https://localhost:8765/oauth/instagram/callback"\ntls_certificate="/fake/path"',
        'app_id="123"\nredirect_uri="https://localhost:8765/oauth/instagram/callback"\ntls_certificate=42\ntls_private_key="/fake/path"',
        "SYNTHETIC_PRIVATE" * 8192,
        "not toml SYNTHETIC_PRIVATE",
    ],
)
def test_config_is_strict_bounded_and_sanitized(tmp_path, text):
    path = tmp_path / "auth.toml"
    path.write_text(text)
    with pytest.raises(AuthError) as caught:
        cli.load_auth_config(path)
    assert caught.value.__context__ is None
    assert "SYNTHETIC_PRIVATE" not in str(caught.value)


def test_optional_tls_paths_are_local_config_only(setup):
    path, _ = setup
    with path.open("a") as stream:
        stream.write('tls_certificate="~/aed-test/cert.pem"\n')
        stream.write('tls_private_key="~/aed-test/key.pem"\n')
    loaded = cli.load_auth_config(path)
    assert loaded.tls_certificate is not None and loaded.tls_certificate.is_absolute()
    assert loaded.tls_private_key is not None and loaded.tls_private_key.is_absolute()
    assert "aed-test" not in repr(loaded)
