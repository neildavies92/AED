"""One-file staging with local sockets and isolated fake provider processes."""

import http.client
import json
import logging
import os
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest
from PIL import Image

import ask_every_day.media_staging as module
from ask_every_day.domain import AppConfig, Post
from ask_every_day.logging_config import JSONFormatter
from ask_every_day.repository import Repository, open_repository


@pytest.fixture
def source(tmp_path):
    config = AppConfig(tmp_path / "posts.db", tmp_path / "assets", "@example")
    config.assets_path.mkdir()
    output = BytesIO()
    with Image.new("RGB", (1080, 1350), (244, 240, 232)) as image:
        image.save(output, format="JPEG")
    data = output.getvalue()
    (config.assets_path / "AED-0001.jpg").write_bytes(data)
    repository = Mock(spec=Repository)
    repository.get_post.return_value = Post(
        "AED-0001", "Private prompt?", "A", "B", "Classic"
    )
    return repository, config, data


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original = socket.socket.connect
    resolve = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("External DNS forbidden")
        return resolve(host, *args, **kwargs)

    def connect(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("External network forbidden")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def test_canonical_jpeg(source):
    repository, config, data = source
    assert module._jpeg(repository, config, "AED-0001") == data
    repository.get_post.assert_called_once_with("AED-0001")


@pytest.mark.parametrize(
    "post_id", ["../AED-0001", "AED-0000", "AED-１２３４", "AED-0001.jpg", None]
)
def test_invalid_id_before_repository(source, post_id):
    repository, config, _ = source
    with pytest.raises(module.StagingError):
        with module.stage_media(repository, config, post_id):
            pytest.fail("Invalid input became ready")
    repository.get_post.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    ["unknown", "missing", "malformed", "symlink", "directory", "fifo", "root_symlink"],
)
def test_source_rejected_before_origin(source, monkeypatch, kind):
    repository, config, _ = source
    asset = config.assets_path / "AED-0001.jpg"
    if kind == "unknown":
        repository.get_post.return_value = None
    elif kind == "malformed":
        asset.write_bytes(b"Private invalid image payload")
    else:
        asset.unlink()
        if kind == "symlink":
            asset.symlink_to(config.database_path)
        elif kind == "directory":
            asset.mkdir()
        elif kind == "fifo":
            os.mkfifo(asset)
        elif kind == "root_symlink":
            moved = config.assets_path.with_name("actual")
            config.assets_path.rename(moved)
            config.assets_path.symlink_to(moved, target_is_directory=True)
    origin = Mock()
    monkeypatch.setattr(module, "_origin", origin)
    with pytest.raises(module.StagingError) as caught:
        with module.stage_media(repository, config, "AED-0001"):
            pytest.fail("Invalid asset became ready")
    assert caught.value.__context__ is None
    assert "Private" not in str(caught.value)
    origin.assert_not_called()


def test_real_local_origin_exact_route_and_shutdown(source, capsys):
    _, _, data = source
    with module._origin(data) as (port, route):
        assert len(route) > 40 and route.endswith(".jpg")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        for method, path, status in [
            ("GET", route, 200),
            ("HEAD", route, 200),
            ("GET", "/", 404),
            ("GET", "/../aed.toml", 404),
            ("GET", route + "?extra=1", 404),
            ("POST", route, 405),
            ("PUT", route, 405),
            ("DELETE", route, 405),
        ]:
            connection.request(method, path)
            response = connection.getresponse()
            assert response.status == status
            body = response.read()
            if status == 200:
                assert response.getheader("Content-Type") == "image/jpeg"
                assert response.getheader("Content-Length") == str(len(data))
                assert response.getheader("Cache-Control") == "no-store, max-age=0"
                assert body == (b"" if method == "HEAD" else data)
            else:
                assert body == b""
        connection.close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)
    assert not any(t.name == "aed-media-origin" for t in threading.enumerate())
    assert capsys.readouterr().err == ""


