"""Explicit, owner-private POSIX storage; permissions are not encryption."""

import hashlib
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ask_every_day.credential_store import KeychainCredentialStore
from ask_every_day.instagram_auth import AuthError

_LIMIT = 65_536


class FileSecretBackend:
    """Reuse the validated credential codec, never instantiate macOS Keychain.

    The operator provisions the directory. Ancestor directories and the host are
    trusted; the pinned leaf directory/files must belong to the effective user.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    @staticmethod
    def _name(service: str, account: str) -> str:
        if account not in {"credential", "app-secret"}:
            raise AuthError("secure_store")
        return hashlib.sha256(service.encode()).hexdigest() + "." + account

    @staticmethod
    def _private(info: os.stat_result, *, directory: bool = False) -> None:
        expected = 0o700 if directory else 0o600
        correct_type = (
            stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        )
        if (
            not correct_type
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != expected
            or (not directory and (info.st_nlink != 1 or info.st_size > _LIMIT))
        ):
            raise AuthError("secure_store")

    def _operation[T](self, function: Callable[[int], T]) -> T:
        failed = False
        descriptor = None
        result = None
        try:
            descriptor = os.open(
                self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            self._private(os.fstat(descriptor), directory=True)
            result = function(descriptor)
        except Exception:
            failed = True
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if failed:
            raise AuthError("secure_store")
        return cast(T, result)

    def read(self, service: str, account: str) -> bytes | None:
        name = self._name(service, account)

        def read(directory: int) -> bytes | None:
            try:
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
            except FileNotFoundError:
                return None
            with os.fdopen(descriptor, "rb") as stream:
                self._private(os.fstat(stream.fileno()))
                payload = stream.read(_LIMIT + 1)
            if not 0 < len(payload) <= _LIMIT:
                raise AuthError("secure_store")
            return payload

        return self._operation(read)

    def write(self, service: str, account: str, value: bytes) -> None:
        name = self._name(service, account)
        if not isinstance(value, bytes) or not 0 < len(value) <= _LIMIT:
            raise AuthError("secure_store")

        def write(directory: int) -> None:
            try:
                self._private(os.stat(name, dir_fd=directory, follow_symlinks=False))
            except FileNotFoundError:
                pass
            temporary = ".aed-secret-" + secrets.token_hex(16)
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass

        self._operation(write)

    def delete(self, service: str, account: str) -> None:
        name = self._name(service, account)

        def delete(directory: int) -> None:
            try:
                self._private(os.stat(name, dir_fd=directory, follow_symlinks=False))
            except FileNotFoundError:
                return
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)

        self._operation(delete)


class FileCredentialStore(KeychainCredentialStore):
    """CredentialStore-compatible explicit file backend with the existing codec."""

    def __init__(self, app_id: str, directory: Path):
        super().__init__(app_id, backend=FileSecretBackend(directory))
