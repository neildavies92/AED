"""One-file, operator-controlled HTTPS staging through Cloudflare Quick Tunnel."""

import argparse
import logging
import math
import os
import queue
import re
import secrets
import select
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ask_every_day.config import load_config
from ask_every_day.domain import AppConfig
from ask_every_day.jpeg_export import MAX_JPEG_BYTES, validate_jpeg
from ask_every_day.logging_config import configure_logging
from ask_every_day.repository import Repository, SQLiteRepository

logger = logging.getLogger(
    "ask_every_day.media_staging" if __name__ == "__main__" else __name__
)


class StagingError(ValueError):
    """Safe operational diagnostic; never includes a URL, payload or raw output."""


@dataclass(frozen=True)
class StagedMedia:
    post_id: str
    public_url: str = field(repr=False)


def _timeout(value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 < value <= 120
    ):
        raise StagingError("Timeout must be finite, positive and at most 120 seconds")


def _jpeg(repository: Repository, config: AppConfig, post_id: str) -> bytes:
    if (
        not isinstance(post_id, str)
        or re.fullmatch(r"AED-[0-9]{4}", post_id) is None
        or post_id == "AED-0000"
    ):
        raise StagingError("Expected a Post ID from AED-0001 through AED-9999")
    if repository.get_post(post_id) is None:
        raise StagingError("Post not found; import content first")
    # Pin the directory and file descriptors rather than following a replaced symlink.
    root_fd = os.open(config.assets_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(
            f"{post_id}.jpg",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=root_fd,
        )
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise StagingError("Canonical JPEG must be a regular file")
            data = stream.read(MAX_JPEG_BYTES + 1)
    finally:
        os.close(root_fd)
    validate_jpeg(data)
    return data


class _Origin(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, *args, **kwargs):
        self.active = set()
        self.active_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        with self.active_lock:
            self.active.add(connection)
        return connection, address

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            with self.active_lock:
                self.active.discard(request)

    def close_connections(self):
        with self.active_lock:
            connections = tuple(self.active)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def handle_error(self, request, client_address):
        # Default traceback output can contain request-local sensitive data.
        pass


@contextmanager
def _origin(data: bytes) -> Iterator[tuple[int, str]]:
    route = "/" + secrets.token_urlsafe(32) + ".jpg"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send_error(self, code, message=None, explain=None):
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_HEAD(self):
            if self.path != route:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

        def do_GET(self):
            self.do_HEAD()
            if self.path == route:
                self.wfile.write(data)

        def reject(self):
            self.send_error(405 if self.path == route else 404)

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_CONNECT = do_TRACE = (
            reject
        )

    server = _Origin(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="aed-media-origin",
    )
    try:
        thread.start()
        yield server.server_port, route
    finally:
        if thread.is_alive():
            server.shutdown()
        server.close_connections()
        server.server_close()
        if thread.ident is not None:
            thread.join()


def _provider_url(value: str) -> str:
    # A base URL only. No credentials, port, path, query, fragment or suffix host.
    if (
        re.fullmatch(
            r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com", value
        )
        is None
    ):
        raise StagingError("Tunnel returned an invalid provider URL")
    return value


def _drain(stream, discovered: queue.Queue[str], stop: threading.Event) -> None:
    buffer = b""
    try:
        while not stop.is_set():
            if not select.select([stream], [], [], 0.1)[0]:
                continue
            chunk = os.read(stream.fileno(), 4096)
            if not chunk:
                break
            buffer = (buffer + chunk)[-16384:]
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                for candidate in re.findall(rb"https?://[^\s|]+", line):
                    if b"trycloudflare" in candidate and discovered.empty():
                        try:
                            discovered.put_nowait(candidate.decode("ascii"))
                        except (UnicodeError, queue.Full):
                            pass
    except (OSError, ValueError):
        pass


def _stop(process: subprocess.Popen) -> None:
    def send(sig: int) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        send(signal.SIGKILL)
        process.wait(timeout=5)
    finally:
        # Also stop any descendant that outlived the process-group leader.
        send(signal.SIGKILL)


@contextmanager
def _tunnel(port: int, timeout: float) -> Iterator[tuple[str, subprocess.Popen]]:
    binary = shutil.which("cloudflared")
    if binary is None:
        raise StagingError(
            "cloudflared is missing; install it separately following "
            "https://developers.cloudflare.com/tunnel/downloads/ then retry"
        )
    with tempfile.TemporaryDirectory(prefix="aed-tunnel-") as directory:
        config = Path(directory) / "config.yml"
        config.write_text("{}\n", encoding="utf-8")
        process = subprocess.Popen(
            [
                binary,
                "tunnel",
                "--config",
                str(config),
                "--no-autoupdate",
                "--url",
                f"http://127.0.0.1:{port}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=True,
            env={"HOME": directory, "PATH": os.defpath},
        )
        assert process.stdout is not None
        discovered: queue.Queue[str] = queue.Queue(maxsize=1)
        stop_reader = threading.Event()
        reader = threading.Thread(
            target=_drain,
            args=(process.stdout, discovered, stop_reader),
            name="aed-tunnel-output",
        )
        try:
            reader.start()
            deadline = time.monotonic() + timeout
            while True:
                if process.poll() is not None:
                    raise StagingError("Tunnel exited before readiness")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StagingError("Tunnel startup timed out without a public URL")
                try:
                    base = _provider_url(discovered.get(timeout=min(remaining, 0.1)))
                    break
                except queue.Empty:
                    continue
            yield base, process
        finally:
            try:
                _stop(process)
            finally:
                # Killing the entire session also closes inherited pipe writers.
                stop_reader.set()
                if reader.ident is not None:
                    reader.join(timeout=2)
                process.stdout.close()
                if reader.is_alive():
                    raise StagingError("Tunnel output reader did not stop")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _verify(url: str, data: bytes, timeout: float) -> None:
    # No cookies, auth handlers, environment proxy credentials or redirects.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(url, headers={"Accept": "image/jpeg"})
    deadline = time.monotonic() + timeout
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        error.close()
        raise StagingError("Public fetch returned an unsuccessful status") from None
    with response:
        if (
            response.status != 200
            or response.headers.get_content_type() != "image/jpeg"
        ):
            raise StagingError("Public fetch did not return HTTP 200 image/jpeg")
        length = response.headers.get("Content-Length")
        if length is not None and length != str(len(data)):
            raise StagingError("Public fetch length differs from the staged JPEG")
        received = bytearray()
        while len(received) <= len(data):
            if time.monotonic() >= deadline:
                raise StagingError("Public fetch exceeded its read deadline")
            chunk = response.read1(min(65536, len(data) + 1 - len(received)))
            if not chunk:
                break
            received.extend(chunk)
        if received != data:
            raise StagingError("Public fetch bytes differ from the staged JPEG")


def _close(stack: ExitStack) -> None:
    failed = False
    try:
        stack.close()
    except Exception:
        failed = True
    if failed:
        raise StagingError(
            "Media staging cleanup failed; inspect local processes"
        ) from None


@contextmanager
def stage_media(
    repository: Repository,
    config: AppConfig,
    post_id: str,
    *,
    startup_timeout: float = 120,
    fetch_timeout: float = 15,
) -> Iterator[StagedMedia]:
    """Keep one validated JPEG public until context exit; never contact Instagram."""
    _timeout(startup_timeout)
    _timeout(fetch_timeout)
    failure = False
    data = b""
    public_url = ""
    try:
        data = _jpeg(repository, config, post_id)
    except Exception:
        failure = True
    if failure:
        logger.error(
            "media_staging_failed",
            extra={
                "event": "media_staging_failed",
                "operation": "validate_source",
                "error_type": "StagingError",
            },
        )
        raise StagingError(
            "Cannot stage Post/JPEG; check canonical regular asset and database"
        )
    context = {"post_id": post_id}
    logger.info(
        "media_staging_started", extra={"event": "media_staging_started", **context}
    )
    # Setup exceptions are sanitized without retaining a secret-bearing chain.
    stack = ExitStack()
    diagnostic = "Media staging failed; check tunnel availability and public fetch"
    try:
        deadline = time.monotonic() + startup_timeout
        port, route = stack.enter_context(_origin(data))
        base, process = stack.enter_context(
            _tunnel(port, max(0.001, deadline - time.monotonic()))
        )
        public_url = _provider_url(base) + route
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StagingError("Public HTTPS readiness timed out")
            verified = False
            try:
                _verify(public_url, data, min(fetch_timeout, remaining))
                verified = True
            except Exception:
                pass
            if verified:
                break
            if process.poll() is not None:
                raise StagingError("Tunnel stopped during public verification")
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        if process.poll() is not None:
            raise StagingError("Tunnel stopped during public verification")
    except Exception as error:
        failure = True
        if isinstance(error, StagingError):
            diagnostic = str(error)
    except BaseException:
        _close(stack)
        raise
    if failure:
        try:
            _close(stack)
        finally:
            logger.error(
                "media_staging_failed",
                extra={
                    "event": "media_staging_failed",
                    "error_type": "StagingError",
                    **context,
                },
            )
        raise StagingError(diagnostic)
    try:
        logger.info(
            "media_staging_ready", extra={"event": "media_staging_ready", **context}
        )
        yield StagedMedia(post_id, public_url)
    finally:
        _close(stack)
        logger.info(
            "media_staging_stopped", extra={"event": "media_staging_stopped", **context}
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Temporarily stage one validated JPEG")
    parser.add_argument("post_id")
    parser.add_argument("--config", type=Path, default=Path("aed.toml"))
    args = parser.parse_args()
    configure_logging()
    failed = False
    message = (
        "Cannot stage media; inspect local config/database and staging prerequisites"
    )
    try:
        config = load_config(args.config)
        uri = config.database_path.absolute().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            with stage_media(
                SQLiteRepository(connection), config, args.post_id
            ) as staged:
                print(staged.public_url, flush=True)
                while True:
                    time.sleep(1)
    except KeyboardInterrupt:
        return
    except Exception as error:
        failed = True
        if isinstance(error, StagingError):
            message = str(error)
    if failed:
        parser.exit(1, message + "\n")


if __name__ == "__main__":
    main()
