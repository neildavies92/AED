"""Real local TLS, ephemeral test ports and certificates; no OS trust changes."""

import socket
import ssl
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from ask_every_day import instagram_callback as callback
from ask_every_day.instagram_auth import (
    REDIRECT_URI,
    AuthConfig,
    AuthError,
    AuthorizationAttempt,
)


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    directory = tmp_path_factory.mktemp("aed-synthetic-tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-addext",
            "basicConstraints=critical,CA:FALSE",
            "-addext",
            "extendedKeyUsage=serverAuth",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=10,
    )
    cert.chmod(0o600)
    key.chmod(0o600)
    return cert, key


@pytest.fixture
def local(certificates, monkeypatch):
    cert, key = certificates
    config = AuthConfig("100000000000001", REDIRECT_URI, cert, key)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(cert, key)
    client = ssl.create_default_context(cafile=str(cert))
    listeners = []

    class Listener(socket.socket):
        def bind(self, address):
            assert address == ("127.0.0.1", 8765)
            super().bind(("127.0.0.1", 0))

    def listener():
        value = Listener()
        listeners.append(value)
        return value

    original = socket.create_connection

    def loopback_only(address, *args, **kwargs):
        assert address[0] == "127.0.0.1"
        return original(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", loopback_only)

    def receiver(timeout=1):
        return callback.LocalCallbackReceiver(
            config,
            context_factory=lambda _: server,
            listener_factory=listener,
            timeout=timeout,
        )

    yield config, receiver, client, listeners
    assert all(value.fileno() == -1 for value in listeners)


def request(client, address, target, method="GET", headers=""):
    with socket.create_connection(address, timeout=2) as connection:
        with client.wrap_socket(connection, server_hostname="localhost") as tls:
            tls.sendall(
                f"{method} {target} HTTP/1.1\r\nHost: localhost:8765\r\n{headers}\r\n".encode()
            )
            return tls.recv(4096)


def query(attempt):
    state = parse_qs(urlsplit(attempt.authorization_url()).query)["state"][0]
    return urlencode({"state": state, "code": "SYNTHETIC_PRIVATE_CODE"})


def address(receiver):
    assert receiver._listener is not None
    return receiver._listener.getsockname()


def test_https_success_raw_query_handoff_and_cleanup(local, caplog, monkeypatch):
    config, factory, client, _ = local
    attempt = AuthorizationAttempt(config)
    raw = query(attempt)
    consume = Mock(wraps=attempt.consume)
    monkeypatch.setattr(attempt, "consume", consume)
    before = {t.ident for t in threading.enumerate()}
    with factory() as receiver, ThreadPoolExecutor(max_workers=1) as pool:
        endpoint = address(receiver)
        future = pool.submit(receiver.wait, attempt)
        response = request(client, endpoint, "/oauth/instagram/callback?" + raw)
        assert b"200 OK" in response and b"no-store" in response
        assert future.result(timeout=2) == "SYNTHETIC_PRIVATE_CODE"
        consume.assert_called_once_with(raw)
        assert receiver._listener is None
        with pytest.raises(AuthError):
            receiver.wait(attempt)
    assert not ({t.ident for t in threading.enumerate()} - before)
    with pytest.raises(OSError):
        socket.create_connection(endpoint, timeout=0.1)
    assert raw not in caplog.text and "SYNTHETIC_PRIVATE" not in caplog.text
    with pytest.raises(AuthError):
        attempt.consume(raw)


@pytest.mark.parametrize(
    "method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT"]
)
def test_mutations_rejected_without_consuming_state(local, method):
    config, factory, client, _ = local
    attempt = AuthorizationAttempt(config)
    target = "/oauth/instagram/callback?" + query(attempt)
    with factory() as receiver, ThreadPoolExecutor(max_workers=1) as pool:
        endpoint = address(receiver)
        future = pool.submit(receiver.wait, attempt)
        assert b"405 Method Not Allowed" in request(client, endpoint, target, method)
        assert b"200 OK" in request(client, endpoint, target)
        assert future.result(timeout=2) == "SYNTHETIC_PRIVATE_CODE"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/favicon.ico",
        "/oauth/instagram/callback/",
        "/oauth/instagram/%63allback",
        "https://localhost:8765/oauth/instagram/callback",
    ],
)
def test_only_exact_path_is_accepted(local, path):
    config, factory, client, _ = local
    attempt = AuthorizationAttempt(config)
    raw = query(attempt)
    with factory() as receiver, ThreadPoolExecutor(max_workers=1) as pool:
        endpoint = address(receiver)
        future = pool.submit(receiver.wait, attempt)
        assert b"404 Not Found" in request(client, endpoint, path + "?" + raw)
        request(client, endpoint, "/oauth/instagram/callback?" + raw)
        assert future.result(timeout=2) == "SYNTHETIC_PRIVATE_CODE"


