"""Private R2 objects for approved publications. Quick Tunnels are diagnostic only."""

import hashlib
import json
import logging
import multiprocessing
import re
import secrets
import socket
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from io import BytesIO
from multiprocessing.connection import Connection
from pathlib import Path

import botocore.session
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
    SSLError,
)
from PIL import Image

from ask_every_day.domain import AppConfig, PublicationStatus
from ask_every_day.file_credential_store import FileSecretBackend
from ask_every_day.media_staging import StagedMedia, StagingError, _jpeg, wait_for_media
from ask_every_day.recovery_domain import CheckpointPhase
from ask_every_day.repository import Repository

_LOG = logging.getLogger(__name__)
_DIRECTORY = Path("/var/lib/aed-credentials")
_SERVICE = "ask-every-day.r2-media"
_URL_SECONDS = 25 * 3600
_READY_SECONDS = 120


class OriginError(StagingError):
    def __init__(self, diagnostics: dict[str, object]):
        super().__init__(
            "Private media origin unavailable; inspect staging diagnostics"
        )
        self.diagnostics = diagnostics


def settings(raw: bytes) -> dict[str, str]:
    value = json.loads(raw)
    patterns = {
        "account_id": r"[0-9a-f]{32}",
        "bucket": r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]",
        "access_key_id": r"[A-Za-z0-9]{16,128}",
        "secret_access_key": r"[A-Za-z0-9/+=_-]{32,128}",
    }
    if (
        not isinstance(value, dict)
        or value.keys() != patterns.keys()
        or any(
            not isinstance(value[k], str) or not re.fullmatch(pattern, value[k])
            for k, pattern in patterns.items()
        )
    ):
        raise ValueError("invalid_media_origin_configuration")
    return value


def configure(directory: Path, raw: bytes) -> None:
    settings(raw)
    FileSecretBackend(directory).write(_SERVICE, "credential", raw)


def _configuration(directory: Path) -> dict[str, str]:
    try:
        raw = FileSecretBackend(directory).read(_SERVICE, "credential")
        return settings(raw or b"{}")
    except Exception:
        pass
    raise OriginError(
        {
            "operation": "origin_configuration",
            "failure_category": "configuration",
            "attempt_count": 0,
            "origin_ready": False,
        }
    )


