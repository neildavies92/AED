"""One loopback HTTPS callback; OAuth parsing belongs to AuthorizationAttempt."""

import os
import socket
import ssl
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ask_every_day.instagram_auth import AuthConfig, AuthError, AuthorizationAttempt

_PATH = "/oauth/instagram/callback"
_CHECKOUT = Path(__file__).resolve().parents[2]


def trusted_context(config: AuthConfig) -> ssl.SSLContext:
    """Require private local files and existing macOS SSL trust; never install it."""
    context = None
    try:
        if sys.platform != "darwin":
            raise ValueError
        paths = (config.tls_certificate, config.tls_private_key)
        for path in paths:
            if path is None or not path.is_absolute() or path.is_symlink():
                raise ValueError
            resolved = path.resolve(strict=True)
            info = resolved.stat()
            parent = resolved.parent.stat()
            if (
                resolved.is_relative_to(_CHECKOUT)
                or any((directory / ".git").exists() for directory in resolved.parents)
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or parent.st_uid != os.getuid()
                or parent.st_mode & 0o077
                or not 0 < info.st_size <= 65_536
            ):
                raise ValueError
        assert config.tls_certificate is not None
        assert config.tls_private_key is not None
        if (
            config.tls_certificate.resolve() == config.tls_private_key.resolve()
            or b"PRIVATE KEY" in config.tls_certificate.read_bytes()
            or b"CERTIFICATE" in config.tls_private_key.read_bytes()
        ):
            raise ValueError
        candidate = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        candidate.minimum_version = ssl.TLSVersion.TLSv1_2
        candidate.load_cert_chain(
            config.tls_certificate, config.tls_private_key, password=lambda: ""
        )
        subprocess.run(
            [
                "/usr/bin/security",
                "verify-cert",
                "-p",
                "ssl",
                "-n",
                "localhost",
                "-L",
                "-R",
                "offline",
                "-q",
                "-c",
                str(config.tls_certificate),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=True,
        )
        context = candidate
    except Exception:
        pass
    if context is None:
        raise AuthError("localhost_certificate")
    return context


class LocalCallbackReceiver:
    """Synchronous, bounded listener: no worker threads or generic HTTP server."""

    def __init__(
        self,
        config: AuthConfig,
        *,
        context_factory: Callable[[AuthConfig], ssl.SSLContext] = trusted_context,
        listener_factory: Callable[[], socket.socket] = socket.socket,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 300,
    ):
        if isinstance(timeout, bool) or not 0 < timeout <= 300:
            raise AuthError("callback_configuration")
        self._config = config
        self._context_factory = context_factory
        self._listener_factory = listener_factory
        self._clock = clock
        self._timeout = timeout
        self._listener: socket.socket | None = None
        self._context: ssl.SSLContext | None = None
        self._used = False
        self._deadline = 0.0

    def __enter__(self):
        if self._used or self._listener is not None:
            raise AuthError("callback_used")
        self._context = self._context_factory(self._config)
        listener = self._listener_factory()
        self._listener = listener
        failed = False
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 8765))
            listener.listen(4)
            self._deadline = self._clock() + self._timeout
        except BaseException as error:
            self.close()
            if not isinstance(error, Exception):
                raise
            failed = True
        if failed:
            raise AuthError("callback_startup")
        return self

    def close(self) -> None:
        self._used = True
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        self._context = None

    def __exit__(self, *_):
        self.close()

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def _request(self, connection: ssl.SSLSocket, deadline: float) -> tuple[str, str]:
        payload = bytearray()
        while b"\r\n\r\n" not in payload:
            connection.settimeout(self._remaining(deadline))
            chunk = connection.recv(min(4096, 16_385 - len(payload)))
            if not chunk:
                raise ValueError
            payload.extend(chunk)
            if len(payload) > 16_384:
                raise ValueError
            if b"\r\n" not in payload and len(payload) > 8300:
                raise ValueError
        lines = payload.split(b"\r\n\r\n", 1)[0].decode("ascii").split("\r\n")
        if len(lines[0]) > 8300 or len(lines) > 33:
            raise ValueError
        method, target, version = lines[0].split(" ")
        if version not in {"HTTP/1.0", "HTTP/1.1"}:
            raise ValueError
        headers = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            name = name.lower()
            if (
                not separator
                or not name
                or not name.isascii()
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name)
                or name in headers
                or len(line) > 8192
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
            ):
                raise ValueError
            headers[name] = value.strip()
        if (
            headers.get("host") != "localhost:8765"
            or "transfer-encoding" in headers
            or headers.get("content-length", "0") != "0"
            or any(ord(c) < 33 or ord(c) > 126 for c in target)
        ):
            raise ValueError
        return method, target

    def _respond(self, connection: ssl.SSLSocket, status: str, deadline: float):
        body = b"Instagram response received. Return to AED."
        response = (
            f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n"
            "Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n"
            "Content-Security-Policy: default-src 'none'\r\n\r\n"
        ).encode() + body
        try:
            connection.settimeout(self._remaining(deadline))
            connection.sendall(response)
        except OSError:
            pass

    def wait(self, attempt: AuthorizationAttempt) -> str:
        listener, context = self._listener, self._context
        if self._used or listener is None or context is None:
            raise AuthError("callback_used")
        try:
            if attempt.config != self._config:
                raise AuthError("callback_configuration")
            for _ in range(32):
                listener.settimeout(self._remaining(self._deadline))
                connection, _ = listener.accept()
                deadline = min(self._deadline, self._clock() + 2)
                with connection:
                    try:
                        connection.settimeout(self._remaining(deadline))
                        with context.wrap_socket(connection, server_side=True) as tls:
                            method, target = self._request(tls, deadline)
                            path, _, query = target.partition("?")
                            if method != "GET":
                                self._respond(tls, "405 Method Not Allowed", deadline)
                                continue
                            if path != _PATH:
                                self._respond(tls, "404 Not Found", deadline)
                                continue
                            error = False
                            code = ""
                            try:
                                code = attempt.consume(query)
                            except AuthError:
                                error = True
                            self._respond(
                                tls, "400 Bad Request" if error else "200 OK", deadline
                            )
                            if error:
                                raise AuthError("authorization_response")
                            return code
                    except (OSError, ValueError):
                        continue
            raise AuthError("callback_request_limit")
        except TimeoutError:
            pass
        finally:
            self.close()
            attempt.cancel()
        raise AuthError("callback_timeout")
