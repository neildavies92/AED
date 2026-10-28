"""Owner opt-in precedes any Keychain construction or credential access."""

from unittest.mock import Mock

import pytest

from ask_every_day import credential_handoff as cli
from ask_every_day.file_credential_store import FileCredentialStore
from ask_every_day.instagram_auth import REDIRECT_URI, AuthConfig
from tests.test_credential_store import credential


@pytest.fixture
def setup(monkeypatch):
    manager = Mock()
    manager.local_credential.return_value = credential()
    factory = Mock(return_value=manager)
    monkeypatch.setattr(cli, "credential_manager", factory)
    monkeypatch.setattr(
        cli,
        "load_auth_config",
        Mock(return_value=AuthConfig(credential().app_id, REDIRECT_URI)),
    )
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    return factory


@pytest.mark.parametrize("answer", ["yes", "", "EXPORT"])
def test_no_access_without_exact_approval(setup, monkeypatch, tmp_path, answer):
    monkeypatch.setattr("builtins.input", lambda _: answer)
    with pytest.raises(SystemExit):
        cli.main(["--destination", str(tmp_path / "private")])
    setup.assert_not_called()


def test_non_tty_never_accesses_keychain(setup, monkeypatch, tmp_path):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        cli.main(["--destination", str(tmp_path / "private")])
    setup.assert_not_called()


def test_approved_export_private_roundtrip_without_secret_output(
    setup, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: cli.CHALLENGE)
    target = tmp_path / "private"
    cli.main(["--destination", str(target)])
    assert FileCredentialStore(credential().app_id, target).load() == credential()
    assert credential().access_token not in capsys.readouterr().out
    assert target.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.iterdir())
    assert len(list(target.iterdir())) == 1


def test_existing_destination_never_read_or_overwritten(setup, monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["--destination", str(tmp_path)])
    setup.assert_not_called()