def test_origin_closes_partial_header_connections(source, monkeypatch):
    _, _, data = source
    accepted = threading.Event()
    original = module._Origin.get_request

    def get_request(server):
        result = original(server)
        accepted.set()
        return result

    monkeypatch.setattr(module._Origin, "get_request", get_request)
    with module._origin(data) as (port, route):
        connection = socket.create_connection(("127.0.0.1", port), timeout=2)
        connection.sendall(f"GET {route} HTTP/1.1\r\nHost:".encode())
        assert accepted.wait(2)
    assert connection.recv(1) == b""
    connection.close()
    assert not any(t.name == "aed-media-origin" for t in threading.enumerate())


@pytest.mark.parametrize(
    "value",
    [
        "http://a.trycloudflare.com",
        "https://a.trycloudflare.com.evil.test",
        "https://user@a.trycloudflare.com",
        "https://a.trycloudflare.com:443",
        "https://a.trycloudflare.com/",
        "https://a.trycloudflare.com#x",
        "https://a.trycloudflare.com?x",
        "https://-a.trycloudflare.com",
        "https://a_b.trycloudflare.com",
        "https://a.b.trycloudflare.com",
    ],
)
def test_invalid_provider_url(value):
    with pytest.raises(module.StagingError):
        module._provider_url(value)


def test_provider_url():
    assert (
        module._provider_url("https://quiet-test-123.trycloudflare.com")
        == "https://quiet-test-123.trycloudflare.com"
    )


