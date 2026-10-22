"""macOS Keychain storage through native APIs; no secrets in subprocess arguments."""

import ctypes
import json
import sys
from contextlib import ExitStack
from datetime import datetime
from typing import Protocol

from ask_every_day.instagram_auth import (
    AuthError,
    InstagramCredential,
    _id,
    _secret,
    _unique_object,
)

_MAX_ITEM = 65_536


class SecretBackend(Protocol):
    def read(self, service: str, account: str) -> bytes | None: ...

    def write(self, service: str, account: str, value: bytes) -> None: ...

    def delete(self, service: str, account: str) -> None: ...


class MacOSKeychain:
    """Generic-password entries in the user's default local Keychain."""

    _cf: ctypes.CDLL
    _security: ctypes.CDLL

    def __init__(self):
        if sys.platform != "darwin":
            raise AuthError("secure_store_unavailable")
        failed = False
        try:
            self._cf = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            self._security = ctypes.CDLL(
                "/System/Library/Frameworks/Security.framework/Security"
            )
            signatures = {
                "CFStringCreateWithBytes": (
                    [
                        ctypes.c_void_p,
                        ctypes.c_char_p,
                        ctypes.c_long,
                        ctypes.c_uint32,
                        ctypes.c_bool,
                    ],
                    ctypes.c_void_p,
                ),
                "CFDataCreate": (
                    [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long],
                    ctypes.c_void_p,
                ),
                "CFDictionaryCreate": (
                    [
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                        ctypes.c_long,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                    ],
                    ctypes.c_void_p,
                ),
                "CFDataGetLength": ([ctypes.c_void_p], ctypes.c_long),
                "CFDataGetBytePtr": ([ctypes.c_void_p], ctypes.c_void_p),
                "CFGetTypeID": ([ctypes.c_void_p], ctypes.c_ulong),
                "CFDataGetTypeID": ([], ctypes.c_ulong),
                "CFRelease": ([ctypes.c_void_p], None),
            }
            for name, (args, result) in signatures.items():
                function = getattr(self._cf, name)
                function.argtypes = args
                function.restype = result
            for name, args in {
                "SecItemAdd": [ctypes.c_void_p, ctypes.c_void_p],
                "SecItemCopyMatching": [ctypes.c_void_p, ctypes.c_void_p],
                "SecItemUpdate": [ctypes.c_void_p, ctypes.c_void_p],
                "SecItemDelete": [ctypes.c_void_p],
            }.items():
                function = getattr(self._security, name)
                function.argtypes = args
                function.restype = ctypes.c_int32
        except Exception:
            failed = True
        if failed:
            raise AuthError("secure_store_unavailable")

    def _constant(self, name: str, library=None) -> int:
        value = ctypes.c_void_p.in_dll(
            self._security if library is None else library, name
        ).value
        if value is None:
            raise AuthError("secure_store")
        return value

    def _query(self, stack: ExitStack, service: str, account: str) -> dict[int, int]:
        def string(value):
            encoded = value.encode("utf-8")
            result = self._cf.CFStringCreateWithBytes(
                None, encoded, len(encoded), 0x08000100, False
            )
            if not result:
                raise AuthError("secure_store")
            stack.callback(self._cf.CFRelease, result)
            return result

        return {
            self._constant("kSecClass"): self._constant("kSecClassGenericPassword"),
            self._constant("kSecAttrService"): string(service),
            self._constant("kSecAttrAccount"): string(account),
        }

    def _dictionary(self, stack: ExitStack, values: dict[int, int]) -> int:
        result = self._cf.CFDictionaryCreate(
            None,
            (ctypes.c_void_p * len(values))(*values),
            (ctypes.c_void_p * len(values))(*values.values()),
            len(values),
            ctypes.byref(
                ctypes.c_byte.in_dll(self._cf, "kCFTypeDictionaryKeyCallBacks")
            ),
            ctypes.byref(
                ctypes.c_byte.in_dll(self._cf, "kCFTypeDictionaryValueCallBacks")
            ),
        )
        if not result:
            raise AuthError("secure_store")
        stack.callback(self._cf.CFRelease, result)
        return result

    def read(self, service: str, account: str) -> bytes | None:
        with ExitStack() as stack:
            attributes = self._query(stack, service, account)
            attributes[self._constant("kSecReturnData")] = self._constant(
                "kCFBooleanTrue", self._cf
            )
            query = self._dictionary(stack, attributes)
            result = ctypes.c_void_p()
            status = self._security.SecItemCopyMatching(query, ctypes.byref(result))
            if result.value:
                stack.callback(self._cf.CFRelease, result)
            if status == -25300:
                return None
            if (
                status != 0
                or not result.value
                or self._cf.CFGetTypeID(result) != self._cf.CFDataGetTypeID()
            ):
                raise AuthError("secure_store")
            size = self._cf.CFDataGetLength(result)
            if not 0 < size <= _MAX_ITEM:
                raise AuthError("secure_store")
            return ctypes.string_at(self._cf.CFDataGetBytePtr(result), size)

    def write(self, service: str, account: str, value: bytes) -> None:
        if not isinstance(value, bytes) or not 0 < len(value) <= _MAX_ITEM:
            raise AuthError("secure_store")
        with ExitStack() as stack:
            attributes = self._query(stack, service, account)
            query = self._dictionary(stack, attributes)
            data = self._cf.CFDataCreate(None, value, len(value))
            if not data:
                raise AuthError("secure_store")
            stack.callback(self._cf.CFRelease, data)
            updates = {self._constant("kSecValueData"): data}
            update = self._dictionary(stack, updates)
            status = self._security.SecItemUpdate(query, update)
            if status == -25300:
                status = self._security.SecItemAdd(
                    self._dictionary(stack, attributes | updates), None
                )
                if status == -25299:
                    status = self._security.SecItemUpdate(query, update)
            if status != 0:
                raise AuthError("secure_store")

    def delete(self, service: str, account: str) -> None:
        with ExitStack() as stack:
            query = self._dictionary(stack, self._query(stack, service, account))
            if self._security.SecItemDelete(query) not in {0, -25300}:
                raise AuthError("secure_store")


