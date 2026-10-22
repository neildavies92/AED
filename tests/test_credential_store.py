"""Native Keychain boundaries are faked; CI never accesses an operator Keychain."""

import ctypes
import json
from datetime import UTC, datetime, timedelta
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from ask_every_day import credential_store as storage
from ask_every_day.instagram_auth import SCOPES, AuthError, InstagramCredential


class Backend:
    def __init__(self):
        self.values = {}

    def read(self, service, account):
        return self.values.get((service, account))

    def write(self, service, account, value):
        self.values[service, account] = value

    def delete(self, service, account):
        self.values.pop((service, account), None)


def credential():
    now = datetime(2026, 10, 22, tzinfo=UTC)
    return InstagramCredential(
        "SYNTHETIC_TEST_TOKEN",
        "100000000000001",
        "100000000000002",
        "100000000000003",
        "ask.every.day",
        SCOPES,
        now,
        now + timedelta(days=60),
    )


def test_store_round_trip_update_and_local_disconnect():
    backend = Backend()
    store = storage.KeychainCredentialStore(credential().app_id, backend=backend)
    assert store.load() is None
    store.save(credential())
    assert store.load() == credential()
    store.save(credential())
    assert len(backend.values) == 1
    store.set_app_secret("SYNTHETIC_APP_SECRET")
    assert store.app_secret() == "SYNTHETIC_APP_SECRET"
    assert len(backend.values) == 2
    assert "SYNTHETIC" not in repr(store)
    store.delete()
    store.delete()
    assert store.load() is None
    store.delete_app_secret()
    with pytest.raises(AuthError):
        store.app_secret()
    assert not backend.values


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"username": "wrong.account"},
        {"user_id": "PRIVATE"},
        {"expires_at": "PRIVATE"},
        {"token_type": "other"},
        {"scopes": []},
        {"scopes": "instagram_business_basic"},
        {"access_token": ""},
        {"issued_at": "2026-10-22T00:00:00"},
        {"app_id": "100000000000009"},
        {"extra": "PRIVATE"},
    ],
)
def test_corrupted_metadata_fails_closed_without_leaking(change):
    backend = Backend()
    store = storage.KeychainCredentialStore(credential().app_id, backend=backend)
    store.save(credential())
    key = next(iter(backend.values))
    data = json.loads(backend.values[key])
    data.update(change)
    backend.values[key] = json.dumps(data).encode()
    with pytest.raises(AuthError) as caught:
        store.load()
    assert "PRIVATE" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize(
    "payload",
    [b"PRIVATE", b"[]", b"x" * 65537, b"", b'{"version":1,"version":1}', None],
)
def test_invalid_backend_data(payload):
    backend = Mock(read=Mock(return_value=payload))
    store = storage.KeychainCredentialStore(credential().app_id, backend=backend)
    if payload is None:
        assert store.load() is None
    else:
        with pytest.raises(AuthError):
            store.load()


@pytest.mark.parametrize(
    "method",
    ["load", "save", "delete", "app_secret", "set_app_secret", "delete_app_secret"],
)
def test_store_failures_discard_original_exception(method):
    backend = Mock()
    backend.read.side_effect = OSError("PRIVATE_BODY_TOKEN")
    backend.write.side_effect = OSError("PRIVATE_BODY_TOKEN")
    backend.delete.side_effect = OSError("PRIVATE_BODY_TOKEN")
    store = storage.KeychainCredentialStore(credential().app_id, backend=backend)
    args = (
        [credential()]
        if method == "save"
        else ["SYNTHETIC_SECRET"]
        if method == "set_app_secret"
        else []
    )
    with pytest.raises(AuthError) as caught:
        getattr(store, method)(*args)
    assert caught.value.__context__ is None
    assert "PRIVATE" not in str(caught.value)


def test_app_namespaces_cannot_reuse_credentials():
    backend = Backend()
    original = storage.KeychainCredentialStore(credential().app_id, backend=backend)
    other = storage.KeychainCredentialStore("100000000000009", backend=backend)
    original.save(credential())
    assert other.load() is None
    with pytest.raises(AuthError):
        other.save(credential())