def fake_binary(tmp_path, monkeypatch, code):
    binary = tmp_path / "fake-cloudflared"
    binary.write_text(f"#!{sys.executable}\n" + code, encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setattr(module.shutil, "which", lambda name: str(binary))
    return binary


def test_missing_binary(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    with pytest.raises(module.StagingError, match="install it separately"):
        with module._tunnel(12345, 1):
            pytest.fail("Missing binary started")


def test_tunnel_process_and_temporary_config_cleanup(tmp_path, monkeypatch):
    fake_binary(
        tmp_path,
        monkeypatch,
        """import os, pathlib, sys, time
assert sys.argv[1] == 'tunnel'
assert sys.argv[-2:] == ['--url', 'http://127.0.0.1:12345']
assert '--no-autoupdate' in sys.argv
assert pathlib.Path(sys.argv[3]).read_text() == '{}\\n'
assert os.environ['HOME'] == str(pathlib.Path(sys.argv[3]).parent)
print('raw private provider output', flush=True)
print('https://quiet-test.trycloudflare.com', flush=True)
while True: time.sleep(1)
""",
    )
    with module._tunnel(12345, 2) as (url, process):
        assert url == "https://quiet-test.trycloudflare.com"
        assert process.poll() is None
        arguments = process.args
        assert isinstance(arguments, list) and isinstance(arguments[3], str)
        assert "--protocol" in arguments
        assert arguments[arguments.index("--protocol") + 1] == "http2"
        directory = Path(arguments[3]).parent
        assert directory.is_dir()
    assert process.poll() is not None
    assert not directory.exists()
    assert not any(t.name == "aed-tunnel-output" for t in threading.enumerate())


@pytest.mark.parametrize("mode", ["early_exit", "timeout", "malformed"])
def test_tunnel_startup_failures_cleanup(tmp_path, monkeypatch, mode):
    code = "import time\n"
    if mode == "early_exit":
        code += "raise SystemExit(1)\n"
    elif mode == "malformed":
        code += "print('http://bad.trycloudflare.com', flush=True)\n"
    code += "time.sleep(30)\n"
    fake_binary(tmp_path, monkeypatch, code)
    processes = []
    real = module.subprocess.Popen

    def popen(*args, **kwargs):
        assert not kwargs.get("shell", False)
        process = real(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    with pytest.raises(module.StagingError):
        with module._tunnel(12345, 0.3):
            pytest.fail("Provider became ready")
    assert all(p.poll() is not None and p.stdout.closed for p in processes)
    assert not any(t.name == "aed-tunnel-output" for t in threading.enumerate())


def test_force_kill_fallback(monkeypatch):
    process = Mock(pid=12345)
    process.wait.side_effect = [subprocess.TimeoutExpired("fake", 5), 0]
    kill = Mock()
    monkeypatch.setattr(module.os, "killpg", kill)
    module._stop(process)
    assert kill.call_args_list[0].args == (12345, signal.SIGTERM)
    assert (12345, signal.SIGKILL) in [call.args for call in kill.call_args_list]
    assert process.wait.call_count == 2


def test_exited_leader_descendant_is_stopped(tmp_path, monkeypatch):
    marker = tmp_path / "child-pid"
    fake_binary(
        tmp_path,
        monkeypatch,
        f"""import os, signal, time
pid = os.fork()
if pid == 0:
    open({str(marker)!r}, 'w').write(str(os.getpid()))
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print('child-ready', flush=True)
    while True: time.sleep(1)
else:
    time.sleep(0.1)
    os._exit(1)
""",
    )
    with pytest.raises(module.StagingError, match="exited before readiness"):
        with module._tunnel(12345, 2):
            pytest.fail("Exited leader became ready")
    child_pid = int(marker.read_text())
    deadline = time.monotonic() + 2
    while True:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(child_pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
            break
        assert time.monotonic() < deadline, "Tunnel child is still running"
        time.sleep(0.01)
    assert not any(t.name == "aed-tunnel-output" for t in threading.enumerate())


class Response(BytesIO):
    def __init__(self, data, status=200, content_type="image/jpeg", length=None):
        super().__init__(data)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(data) if length is None else length)


def test_public_fetch_exact_bytes_no_auth_and_no_redirect(source, monkeypatch):
    _, _, data = source
    response = Response(data)
    opener = Mock()
    opener.open.return_value = response
    build = Mock(return_value=opener)
    monkeypatch.setattr(module, "build_opener", build)
    module._verify("https://example.trycloudflare.com/private.jpg", data, 2)
    request = opener.open.call_args.args[0]
    assert request.header_items() == [("Accept", "image/jpeg")]
    assert opener.open.call_args.kwargs == {"timeout": 2}
    assert build.call_args.args[0].proxies == {}
    assert (
        build.call_args.args[1].redirect_request(
            None, None, 302, "", {}, "https://other.test"
        )
        is None
    )
    assert response.closed


@pytest.mark.parametrize(
    "kind", ["status", "type", "length", "short", "different", "oversize", "http_error"]
)
def test_public_fetch_rejects_incorrect_response(source, monkeypatch, kind):
    _, _, data = source
    response = Response(
        data + b"x"
        if kind == "oversize"
        else data[:-1]
        if kind == "short"
        else b"x" * len(data)
        if kind == "different"
        else data,
        status=401 if kind == "status" else 200,
        content_type="text/html" if kind == "type" else "image/jpeg",
        length=1 if kind == "length" else len(data),
    )
    opener = Mock()
    if kind == "http_error":
        opener.open.side_effect = HTTPError(
            "https://private.test", 302, "private", Message(), response
        )
    else:
        opener.open.return_value = response
    monkeypatch.setattr(module, "build_opener", lambda *args: opener)
    with pytest.raises(module.StagingError):
        module._verify("https://example.trycloudflare.com/private.jpg", data, 2)
    assert response.closed


def test_public_fetch_read_deadline(source, monkeypatch):
    _, _, data = source
    response = Response(data)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(module, "build_opener", lambda *args: opener)
    monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[0, 3]))
    with pytest.raises(module.StagingError, match="read deadline"):
        module._verify("https://example.trycloudflare.com/private.jpg", data, 2)
    assert response.closed


def test_lifecycle_and_safe_logs_on_downstream_exception(source, monkeypatch, caplog):
    repository, config, data = source
    stopped = []
    process = Mock()
    process.poll.return_value = None

    @contextmanager
    def tunnel(port, timeout, state=None):
        try:
            yield "https://private-example.trycloudflare.com", process
        finally:
            stopped.append(True)

    monkeypatch.setattr(module, "_tunnel", tunnel)
    verify = Mock()
    monkeypatch.setattr(module, "_verify", verify)
    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError, match="downstream"):
            with module.stage_media(repository, config, "AED-0001") as staged:
                assert staged.public_url.startswith(
                    "https://private-example.trycloudflare.com/"
                )
                assert "private-example" not in repr(staged)
                raise RuntimeError("downstream")
    assert stopped == [True]
    assert verify.call_args.args[1] == data
    events = [r.event for r in caplog.records if r.name == module.__name__]
    assert events == [
        "media_staging_started",
        "media_staging_ready",
        "media_staging_stopped",
    ]
    logs = json.dumps([JSONFormatter().format(r) for r in caplog.records])
    for private in [
        "private-example",
        "Private prompt",
        "trycloudflare.com",
        repr(data[:30]),
    ]:
        assert private not in logs
    assert not any(t.name == "aed-media-origin" for t in threading.enumerate())


