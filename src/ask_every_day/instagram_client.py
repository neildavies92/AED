"""Small Instagram Login client; no orchestration or automatic retries."""

import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, cast
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ask_every_day.production_runtime import require_docker_publisher

BASE_URL = "https://graph.instagram.com/v26.0"
_MAX_RESPONSE_BYTES = 1_048_576
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstagramIdentity:
    user_id: str
    username: str
    account_type: str


@dataclass(frozen=True)
class PublishingQuota:
    usage: int
    total: int
    duration: int


@dataclass(frozen=True)
class Container:
    id: str


@dataclass(frozen=True)
class PublishedMedia:
    id: str


@dataclass(frozen=True)
class MediaDetails:
    id: str
    owner_id: str
    media_type: str
    published_at: datetime

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.owner_id)
        if self.media_type not in {"IMAGE", "VIDEO", "CAROUSEL_ALBUM"}:
            raise ValueError("Unexpected media type")
        if (
            not isinstance(self.published_at, datetime)
            or self.published_at.tzinfo is None
            or self.published_at.utcoffset() is None
        ):
            raise ValueError("Expected an aware media timestamp")


class ContainerStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    FINISHED = "FINISHED"
    PUBLISHED = "PUBLISHED"
    ERROR = "ERROR"
    EXPIRED = "EXPIRED"


class InstagramError(Exception):
    """Safe diagnostics only; uncertain POST outcomes require reconciliation."""

    def __init__(
        self, operation: str, http_status: int | None, outcome_uncertain: bool
    ) -> None:
        self.operation = operation
        self.http_status = http_status
        self.outcome_uncertain = outcome_uncertain
        super().__init__(f"{operation}: {type(self).__name__} (HTTP {http_status})")


class TransportError(InstagramError):
    """No trustworthy response received."""


class APIError(InstagramError):
    """HTTP failure or provider error envelope."""


class ResponseError(InstagramError):
    """Malformed JSON or unexpected provider schema/state."""


class Transport(Protocol):
    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, bytes]: ...


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes]:
    """One request, bounded reads, no redirects or retries; socket timeout applies."""
    if method.upper() == "POST":
        require_docker_publisher()
    request = Request(url, data=body, headers=headers, method=method)
    opener = build_opener(_NoRedirects())
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        return response.code, response.read(_MAX_RESPONSE_BYTES + 1)