@pytest.mark.parametrize(
    "mode",
    [
        "missing_query",
        "malformed",
        "missing_state",
        "wrong_state",
        "missing_code",
        "denial",
        "duplicate",
        "expired",
        "query_bound",
    ],
)
def test_invalid_callback_fails_closed_and_cleans_up(local, mode, caplog):
    config, factory, client, _ = local
    now = [0.0]
    attempt = AuthorizationAttempt(config, clock=lambda: now[0])
    raw = query(attempt)
    state = parse_qs(raw)["state"][0]
    raw = {
        "missing_query": "",
        "malformed": "broken",
        "missing_state": "code=SYNTHETIC_PRIVATE_CODE",
        "wrong_state": "state=SYNTHETIC_PRIVATE_STATE&code=SYNTHETIC_PRIVATE_CODE",
        "missing_code": urlencode({"state": state}),
        "denial": urlencode({"state": state, "error": "access_denied"}),
        "duplicate": raw + "&code=SYNTHETIC_DUPLICATE",
        "expired": raw,
        "query_bound": "x" * 8193,
    }[mode]
    if mode == "expired":
        now[0] = 300
    with factory() as receiver, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(receiver.wait, attempt)
        response = request(
            client, address(receiver), "/oauth/instagram/callback?" + raw
        )
        assert b"400 Bad Request" in response
        with pytest.raises(AuthError, match="authorization_response"):
            future.result(timeout=2)
    assert attempt._state == "" and "SYNTHETIC_PRIVATE" not in caplog.text


@pytest.mark.parametrize(
    "mode",
    [
        "line",
        "headers",
        "header_count",
        "header_line",
        "wrong_host",
        "duplicate_host",
        "body",
        "chunked",
        "plain_http",
    ],
)
def test_transport_bounds_and_tls_rejection_preserve_oauth_boundary(local, mode):
    config, factory, client, _ = local
    attempt = AuthorizationAttempt(config)
    target = "/oauth/instagram/callback?" + query(attempt)
    headers = {
        "headers": "X-Test: " + "x" * 16_385 + "\r\n",
        "header_count": "".join(f"X-{n}: x\r\n" for n in range(33)),
        "header_line": "X-Test: " + "x" * 8193 + "\r\n",
        "wrong_host": "Host: attacker.invalid\r\n",
        "duplicate_host": "Host: localhost:8765\r\n",
        "body": "Content-Length: 1\r\n",
        "chunked": "Transfer-Encoding: chunked\r\n",
    }.get(mode, "")
    with factory() as receiver, ThreadPoolExecutor(max_workers=1) as pool:
        endpoint = address(receiver)
        future = pool.submit(receiver.wait, attempt)
        try:
            if mode == "plain_http":
                with socket.create_connection(endpoint, timeout=2) as stream:
                    stream.sendall(b"GET / HTTP/1.1\r\n\r\n")
                    stream.recv(1024)
            else:
                request(
                    client,
                    endpoint,
                    "x" * 8301 if mode == "line" else target,
                    headers=headers,
                )
        except OSError:
            pass
        assert b"200 OK" in request(client, endpoint, target)
        assert future.result(timeout=2) == "SYNTHETIC_PRIVATE_CODE"