def test_failed_fetch_sanitized_and_origin_closed(source, monkeypatch):
    repository, config, _ = source
    process = Mock()
    process.poll.return_value = None
    stopped = []

    @contextmanager
    def tunnel(port, timeout, state=None):
        try:
            yield "https://private-example.trycloudflare.com", process
        finally:
            stopped.append(True)

    monkeypatch.setattr(module, "_tunnel", tunnel)
    monkeypatch.setattr(
        module, "_verify", Mock(side_effect=ValueError("secret URL payload"))
    )
    with pytest.raises(module.StagingError) as caught:
        with module.stage_media(repository, config, "AED-0001", startup_timeout=0.02):
            pytest.fail("Bad fetch became ready")
    assert caught.value.__context__ is None
    assert "secret" not in str(caught.value)
    assert stopped == [True]
    assert not any(t.name == "aed-media-origin" for t in threading.enumerate())


@pytest.mark.parametrize("dns_recovers", [True, False])
def test_default_readiness_budget_handles_cached_dns_failure(
    source, monkeypatch, dns_recovers
):
    repository, config, data = source
    elapsed = 0.0
    stopped = []
    attempts = []
    process = Mock()
    process.poll.return_value = None

    def advance(seconds):
        nonlocal elapsed
        elapsed += seconds

    @contextmanager
    def tunnel(port, timeout, state=None):
        advance(5)
        try:
            yield "https://private-example.trycloudflare.com", process
        finally:
            stopped.append(True)

    def fetch(request, *, timeout):
        attempts.append(elapsed)
        assert 0 < timeout <= 15
        # Live evidence: about 5s to emit the URL, then 82s for DNS recovery.
        if not dns_recovers or elapsed < 87:
            raise URLError(socket.gaierror(socket.EAI_NONAME, "DNS not ready"))
        return Response(data)

    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: elapsed, sleep=advance)
    )
    monkeypatch.setattr(module, "_tunnel", tunnel)
    monkeypatch.setattr(module, "build_opener", lambda *args: Mock(open=fetch))
    if dns_recovers:
        with module.stage_media(repository, config, "AED-0001"):
            assert 87 <= elapsed < 120
    else:
        with pytest.raises(module.StagingError, match="readiness timed out"):
            with module.stage_media(repository, config, "AED-0001"):
                pytest.fail("Unresolved provider became ready")
        assert elapsed == 120
    assert attempts[0] == 5
    assert stopped == [True]
    assert not any(t.name == "aed-media-origin" for t in threading.enumerate())


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 121, True])
def test_invalid_timeout_before_source(source, timeout):
    repository, config, _ = source
    with pytest.raises(module.StagingError):
        with module.stage_media(
            repository, config, "AED-0001", startup_timeout=timeout
        ):
            pytest.fail("Invalid timeout accepted")
    repository.get_post.assert_not_called()


def test_cli_prints_url_then_interrupts_and_cleans_up(source, monkeypatch, capsys):
    _, config, _ = source
    with open_repository(config) as repository:
        repository.create_post(Post("AED-0001", "Question?", "A", "B", "Classic"))
    closed = []

    @contextmanager
    def stage(repository, settings, post_id):
        try:
            assert repository.get_post(post_id) is not None
            yield module.StagedMedia(
                post_id, "https://private-example.trycloudflare.com/token.jpg"
            )
        finally:
            closed.append(True)

    monkeypatch.setattr(module, "load_config", lambda path: config)
    monkeypatch.setattr(module, "configure_logging", lambda: None)
    monkeypatch.setattr(module, "stage_media", stage)
    monkeypatch.setattr(module.time, "sleep", Mock(side_effect=KeyboardInterrupt))
    monkeypatch.setattr(sys, "argv", ["media_staging", "AED-0001"])
    module.main()
    assert (
        capsys.readouterr().out
        == "https://private-example.trycloudflare.com/token.jpg\n"
    )
    assert closed == [True]


