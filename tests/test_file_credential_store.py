"""Private persistent storage and refresh use synthetic credentials only."""

import json
import os
import stat
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from ask_every_day import credential_store
from ask_every_day.file_credential_store import FileCredentialStore, FileSecretBackend
from ask_every_day.instagram_auth import (
    AuthConfig,
    AuthError,
    CredentialManager,
    InstagramAuthClient,
)
from ask_every_day.instagram_client import InstagramIdentity
from tests.test_credential_store import credential


@pytest.fixture
def store(tmp_path, monkeypatch):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(
        credential_store,
        "MacOSKeychain",
        Mock(side_effect=AssertionError("Keychain forbidden")),
    )
    return FileCredentialStore(credential().app_id, directory)


def root(store):
    assert isinstance(store._backend, FileSecretBackend)
    return store._backend.directory


def test_roundtrip_atomic_refresh_restart_and_delete(store):
    assert store.load() is None
    store.save(credential())
    store.set_app_secret("SYNTHETIC_APP_SECRET")
    reopened = FileCredentialStore(credential().app_id, root(store))
    assert reopened.load() == credential()
    newer = replace(
        credential(),
        access_token="SYNTHETIC_REFRESHED_TOKEN",
        issued_at=credential().issued_at + timedelta(days=1),
        expires_at=credential().expires_at + timedelta(days=1),
    )
    reopened.save(newer)
    assert store.load() == newer
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in root(store).iterdir())
    reopened.delete()
    reopened.delete_app_secret()
    assert store.load() is None and list(root(store).iterdir()) == []


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o777])
def test_shared_directory_is_rejected(store, mode):
    root(store).chmod(mode)
    with pytest.raises(AuthError):
        store.save(credential())
    assert list(root(store).iterdir()) == []


def test_missing_directory_is_not_silently_provisioned(tmp_path):
    directory = tmp_path / "absent"
    store = FileCredentialStore(credential().app_id, directory)
    with pytest.raises(AuthError):
        store.save(credential())
    assert not directory.exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "mode", "oversized"])
def test_unsafe_credential_file_is_rejected_for_read_write_delete(
    store, tmp_path, kind
):
    store.save(credential())
    path = next(root(store).iterdir())
    if kind == "symlink":
        path.unlink()
        target = tmp_path / "target"
        target.write_bytes(b"SYNTHETIC_PRIVATE")
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(path, tmp_path / "copy")
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif kind == "mode":
        path.chmod(0o644)
    else:
        path.write_bytes(b"x" * 65_537)
    for operation in (store.load, lambda: store.save(credential()), store.delete):
        with pytest.raises(AuthError) as caught:
            operation()
        assert "SYNTHETIC" not in str(caught.value) and caught.value.__context__ is None
    assert path.exists()


def test_symlink_directory_and_wrong_owner_are_rejected(store, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(root(store), target_is_directory=True)
    other = FileCredentialStore(credential().app_id, link)
    with pytest.raises(AuthError):
        other.save(credential())
    info = root(store).stat()
    changed = list(info)
    changed[4] = info.st_uid + 1
    with pytest.raises(AuthError):
        FileSecretBackend._private(os.stat_result(changed), directory=True)


@pytest.mark.parametrize(
    "change",
    [
        {"username": "other"},
        {"app_id": "109"},
        {"scopes": []},
        {"expires_at": "SYNTHETIC_PRIVATE"},
        {"extra": "SYNTHETIC_PRIVATE"},
    ],
)
def test_existing_codec_validation_is_retained(store, change):
    store.save(credential())
    path = next(root(store).iterdir())
    data = json.loads(path.read_bytes())
    path.write_text(json.dumps({**data, **change}))
    with pytest.raises(AuthError) as caught:
        store.load()
    assert "SYNTHETIC_PRIVATE" not in str(caught.value)


def test_duplicate_keys_and_wrong_app_fail_closed(store):
    store.save(credential())
    with pytest.raises(AuthError):
        store.save(replace(credential(), app_id="109"))
    path = next(root(store).iterdir())
    path.write_text('{"version":1,"version":1}')
    with pytest.raises(AuthError):
        store.load()


def test_failed_replace_retains_old_credential_and_cleans_private_temporary(
    store, monkeypatch
):
    store.save(credential())
    monkeypatch.setattr(
        os, "replace", Mock(side_effect=OSError("SYNTHETIC_PRIVATE_BODY"))
    )
    with pytest.raises(AuthError):
        store.save(replace(credential(), access_token="SYNTHETIC_NEW_TOKEN"))
    assert store.load() == credential()
    assert len(list(root(store).iterdir())) == 1


def test_manager_refresh_persists_through_file_backend_restart(store):
    now = credential().issued_at + timedelta(days=55)
    store.save(credential())
    provider = Mock(spec=InstagramAuthClient)
    provider.config = AuthConfig(
        credential().app_id, "https://localhost:8765/oauth/instagram/callback"
    )
    provider.refresh_token.return_value = Mock(
        access_token="SYNTHETIC_REFRESHED_TOKEN", expires_in=60 * 86_400
    )
    manager = CredentialManager(
        provider,
        store,
        clock=lambda: now,
        identify=lambda _: InstagramIdentity(
            credential().user_id, "ask.every.day", "BUSINESS"
        ),
    )
    refreshed = manager.get_valid_credential()
    assert refreshed.access_token == "SYNTHETIC_REFRESHED_TOKEN"
    reopened = FileCredentialStore(credential().app_id, root(store))
    assert reopened.load() == refreshed
    manager.get_valid_credential()
    provider.refresh_token.assert_called_once()
    assert "SYNTHETIC" not in repr(refreshed) + repr(store)