def test_keychain_unavailable_platform_or_library_is_sanitized(monkeypatch):
    monkeypatch.setattr(storage.sys, "platform", "linux")
    with pytest.raises(AuthError):
        storage.MacOSKeychain()
    monkeypatch.setattr(storage.sys, "platform", "darwin")
    monkeypatch.setattr(storage.ctypes, "CDLL", Mock(side_effect=OSError("PRIVATE")))
    with pytest.raises(AuthError) as caught:
        storage.MacOSKeychain()
    assert "PRIVATE" not in str(caught.value) and caught.value.__context__ is None


def native():
    result = SimpleNamespace()
    result._cf = SimpleNamespace(
        CFDataCreate=Mock(return_value=21),
        CFRelease=Mock(),
        CFGetTypeID=Mock(return_value=1),
        CFDataGetTypeID=Mock(return_value=1),
        CFDataGetLength=Mock(return_value=3),
        CFDataGetBytePtr=Mock(),
    )
    result._security = SimpleNamespace(
        SecItemUpdate=Mock(return_value=0),
        SecItemAdd=Mock(return_value=0),
        SecItemDelete=Mock(return_value=0),
        SecItemCopyMatching=Mock(),
    )
    result._query = Mock(return_value={1: 2})
    result._dictionary = Mock(return_value=20)
    result._constant = Mock(return_value=3)
    for name in ("read", "write", "delete"):
        setattr(result, name, MethodType(getattr(storage.MacOSKeychain, name), result))
    return result


@pytest.mark.parametrize(
    "statuses, add_status, calls",
    [
        ([0], 0, 1),
        ([-25300], 0, 1),
        ([-25300, 0], -25299, 2),
    ],
)
def test_native_atomic_update_add_and_duplicate_race(statuses, add_status, calls):
    keychain = native()
    keychain._security.SecItemUpdate.side_effect = statuses
    keychain._security.SecItemAdd.return_value = add_status
    keychain.write("SYNTHETIC_SERVICE", "credential", b"SYNTHETIC_SECRET")
    assert keychain._security.SecItemUpdate.call_count == calls
    assert keychain._security.SecItemAdd.call_count == int(statuses[0] == -25300)
    keychain._cf.CFRelease.assert_called_once_with(21)


@pytest.mark.parametrize("operation", ["write", "delete", "read"])
def test_native_os_errors_fail_closed(operation):
    keychain = native()
    keychain._security.SecItemUpdate.return_value = -25293
    keychain._security.SecItemDelete.return_value = -25293
    keychain._security.SecItemCopyMatching.return_value = -25293
    args = ("SYNTHETIC_SERVICE", "credential")
    if operation == "write":
        args += (b"SYNTHETIC_SECRET",)
    with pytest.raises(AuthError):
        getattr(keychain, operation)(*args)


def test_native_missing_read_and_delete_are_clean():
    keychain = native()
    keychain._security.SecItemCopyMatching.return_value = -25300
    keychain._security.SecItemDelete.return_value = -25300
    assert keychain.read("SYNTHETIC_SERVICE", "credential") is None
    keychain.delete("SYNTHETIC_SERVICE", "credential")


def test_native_read_bounds_type_and_releases_returned_data():
    keychain = native()
    buffer = ctypes.create_string_buffer(b"FAK")
    keychain._cf.CFDataGetBytePtr.return_value = ctypes.addressof(buffer)

    def found(query, pointer):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(22)
        return 0

    keychain._security.SecItemCopyMatching.side_effect = found
    assert keychain.read("SYNTHETIC_SERVICE", "credential") == b"FAK"
    keychain._cf.CFDataGetLength.return_value = 65537
    with pytest.raises(AuthError):
        keychain.read("SYNTHETIC_SERVICE", "credential")
    keychain._cf.CFDataGetLength.return_value = 3
    keychain._cf.CFGetTypeID.return_value = 2
    with pytest.raises(AuthError):
        keychain.read("SYNTHETIC_SERVICE", "credential")
    assert keychain._cf.CFRelease.call_count == 3