def test_missing_tunnel_cleans_real_origin(source, monkeypatch):
    repository, config, _ = source
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    real_origin = module._Origin
    servers = []

    def origin(address, handler):
        assert address == ("127.0.0.1", 0)
        server = real_origin(address, handler)
        servers.append(server)
        return server

    monkeypatch.setattr(module, "_Origin", origin)
    with pytest.raises(module.StagingError, match="install it separately"):
        with module.stage_media(repository, config, "AED-0001"):
            pytest.fail("Missing provider became ready")
    assert servers[0].socket.fileno() == -1
    assert not any(t.name == "aed-media-origin" for t in threading.enumerate())


def test_cli_missing_database_never_creates_it(source, monkeypatch, capsys):
    _, config, _ = source
    monkeypatch.setattr(module, "load_config", lambda path: config)
    monkeypatch.setattr(module, "configure_logging", lambda: None)
    monkeypatch.setattr(sys, "argv", ["media_staging", "AED-0001"])
    with pytest.raises(SystemExit) as caught:
        module.main()
    assert caught.value.code == 1
    assert not config.database_path.exists()
    output = capsys.readouterr()
    assert output.out == ""
    assert "Cannot stage media" in output.err


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    "error, category, error_type, code",
    [
        (socket.gaierror(8, "private URL"), "dns", "gaierror", 8),
        (TimeoutError("private URL"), "timeout", "TimeoutError", None),
        (ssl.SSLError(1, "private URL"), "tls", "SSLError", 1),
        (ConnectionRefusedError(61, "private URL"), "connect", "ConnectionError", 61),
        (OSError(5, "private URL"), "os_error", "OSError", 5),
        (ValueError("private URL"), "unknown", "Exception", None),
    ],
)
def test_verification_failure_classification(
    wrapped, error, category, error_type, code
):
    failure = module._classify_failure(URLError(error) if wrapped else error)
    assert failure == module._VerifyFailure(category, error_type, errno=code)
    assert "private URL" not in repr(failure)


def test_http_error_classification_without_headers_or_reason():
    error = HTTPError("private URL", 502, "private reason", Message(), None)
    error.headers["Set-Cookie"] = "private cookie"
    assert module._classify_failure(error) == module._VerifyFailure(
        "http_status", "HTTPError", http_status=502
    )


def test_unknown_and_cyclic_url_errors_are_safe():
    error = URLError("private URL")
    assert module._classify_failure(error).failure_category == "unknown"
    error.reason = error
    assert module._classify_failure(error).failure_category == "unknown"
    secret_type = type("private-secret", (Exception,), {})
    assert module._classify_failure(secret_type()).error_type == "Exception"


def test_real_urllib_connection_error_wrapping(monkeypatch):
    assert socket.timeout is TimeoutError

    def refused(self):
        raise ConnectionRefusedError(61, "private URL")

    monkeypatch.setattr(http.client.HTTPConnection, "connect", refused)
    with pytest.raises(URLError) as caught:
        module._verify("http://127.0.0.1:1/private", b"", 1)
    assert isinstance(caught.value.reason, ConnectionRefusedError)
    assert module._classify_failure(caught.value).failure_category == "connect"


@pytest.mark.parametrize(
    "response, category, status",
    [
        (Response(b"jpeg", status=502), "http_status", 502),
        (Response(b"jpeg", content_type="text/html"), "content_type", None),
        (Response(b"jpeg", length=3), "content_length", None),
        (Response(b"nope"), "byte_mismatch", None),
    ],
)
def test_response_failure_categories(monkeypatch, response, category, status):
    monkeypatch.setattr(
        module, "build_opener", lambda *args: Mock(open=Mock(return_value=response))
    )
    with pytest.raises(module.StagingError) as caught:
        module._verify("https://private.test/private.jpg", b"jpeg", 1)
    assert module._classify_failure(caught.value) == module._VerifyFailure(
        category, "StagingError", http_status=status
    )
    assert response.closed


def test_read_deadline_classification(source, monkeypatch):
    _, _, data = source
    response = Response(data)
    monkeypatch.setattr(
        module, "build_opener", lambda *args: Mock(open=Mock(return_value=response))
    )
    monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[0, 3]))
    with pytest.raises(module.StagingError) as caught:
        module._verify("https://private.test/private.jpg", data, 2)
    assert module._classify_failure(caught.value).failure_category == "read_deadline"