class KeychainCredentialStore:
    def __init__(self, app_id: str, *, backend: SecretBackend | None = None):
        self.app_id = _id(app_id)
        self._backend = backend if backend is not None else MacOSKeychain()
        self._service = f"com.ask-every-day.instagram.oauth.{app_id}"

    def _read(self, account: str) -> bytes | None:
        failed = False
        result = None
        try:
            result = self._backend.read(self._service, account)
            if result is not None and (
                not isinstance(result, bytes) or not 0 < len(result) <= _MAX_ITEM
            ):
                raise ValueError
        except Exception:
            failed = True
        if failed:
            raise AuthError("secure_store")
        return result

    def _write(self, account: str, value: bytes) -> None:
        failed = False
        try:
            self._backend.write(self._service, account, value)
        except Exception:
            failed = True
        if failed:
            raise AuthError("secure_store")

    def _delete(self, account: str) -> None:
        failed = False
        try:
            self._backend.delete(self._service, account)
        except Exception:
            failed = True
        if failed:
            raise AuthError("secure_store")

    def load(self) -> InstagramCredential | None:
        payload = self._read("credential")
        if payload is None:
            return None
        credential = None
        try:
            data = json.loads(payload, object_pairs_hook=_unique_object)
            if (
                not isinstance(data, dict)
                or type(data.get("version")) is not int
                or data.pop("version") != 1
                or type(data["scopes"]) is not list
            ):
                raise ValueError
            data["scopes"] = frozenset(data["scopes"])
            data["issued_at"] = datetime.fromisoformat(data["issued_at"])
            data["expires_at"] = datetime.fromisoformat(data["expires_at"])
            candidate = InstagramCredential(**data)
            if candidate.app_id != self.app_id:
                raise ValueError
            credential = candidate
        except Exception:
            pass
        if credential is None:
            raise AuthError("credential_metadata")
        return credential

    def save(self, credential: InstagramCredential) -> None:
        credential.__post_init__()
        if credential.app_id != self.app_id:
            raise AuthError("app_binding")
        data = {
            "version": 1,
            "access_token": credential.access_token,
            "app_id": credential.app_id,
            "authorization_user_id": credential.authorization_user_id,
            "user_id": credential.user_id,
            "username": credential.username,
            "scopes": sorted(credential.scopes),
            "issued_at": credential.issued_at.isoformat(),
            "expires_at": credential.expires_at.isoformat(),
            "token_type": credential.token_type,
        }
        self._write("credential", json.dumps(data, separators=(",", ":")).encode())

    def delete(self) -> None:
        self._delete("credential")

    def set_app_secret(self, value: str) -> None:
        self._write("app-secret", _secret(value).encode("ascii"))

    def app_secret(self) -> str:
        value = self._read("app-secret")
        result = None
        try:
            if value is not None:
                result = _secret(value.decode("ascii"))
        except Exception:
            pass
        if result is None:
            raise AuthError("missing_app_secret")
        return result

    def delete_app_secret(self) -> None:
        self._delete("app-secret")