def _client(cfg: dict[str, str]):
    return botocore.session.get_session().create_client(
        "s3",
        region_name="auto",
        endpoint_url=f"https://{cfg['account_id']}.r2.cloudflarestorage.com",
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["secret_access_key"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=10,
            proxies={},
            retries={"total_max_attempts": 1},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def _object_operation(
    client, bucket: str, key: str, data: bytes | None, metrics: dict[str, object]
) -> str:
    params = {"Bucket": bucket, "Key": key}
    if data is None:
        client.delete_object(**params)
        return ""
    started = time.monotonic()
    metrics["operation"] = "object_upload"
    try:
        client.put_object(
            **params,
            Body=data,
            ContentType="image/jpeg",
            CacheControl="private, no-store",
            IfNoneMatch="*",
        )
    except ClientError as error:
        if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
            raise
        # A restart may find the immutable key. Both GETs still compare every byte.
    url = client.generate_presigned_url(
        "get_object", Params=params, ExpiresIn=_URL_SECONDS
    )
    wait_for_media(url, data, started, started + _READY_SECONDS, 10, metrics)
    return url


def _worker(
    send: Connection, cfg: dict[str, str], key: str, data: bytes | None
) -> None:
    # Isolate SDK diagnostics and the OS resolver from the publisher. Only typed
    # fields and a successful GET capability cross the private anonymous pipe.
    logging.disable(logging.CRITICAL)
    metrics: dict[str, object] = {
        "operation": "origin_configuration",
        "attempt_count": 0,
        "origin_ready": False,
    }
    url = None
    started = time.monotonic()
    try:
        with closing(_client(cfg)) as client:
            metrics["operation"] = "object_cleanup" if data is None else "object_upload"
            url = _object_operation(client, cfg["bucket"], key, data, metrics)
    except ClientError as error:
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        metrics.update(
            failure_category="object_request",
            http_status=status if type(status) is int else None,
        )
    except (ConnectTimeoutError, ReadTimeoutError):
        metrics["failure_category"] = "timeout"
    except SSLError:
        metrics["failure_category"] = "tls"
    except EndpointConnectionError as error:
        cause: BaseException | None = error
        metrics["failure_category"] = "connect"
        for _ in range(6):
            if isinstance(cause, socket.gaierror):
                metrics["failure_category"] = "dns"
                break
            if cause is None:
                break
            cause = cause.__cause__ or cause.__context__
    except Exception:
        metrics.setdefault(
            "failure_category",
            "configuration"
            if metrics["operation"] == "origin_configuration"
            else "origin_unavailable",
        )
    finally:
        metrics["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        try:
            send.send((url, metrics))
        finally:
            send.close()


def _request(
    cfg: dict[str, str], key: str, data: bytes | None
) -> tuple[str, dict[str, object]]:
    ctx = multiprocessing.get_context("spawn")
    read, write = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker, args=(write, cfg, key, data))
    started = time.monotonic()
    try:
        process.start()
        write.close()
        if read.poll(20 if data is None else _READY_SECONDS + 5):
            try:
                url, metrics = read.recv()
            except EOFError:
                pass
            else:
                if url is None:
                    raise OriginError(metrics)
                return url, metrics
        raise OriginError(
            {
                "operation": "object_cleanup" if data is None else "media_staging",
                "failure_category": "worker_unavailable"
                if not process.is_alive()
                else "timeout",
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        )
    finally:
        read.close()
        write.close()
        if process.pid is not None:
            process.join(0.2)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()


@contextmanager
def stage_approved(
    repository: Repository,
    config: AppConfig,
    post_id: str,
    *,
    expected_sha256: str | None = None,
    directory: Path = _DIRECTORY,
) -> Iterator[StagedMedia]:
    try:
        data = _jpeg(repository, config, post_id)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError
    except Exception:
        raise OriginError(
            {"operation": "validate_source", "failure_category": "invalid_media"}
        ) from None
    key = f"approved/{post_id}/{expected_sha256}.jpg"
    try:
        cfg = _configuration(directory)
        url, metrics = _request(cfg, key, data)
    except OriginError as error:
        _LOG.error(
            "media_staging_failed",
            extra={
                "event": "media_staging_failed",
                "post_id": post_id,
                **error.diagnostics,
            },
        )
        raise
    _LOG.info(
        "media_staging_ready",
        extra={"event": "media_staging_ready", "post_id": post_id, **metrics},
    )
    yield StagedMedia(post_id, url, metrics)
    # On interruption, ambiguous POST, or incomplete checkpoint keep the object.
    # Cleanup is best effort after durable success and cannot change that result.
    try:
        history = repository.publication_history(post_id)
        if (
            len(history) == 1
            and history[0].publication.status is PublicationStatus.PUBLISHED
        ):
            checkpoint = repository.get_checkpoint(history[0].id)
            if (
                checkpoint
                and checkpoint.phase is CheckpointPhase.COMPLETED
                and checkpoint.asset_sha256 == expected_sha256
            ):
                _request(cfg, key, None)
    except Exception:
        _LOG.warning(
            "media_cleanup_deferred",
            extra={"event": "media_cleanup_deferred", "post_id": post_id},
        )


def probe(directory: Path) -> None:
    """Exercise the real storage path with a synthetic image and no publication state."""
    cfg = _configuration(directory)

    output = BytesIO()
    with Image.new("RGB", (1080, 1350), "#E2EFF9") as image:
        image.save(output, format="JPEG")
    key = f"probe/{secrets.token_hex(16)}.jpg"
    try:
        _request(cfg, key, output.getvalue())
    except OriginError:
        try:
            _request(cfg, key, None)
        except OriginError:
            _LOG.warning(
                "probe_cleanup_deferred", extra={"event": "probe_cleanup_deferred"}
            )
        raise
    _request(cfg, key, None)