def test_readiness_diagnostics_are_safe_and_rate_limited(source, monkeypatch, caplog):
    repository, config, data = source
    elapsed = 0.0
    attempts = 0
    process = Mock()
    process.poll.return_value = None
    secret = "https://private.test/private-route caption hashtag token cookie"

    def advance(seconds):
        nonlocal elapsed
        elapsed += seconds

    @contextmanager
    def tunnel(port, timeout, state=None):
        yield "https://private-example.trycloudflare.com", process

    def verify(*args):
        nonlocal attempts
        attempts += 1
        error = socket.gaierror(8, secret) if attempts % 2 else ssl.SSLError(1, secret)
        raise URLError(error)

    monkeypatch.setattr(module, "_tunnel", tunnel)
    monkeypatch.setattr(module, "_verify", verify)
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: elapsed, sleep=advance)
    )
    with caplog.at_level(logging.INFO):
        with pytest.raises(
            module.StagingError, match="last failure: (dns|tls)"
        ) as caught:
            with module.stage_media(repository, config, "AED-0001"):
                pytest.fail("Unverified provider became ready")
    records = [r for r in caplog.records if r.name == module.__name__]
    retries = [r for r in records if r.event == "media_staging_readiness_retry"]
    assert 1 <= len(retries) <= 12
    assert all(
        b.elapsed_ms - a.elapsed_ms >= 9999 for a, b in zip(retries, retries[1:])
    )
    final = json.loads(JSONFormatter().format(records[-1]))
    assert final["event"] == "media_staging_failed"
    assert final["attempt_count"] == attempts
    assert final["elapsed_ms"] == 120000
    assert final["failure_category"] in {"dns", "tls"}
    assert final["errno"] in {1, 8}
    output = caplog.text + str(caught.value) + repr(caught.value.__context__)
    output += "".join(JSONFormatter().format(r) for r in records)
    for private in (
        secret,
        "private-route",
        "private-example",
        "Private prompt",
        repr(data),
    ):
        assert private not in output


def test_dns_recovery_does_not_accept_disconnected_tunnel(source, monkeypatch, caplog):
    repository, config, _ = source
    elapsed = 0.0
    process = Mock()
    process.poll.return_value = None
    stopped = []

    def advance(seconds):
        nonlocal elapsed
        elapsed += seconds

    @contextmanager
    def tunnel(port, timeout, state=None):
        advance(5)
        try:
            yield "https://private-example.trycloudflare.com", process
        finally:
            stopped.append(True)

    def fetch(request, *, timeout):
        assert 0 < timeout <= 15
        if elapsed < 80:
            raise URLError(socket.gaierror(8, "private hostname"))
        raise HTTPError(request.full_url, 530, "private response", Message(), BytesIO())

    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: elapsed, sleep=advance)
    )
    monkeypatch.setattr(module, "_tunnel", tunnel)
    monkeypatch.setattr(module, "build_opener", lambda *args: Mock(open=fetch))
    with caplog.at_level(logging.INFO):
        with pytest.raises(module.StagingError, match="last failure: http_status"):
            with module.stage_media(repository, config, "AED-0001"):
                pytest.fail("Disconnected tunnel became ready")
    records = [r for r in caplog.records if r.name == module.__name__]
    assert records[1].failure_category == "dns"
    assert records[-1].failure_category == "http_status"
    assert records[-1].http_status == 530
    assert elapsed == 120
    assert stopped == [True]
    assert not any(t.name == "aed-media-origin" for t in threading.enumerate())