def test_timeout_closes_socket_and_cancels_state(local):
    config, factory, _, _ = local
    attempt = AuthorizationAttempt(config)
    with factory(timeout=0.03) as receiver:
        with pytest.raises(AuthError, match="callback_timeout"):
            receiver.wait(attempt)
    assert attempt._state == ""


def test_slow_partial_headers_are_bounded(local):
    config, factory, client, _ = local
    attempt = AuthorizationAttempt(config)
    start = time.monotonic()
    with factory(timeout=0.1) as receiver, ThreadPoolExecutor(max_workers=1) as pool:
        endpoint = address(receiver)
        future = pool.submit(receiver.wait, attempt)
        with socket.create_connection(endpoint, timeout=2) as connection:
            with client.wrap_socket(connection, server_hostname="localhost") as tls:
                tls.sendall(b"GET /oauth/instagram/callback HTTP/1.1\r\n")
                with pytest.raises(AuthError, match="callback_timeout"):
                    future.result(timeout=2)
    assert time.monotonic() - start < 2


def test_stalled_tls_handshake_is_bounded(local):
    config, factory, _, _ = local
    with factory(timeout=0.1) as receiver, ThreadPoolExecutor(max_workers=1) as pool:
        endpoint = address(receiver)
        future = pool.submit(receiver.wait, AuthorizationAttempt(config))
        with socket.create_connection(endpoint, timeout=2):
            with pytest.raises(AuthError, match="callback_timeout"):
                future.result(timeout=2)


def test_duplicate_pipelined_callback_consumes_once(local, monkeypatch):
    config, factory, client, _ = local
    attempt = AuthorizationAttempt(config)
    raw = query(attempt)
    consume = Mock(wraps=attempt.consume)
    monkeypatch.setattr(attempt, "consume", consume)
    packet = (
        f"GET /oauth/instagram/callback?{raw} HTTP/1.1\r\nHost: localhost:8765\r\n\r\n"
    ).encode()
    with factory() as receiver, ThreadPoolExecutor(max_workers=1) as pool:
        endpoint = address(receiver)
        future = pool.submit(receiver.wait, attempt)
        with socket.create_connection(endpoint, timeout=2) as connection:
            with client.wrap_socket(connection, server_hostname="localhost") as tls:
                tls.sendall(packet + packet)
                assert b"200 OK" in tls.recv(4096)
        assert future.result(timeout=2) == "SYNTHETIC_PRIVATE_CODE"
    consume.assert_called_once_with(raw)


@pytest.mark.parametrize("timeout", [False, 0, -1, 301, float("nan"), float("inf")])
def test_invalid_timeouts_fail_before_socket_creation(timeout):
    with pytest.raises(AuthError):
        callback.LocalCallbackReceiver(
            AuthConfig("100000000000001", REDIRECT_URI), timeout=timeout
        )


def test_ctrl_c_cleanup_and_receiver_cannot_restart(local, monkeypatch):
    config, factory, _, _ = local
    attempt = AuthorizationAttempt(config)
    receiver = factory()
    with pytest.raises(KeyboardInterrupt):
        with receiver:
            assert receiver._listener is not None
            monkeypatch.setattr(
                receiver._listener, "accept", Mock(side_effect=KeyboardInterrupt)
            )
            receiver.wait(attempt)
    assert attempt._state == ""
    with pytest.raises(AuthError, match="callback_used"):
        receiver.__enter__()


def test_startup_failure_closes_socket(local, monkeypatch):
    _, factory, _, listeners = local
    receiver = factory()
    original = receiver._listener_factory

    def listener():
        result = original()
        monkeypatch.setattr(
            result, "bind", Mock(side_effect=OSError("SYNTHETIC_PRIVATE"))
        )
        return result

    receiver._listener_factory = listener
    with pytest.raises(AuthError) as caught:
        receiver.__enter__()
    assert caught.value.__context__ is None and listeners[-1].fileno() == -1
    assert "SYNTHETIC_PRIVATE" not in str(caught.value)