def _id(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("An ID must be a nonempty ASCII digit string")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Expected nonempty text")
    return value


def _integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative integer")
    return value


def _row(data: dict) -> dict:
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("Expected one result")
    return rows[0]


def _identity(data: dict) -> InstagramIdentity:
    row = _row(data) if "data" in data else data
    return InstagramIdentity(
        _id(row["user_id"]), _text(row["username"]), _text(row["account_type"])
    )


def _quota(data: dict) -> PublishingQuota:
    row = _row(data)
    config = row["config"]
    if not isinstance(config, dict):
        raise ValueError("Expected quota configuration")
    return PublishingQuota(
        _integer(row["quota_usage"]),
        _integer(config["quota_total"]),
        _integer(config["quota_duration"]),
    )


def _media_details(data: dict) -> MediaDetails:
    owner = data["owner"]
    if not isinstance(owner, dict):
        raise ValueError("Expected media owner object")
    return MediaDetails(
        _id(data["id"]),
        _id(owner["id"]),
        _text(data["media_type"]),
        datetime.fromisoformat(_text(data["timestamp"])),
    )


def _image_url(value: object) -> str:
    valid = False
    if isinstance(value, str) and not any(c.isspace() or ord(c) < 32 for c in value):
        try:
            parts = urlsplit(value)
            valid = (
                parts.scheme == "https"
                and bool(parts.hostname)
                and parts.username is None
                and parts.password is None
                and not parts.fragment
                and parts.port != 0
            )
        except ValueError:
            pass
    if not valid:
        raise ValueError("Expected an HTTPS image URL without credentials or fragment")
    assert isinstance(value, str)
    return value


class InstagramClient:
    """Token stays in memory; default repr exposes no instance attributes."""

    def __init__(
        self,
        access_token: str,
        *,
        timeout: float = 15.0,
        transport: Transport = urllib_transport,
    ) -> None:
        if (
            not isinstance(access_token, str)
            or not access_token
            or any(ord(c) < 33 or ord(c) > 126 for c in access_token)
        ):
            raise ValueError("Expected a nonempty ASCII bearer token")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 60
        ):
            raise ValueError(
                "Timeout must be finite, greater than zero and at most 60s"
            )
        self._token = access_token
        self._timeout = float(timeout)
        self._transport = transport

    def identify_account(self) -> InstagramIdentity:
        return self._call(
            "identify_account",
            "GET",
            "me",
            {"fields": "user_id,username,account_type"},
            _identity,
        )

    def publishing_quota(self, account_id: str) -> PublishingQuota:
        return self._call(
            "publishing_quota",
            "GET",
            f"{_id(account_id)}/content_publishing_limit",
            {"fields": "quota_usage,config"},
            _quota,
        )

    def create_image_container(
        self,
        account_id: str,
        image_url: str,
        *,
        caption: str | None = None,
        alt_text: str | None = None,
    ) -> Container:
        fields = {"image_url": _image_url(image_url)}
        for name, value in (("caption", caption), ("alt_text", alt_text)):
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("Optional copy must be text")
                fields[name] = value
        return self._call(
            "create_image_container",
            "POST",
            f"{_id(account_id)}/media",
            fields,
            lambda data: Container(_id(data["id"])),
        )

    def container_status(self, container_id: str) -> ContainerStatus:
        return self._call(
            "container_status",
            "GET",
            _id(container_id),
            {"fields": "status_code,status"},
            lambda data: ContainerStatus(data["status_code"]),
        )

    def publish_container(self, account_id: str, container_id: str) -> PublishedMedia:
        return self._call(
            "publish_container",
            "POST",
            f"{_id(account_id)}/media_publish",
            {"creation_id": _id(container_id)},
            lambda data: PublishedMedia(_id(data["id"])),
        )

    def media_details(self, media_id: str) -> MediaDetails:
        """GET only; owner is required, never infer identity by caption or likeness."""
        return self._call(
            "media_details",
            "GET",
            _id(media_id),
            {"fields": "id,owner,media_type,timestamp"},
            _media_details,
        )

    def _call[T](
        self,
        operation: str,
        method: str,
        path: str,
        fields: dict[str, str],
        parse: Callable[[dict], T],
    ) -> T:
        if method.upper() == "POST":
            require_docker_publisher()
        encoded = b""
        encoding_failed = False
        try:
            encoded = urlencode(fields).encode("ascii")
        except UnicodeError:
            encoding_failed = True
        if encoding_failed:
            raise ValueError("Request fields must contain valid Unicode text")
        url = f"{BASE_URL}/{path}"
        body = encoded if method == "POST" else None
        if method == "GET":
            url += "?" + encoded.decode("ascii")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        failure: InstagramError | None = None
        status: int | None = None
        payload = b""
        result: object = None
        try:
            status, payload = self._transport(method, url, headers, body, self._timeout)
        except Exception:
            # Third-party/injected transport exceptions may contain secrets.
            failure = TransportError(operation, None, method == "POST")
        if failure is None:
            try:
                if type(status) is not int or not 100 <= status <= 599:
                    status = None
                    raise ValueError("Invalid HTTP status")
                if not 200 <= status < 300:
                    failure = APIError(operation, status, method == "POST")
                else:
                    if (
                        not isinstance(payload, bytes)
                        or len(payload) > _MAX_RESPONSE_BYTES
                    ):
                        raise ValueError("Invalid response size/type")
                    data = json.loads(payload)
                    if not isinstance(data, dict):
                        raise ValueError("Expected object")
                    if "error" in data:
                        failure = APIError(operation, status, method == "POST")
                    else:
                        result = parse(data)
            except (ValueError, KeyError, TypeError, RecursionError):
                failure = ResponseError(operation, status, method == "POST")
        context = {"operation": operation, "http_status": status}
        if failure is not None:
            _LOG.warning(
                "Instagram request failed",
                extra={
                    "event": "instagram_request_failed",
                    **context,
                    "error_type": type(failure).__name__,
                },
            )
            # Raise outside handlers: neither __context__ nor __cause__ retains payloads.
            raise failure
        _LOG.info(
            "Instagram request completed",
            extra={
                "event": "instagram_request_completed",
                **context,
            },
        )
        return cast(T, result)