@pytest.mark.parametrize(
    "status, body, code",
    [
        (530, b"<h1>Error 1033</h1>", 1033),
        (530, b"error code: 1016\n", 1016),
        (530, b"Error 1999", 1999),
        (530, b"Error 999", None),
        (530, b"Error 10330", None),
        (530, b"Error 1033abc", None),
        (530, b"<p>Ray 1033 year 1999</p>", None),
        (530, b"malformed\xff body", None),
        (530, b"", None),
        (530, b"x" * module._ERROR_BODY_LIMIT + b" Error 1033", None),
        (503, b"Error 1033", None),
    ],
)
def test_bounded_provider_error_classification(monkeypatch, status, body, code):
    class BoundedBody(BytesIO):
        total = 0

        def read1(self, size: int | None = -1):
            assert size is not None and 0 < size <= 1024
            result = super().read1(size)
            self.total += len(result)
            assert self.total <= module._ERROR_BODY_LIMIT
            return result

    stream = BoundedBody(body)
    error = HTTPError(
        "https://private.test/secret.jpg", status, "private", Message(), stream
    )
    monkeypatch.setattr(
        module, "build_opener", lambda *args: Mock(open=Mock(side_effect=error))
    )
    with pytest.raises(module.StagingError) as caught:
        module._verify(error.url, b"private image", 1)
    failure = module._classify_failure(caught.value)
    assert failure.http_status == status
    assert failure.provider_error_code == code
    assert stream.closed
    assert stream.total == (
        min(len(body), module._ERROR_BODY_LIMIT) if status == 530 else 0
    )
    assert "private" not in repr(failure)


@pytest.mark.parametrize("mode", ["missing", "read_error", "expired"])
def test_provider_body_failure_is_optional_and_closed(monkeypatch, mode):
    stream = BytesIO(b"Error 1033") if mode != "missing" else None
    error = HTTPError(
        "https://private.test/secret.jpg", 530, "private", Message(), stream
    )
    if mode == "read_error":
        monkeypatch.setattr(error, "read1", Mock(side_effect=OSError("private body")))
    if mode == "expired":
        monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[0, 2]))
    close = Mock(wraps=error.close)
    monkeypatch.setattr(error, "close", close)
    monkeypatch.setattr(
        module, "build_opener", lambda *args: Mock(open=Mock(side_effect=error))
    )
    with pytest.raises(module.StagingError) as caught:
        module._verify(error.url, b"private image", 1)
    assert module._classify_failure(caught.value).provider_error_code is None
    close.assert_called_once()
    if stream is not None:
        assert stream.closed


def test_connector_state_tracks_registration_and_disconnect_without_secrets(
    monkeypatch,
):
    monkeypatch.setattr(module.time, "monotonic", lambda: 12.5)
    state = module._TunnelState(10)
    secret = b" connection=private-id ip=192.0.2.1 location=private https://private.test/secret"
    state.observe(
        b"2026-10-21T00:00:00Z INF Registered tunnel connection connIndex=0" + secret
    )
    state.observe(
        b"2026-10-21T00:00:00Z INF Registered tunnel connection connIndex=1" + secret
    )
    state.observe(
        b"2026-10-21T00:00:00Z INF Unregistered tunnel connection connIndex=0" + secret
    )
    assert state.snapshot()["connector_registered"] is True
    state.observe(
        b"2026-10-21T00:00:00Z ERR Connection terminated connIndex=1" + secret
    )
    snapshot = state.snapshot()
    assert snapshot["connector_registered"] is False
    assert snapshot["connector_registration_count"] == 2
    assert snapshot["connector_disconnect_count"] == 2
    assert snapshot["connector_first_registered_ms"] == 2500
    assert "private" not in repr(state) + repr(snapshot)
    before = snapshot.copy()
    for line in (
        b"raw Registered tunnel connection connIndex=0",
        b"2026-10-21 INF Registered tunnel connection connIndex=999",
        b"2026-10-21 INF Registered tunnel connection connIndex=0oops",
        b"2026-10-21 INF request error=Registered tunnel connection connIndex=0",
    ):
        state.observe(line)
    assert state.snapshot() == before