def test_request_count_is_bounded(local):
    config, factory, client, _ = local
    attempt = AuthorizationAttempt(config)
    with factory(timeout=5) as receiver, ThreadPoolExecutor(max_workers=1) as pool:
        endpoint = address(receiver)
        future = pool.submit(receiver.wait, attempt)
        for _ in range(32):
            request(client, endpoint, "/unknown")
        with pytest.raises(AuthError, match="callback_request_limit"):
            future.result(timeout=2)
    assert attempt._state == ""


def test_trust_preflight_requires_native_ssl_hostname_policy(certificates, monkeypatch):
    cert, key = certificates
    monkeypatch.setattr(callback.sys, "platform", "darwin")
    verify = Mock()
    monkeypatch.setattr(callback.subprocess, "run", verify)
    context = callback.trusted_context(
        AuthConfig("100000000000001", REDIRECT_URI, cert, key)
    )
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    args = verify.call_args.args[0]
    assert args == [
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
        str(cert),
    ]
    assert verify.call_args.kwargs["timeout"] == 5
    assert verify.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert "add-trusted-cert" not in args and str(key) not in args


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "relative",
        "permissions",
        "directory_permissions",
        "symlink",
        "large",
        "wrong_key",
        "untrusted",
        "verify_timeout",
        "platform",
        "checkout",
        "other_checkout",
        "combined_cert",
        "combined_key",
        "same_file",
    ],
)
def test_tls_preflight_fails_closed_before_listener(
    certificates, tmp_path, monkeypatch, mode
):
    cert, key = certificates
    local_cert, local_key = tmp_path / "cert.pem", tmp_path / "key.pem"
    local_cert.write_bytes(cert.read_bytes())
    local_key.write_bytes(key.read_bytes())
    local_cert.chmod(0o600)
    local_key.chmod(0o600)
    config = AuthConfig("100000000000001", REDIRECT_URI, local_cert, local_key)
    monkeypatch.setattr(callback.sys, "platform", "darwin")
    verify = Mock()
    monkeypatch.setattr(callback.subprocess, "run", verify)
    if mode == "missing":
        config = replace(config, tls_certificate=None, tls_private_key=None)
    if mode == "relative":
        config = replace(config, tls_private_key=Path("key.pem"))
    if mode == "permissions":
        local_key.chmod(0o644)
    if mode == "directory_permissions":
        tmp_path.chmod(0o755)
    if mode == "symlink":
        local_key.unlink()
        local_key.symlink_to(key)
    if mode == "large":
        local_cert.write_bytes(b"x" * 65_537)
    if mode == "wrong_key":
        local_key.write_text("SYNTHETIC_PRIVATE")
    if mode == "untrusted":
        verify.side_effect = subprocess.CalledProcessError(1, "SYNTHETIC_PRIVATE")
    if mode == "verify_timeout":
        verify.side_effect = subprocess.TimeoutExpired("SYNTHETIC_PRIVATE", 5)
    if mode == "platform":
        monkeypatch.setattr(callback.sys, "platform", "linux")
    if mode == "checkout":
        monkeypatch.setattr(callback, "_CHECKOUT", tmp_path)
    if mode == "other_checkout":
        (tmp_path / ".git").mkdir()
    if mode == "combined_cert":
        local_cert.write_bytes(cert.read_bytes() + key.read_bytes())
    if mode == "combined_key":
        local_key.write_bytes(key.read_bytes() + cert.read_bytes())
    if mode == "same_file":
        config = replace(config, tls_private_key=local_cert)
    listener = Mock()
    with pytest.raises(AuthError, match="localhost_certificate") as caught:
        with callback.LocalCallbackReceiver(config, listener_factory=listener):
            pytest.fail("Invalid TLS started listener")
    listener.assert_not_called()
    assert caught.value.__context__ is None and "SYNTHETIC_PRIVATE" not in str(
        caught.value
    )