def test_output_reader_collects_safe_state_and_keeps_cleanup(tmp_path, monkeypatch):
    fake_binary(
        tmp_path,
        monkeypatch,
        """import time
print('https://quiet-test.trycloudflare.com', flush=True)
print('2026-10-21T00:00:00Z INF Registered tunnel connection connIndex=0 connection=private-id ip=192.0.2.1 protocol=http2', flush=True)
while True: time.sleep(1)
""",
    )
    state = module._TunnelState(time.monotonic())
    with module._tunnel(12345, 2, state) as (_, process):
        deadline = time.monotonic() + 1
        while (
            not state.snapshot()["connector_registered"] and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert state.snapshot()["connector_registered"] is True
        assert state.snapshot()["connector_registration_count"] == 1
    assert process.poll() is not None
    assert not any(t.name == "aed-tunnel-output" for t in threading.enumerate())


def test_provider_code_and_connector_logs_never_include_raw_data(
    source, monkeypatch, caplog
):
    repository, config, data = source
    elapsed = 0.0
    secret = b"<html>https://private.test/private-route Error 1033 private-id</html>"
    process = Mock()
    process.poll.return_value = None

    def advance(seconds):
        nonlocal elapsed
        elapsed += seconds

    @contextmanager
    def tunnel(port, timeout, state):
        state.observe(
            b"2026-10-21 INF Registered tunnel connection connIndex=0 connection=private-id"
        )
        try:
            yield "https://private-example.trycloudflare.com", process
        finally:
            state.observe(b"2026-10-21 INF Unregistered tunnel connection connIndex=0")

    def fetch(*args, **kwargs):
        raise HTTPError(
            "https://private.test/private-route",
            530,
            "private",
            Message(),
            BytesIO(secret),
        )

    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: elapsed, sleep=advance)
    )
    monkeypatch.setattr(module, "_tunnel", tunnel)
    monkeypatch.setattr(module, "build_opener", lambda *args: Mock(open=fetch))
    with caplog.at_level(logging.INFO):
        with pytest.raises(module.StagingError):
            with module.stage_media(repository, config, "AED-0001"):
                pytest.fail("Unverified response became ready")
    records = [r for r in caplog.records if r.name == module.__name__]
    final = json.loads(JSONFormatter().format(records[-1]))
    assert final["provider_error_code"] == 1033
    assert final["connector_registered"] is True
    assert final["connector_registration_count"] == 1
    assert len([r for r in records if r.event == "media_staging_readiness_retry"]) <= 12
    output = caplog.text + "".join(JSONFormatter().format(r) for r in records)
    for value in (
        "private.test",
        "private-route",
        "private-id",
        "private-example",
        "<html>",
        repr(data),
        secret.decode(),
    ):
        assert value not in output


def test_provider_error_body_respects_existing_fetch_deadline(monkeypatch):
    stream = BytesIO(b"x" * 2048 + b" Error 1033")
    error = HTTPError("https://private.test/secret", 530, "private", Message(), stream)
    read = Mock(wraps=error.read1)
    monkeypatch.setattr(error, "read1", read)
    monkeypatch.setattr(module.time, "monotonic", Mock(side_effect=[0, 0.5, 2]))
    monkeypatch.setattr(
        module, "build_opener", lambda *args: Mock(open=Mock(side_effect=error))
    )
    with pytest.raises(module.StagingError) as caught:
        module._verify(error.url, b"private image", 1)
    assert module._classify_failure(caught.value).provider_error_code is None
    read.assert_called_once_with(1024)
    assert stream.closed


def test_provider_error_body_closes_on_interruption(monkeypatch):
    stream = BytesIO(b"Error 1033")
    error = HTTPError("https://private.test/secret", 530, "private", Message(), stream)
    monkeypatch.setattr(error, "read1", Mock(side_effect=KeyboardInterrupt))
    monkeypatch.setattr(
        module, "build_opener", lambda *args: Mock(open=Mock(side_effect=error))
    )
    with pytest.raises(KeyboardInterrupt):
        module._verify(error.url, b"private image", 1)
    assert stream.closed


def test_transport_failure_clears_observed_registration():
    state = module._TunnelState(time.monotonic())
    state.observe(b"2026-10-21 INF Registered tunnel connection connIndex=0")
    state.observe(
        b'2026-10-21 ERR Unable to establish connection with Cloudflare edge error="private timeout" connIndex=0'
    )
    assert state.snapshot()["connector_registered"] is False
    assert state.snapshot()["connector_transport_failures"] == 1


@pytest.mark.parametrize("expected", ["0" * 64, "not-a-digest"])
def test_approved_digest_mismatch_never_starts_public_origin(
    source, monkeypatch, expected
):
    repository, config, _ = source
    origin = Mock(side_effect=AssertionError("must not expose changed content"))
    monkeypatch.setattr(module, "_origin", origin)
    with pytest.raises(module.StagingError):
        with module.stage_media(
            repository, config, "AED-0001", expected_sha256=expected
        ):
            pytest.fail("changed JPEG was exposed")
    origin.assert_not_called()
