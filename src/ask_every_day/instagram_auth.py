"""Instagram OAuth primitives and credential lifecycle, separate from publishing."""

import json
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ask_every_day.instagram_client import (
    InstagramClient,
    InstagramError,
    InstagramIdentity,
)

SCOPES = frozenset({"instagram_business_basic", "instagram_business_content_publish"})
EXPECTED_USERNAME = "ask.every.day"
REDIRECT_URI = "https://localhost:8765/oauth/instagram/callback"
_MAX_RESPONSE = 65_536
_LOG = logging.getLogger(__name__)


class AuthError(Exception):
    """Fixed diagnostics only; provider payloads and exception chains are discarded."""

    def __init__(
        self, operation: str, *, http_status: int | None = None, uncertain: bool = False
    ):
        self.diagnostics: dict[str, object] = {}
        self.operation = operation
        self.http_status = http_status
        self.outcome_uncertain = uncertain
        super().__init__(f"Instagram authentication failed ({operation})")


def _secret(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 16_384
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise AuthError("invalid_credential")
    return value


def _id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or not 1 <= len(value) <= 128
    ):
        raise AuthError("invalid_metadata")
    return value


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AuthError("invalid_metadata")
    if value.utcoffset() is None:
        raise AuthError("invalid_metadata")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Ambiguous object")
    return result


@dataclass(frozen=True)
class AuthConfig:
    app_id: str
    redirect_uri: str = field(repr=False)
    tls_certificate: Path | None = field(default=None, repr=False)
    tls_private_key: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _id(self.app_id)
        if self.redirect_uri != REDIRECT_URI:
            raise AuthError("redirect_configuration")
        if (self.tls_certificate is None) != (self.tls_private_key is None) or any(
            value is not None and not isinstance(value, Path)
            for value in (self.tls_certificate, self.tls_private_key)
        ):
            raise AuthError("tls_configuration")


@dataclass(frozen=True)
class InstagramCredential:
    access_token: str = field(repr=False)
    app_id: str
    authorization_user_id: str = field(repr=False)
    user_id: str = field(repr=False)
    username: str
    scopes: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    token_type: str = "bearer"

    def __post_init__(self) -> None:
        _secret(self.access_token)
        for value in (self.app_id, self.authorization_user_id, self.user_id):
            _id(value)
        _aware(self.issued_at)
        _aware(self.expires_at)
        if (
            self.username != EXPECTED_USERNAME
            or self.token_type != "bearer"
            or type(self.scopes) is not frozenset
            or not SCOPES <= self.scopes
            or len(self.scopes) > 16
            or any(
                not isinstance(s, str)
                or not s.startswith("instagram_business_")
                or not s.replace("_", "").isascii()
                or not s.replace("_", "").isalpha()
                or len(s) > 100
                for s in self.scopes
            )
            or not timedelta(0) < self.expires_at - self.issued_at <= timedelta(days=60)
        ):
            raise AuthError("invalid_metadata")


class CredentialStore(Protocol):
    def load(self) -> InstagramCredential | None: ...

    def save(self, credential: InstagramCredential) -> None: ...

    def delete(self) -> None: ...


@dataclass(repr=False)
class AuthorizationAttempt:
    """One bounded, single-use state; a receiver must pass its exact query here."""

    config: AuthConfig
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _state: str = field(
        default_factory=lambda: secrets.token_urlsafe(32), init=False, repr=False
    )
    _started: float = field(init=False, repr=False)
    _used: bool = field(default=False, init=False)
    _lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._started = self.clock()

    def authorization_url(self) -> str:
        with self._lock:
            if self._used or not 0 <= self.clock() - self._started < 300:
                self.cancel()
                raise AuthError("authorization_expired")
            return "https://www.instagram.com/oauth/authorize?" + urlencode(
                {
                    "client_id": self.config.app_id,
                    "redirect_uri": self.config.redirect_uri,
                    "response_type": "code",
                    "scope": ",".join(sorted(SCOPES)),
                    "state": self._state,
                    "enable_fb_login": "false",
                }
            )

    def cancel(self) -> None:
        with self._lock:
            self._used = True
            self._state = ""

    def consume(self, query: str) -> str:
        with self._lock:
            state, elapsed, used = self._state, self.clock() - self._started, self._used
            self.cancel()
        valid = False
        code = ""
        try:
            if used or not 0 <= elapsed < 300 or len(query) > 8192:
                raise ValueError
            fields = parse_qs(
                query, keep_blank_values=True, strict_parsing=True, max_num_fields=8
            )
            if any(len(values) != 1 for values in fields.values()):
                raise ValueError
            received_state = fields["state"][0]
            if not secrets.compare_digest(state, received_state):
                raise ValueError
            if set(fields) != {"state", "code"}:
                raise ValueError
            code = _secret(fields["code"][0])
            valid = True
        except (ValueError, KeyError, TypeError, AuthError):
            pass
        if not valid:
            raise AuthError("authorization_response")
        return code


class AuthTransport(Protocol):
    def __call__(
        self, method: str, url: str, body: bytes | None, content_type: str | None
    ) -> tuple[int, bytes]: ...


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def auth_transport(
    method: str, url: str, body: bytes | None, content_type: str | None
) -> tuple[int, bytes]:
    headers = {"Accept": "application/json"}
    if content_type is not None:
        headers["Content-Type"] = content_type
    request = Request(url, data=body, headers=headers, method=method)
    opener = build_opener(ProxyHandler({}), _NoRedirects())
    try:
        response = opener.open(request, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        return response.code, response.read(_MAX_RESPONSE + 1)


@dataclass(frozen=True, repr=False)
class _AuthorizationGrant:
    access_token: str
    authorization_user_id: str
    scopes: frozenset[str]


@dataclass(frozen=True, repr=False)
class _LongLivedToken:
    access_token: str
    expires_in: int


class InstagramAuthClient:
    def __init__(
        self, config: AuthConfig, *, transport: AuthTransport = auth_transport
    ):
        self.config = config
        self._transport = transport

    def _request(
        self,
        operation: str,
        method: str,
        url: str,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> dict:
        failure = False
        status = None
        result = {}
        try:
            status, payload = self._transport(method, url, body, content_type)
            if type(status) is not int or not 100 <= status <= 599:
                status = None
                raise ValueError
            if not 200 <= status < 300:
                raise ValueError
            if not isinstance(payload, bytes) or len(payload) > _MAX_RESPONSE:
                raise ValueError
            result = json.loads(payload, object_pairs_hook=_unique_object)
            if not isinstance(result, dict) or "error" in result:
                raise ValueError
        except Exception:
            failure = True
        if failure:
            _LOG.warning(
                "instagram_auth_failed",
                extra={
                    "event": "instagram_auth_failed",
                    "operation": operation,
                    "http_status": status,
                    "error_type": "AuthError",
                },
            )
            raise AuthError(operation, http_status=status, uncertain=method == "POST")
        return result

    def exchange_code(self, code: str, app_secret: str) -> _AuthorizationGrant:
        fields = {
            "client_id": self.config.app_id,
            "client_secret": _secret(app_secret),
            "grant_type": "authorization_code",
            "redirect_uri": self.config.redirect_uri,
            "code": _secret(code),
        }
        boundary = secrets.token_hex(24)
        body = (
            b"".join(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                    f"\r\n\r\n{value}\r\n"
                ).encode()
                for name, value in fields.items()
            )
            + f"--{boundary}--\r\n".encode()
        )
        data = self._request(
            "exchange_code",
            "POST",
            "https://api.instagram.com/oauth/access_token",
            body,
            f"multipart/form-data; boundary={boundary}",
        )
        result = None
        try:
            row = data
            if "data" in data:
                rows = data["data"]
                if (
                    not isinstance(rows, list)
                    or len(rows) != 1
                    or any(
                        k in data for k in ("access_token", "user_id", "permissions")
                    )
                ):
                    raise ValueError
                row = rows[0]
            if not isinstance(row, dict) or "error" in row:
                raise ValueError
            permissions = row["permissions"]
            if isinstance(permissions, str):
                permissions = permissions.split(",")
            if (
                not isinstance(permissions, list)
                or not 1 <= len(permissions) <= 16
                or any(
                    not isinstance(s, str)
                    or not s.startswith("instagram_business_")
                    or not s.replace("_", "").isascii()
                    or not s.replace("_", "").isalpha()
                    or len(s) > 100
                    for s in permissions
                )
            ):
                raise ValueError
            scopes = frozenset(permissions)
            if len(scopes) != len(permissions) or not SCOPES <= scopes:
                raise ValueError
            user_id = row["user_id"]
            if type(user_id) is int and user_id > 0:
                user_id = str(user_id)
            result = _AuthorizationGrant(
                _secret(row["access_token"]), _id(user_id), scopes
            )
        except (ValueError, KeyError, TypeError, AuthError):
            pass
        if result is None:
            raise AuthError("exchange_schema", uncertain=True)
        return result

    def _long_lived(self, operation: str, path: str, fields: dict) -> _LongLivedToken:
        data = self._request(
            operation, "GET", f"https://graph.instagram.com/{path}?{urlencode(fields)}"
        )
        result = None
        try:
            seconds = data["expires_in"]
            if (
                data["token_type"] != "bearer"
                or type(seconds) is not int
                or not 0 < seconds <= 60 * 86_400
            ):
                raise ValueError
            result = _LongLivedToken(_secret(data["access_token"]), seconds)
        except (ValueError, KeyError, TypeError, AuthError):
            pass
        if result is None:
            raise AuthError("token_schema")
        return result

    def extend_token(self, token: str, app_secret: str) -> _LongLivedToken:
        return self._long_lived(
            "extend_token",
            "access_token",
            {
                "grant_type": "ig_exchange_token",
                "client_secret": _secret(app_secret),
                "access_token": _secret(token),
            },
        )

    def refresh_token(self, token: str) -> _LongLivedToken:
        return self._long_lived(
            "refresh_token",
            "refresh_access_token",
            {"grant_type": "ig_refresh_token", "access_token": _secret(token)},
        )


def _identify(token: str) -> InstagramIdentity:
    return InstagramClient(token).identify_account()


class CredentialManager:
    def __init__(
        self,
        provider: InstagramAuthClient,
        store: CredentialStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        identify: Callable[[str], InstagramIdentity] = _identify,
    ):
        self.provider = provider
        self.store = store
        self._clock = clock
        self._identify = identify

    def _save(self, credential: InstagramCredential) -> None:
        failed = False
        try:
            self.store.save(credential)
        except Exception:
            failed = True
        if failed:
            raise AuthError("secure_store")

    def _identity(self, token: str, user_id: str | None = None) -> InstagramIdentity:
        identity = None
        failure = AuthError("account_binding")
        try:
            candidate = self._identify(token)
            if (
                candidate.username != EXPECTED_USERNAME
                or candidate.account_type.casefold()
                not in {"business", "media_creator"}
                or (user_id is not None and candidate.user_id != user_id)
            ):
                raise ValueError
            _id(candidate.user_id)
            identity = candidate
        except InstagramError as error:
            failure.diagnostics = {
                **error.diagnostics,
                "http_status": error.http_status,
                "api_operation": error.operation,
            }
        except Exception:
            pass
        if identity is None:
            raise failure
        return identity

    def connect(self, code: str, app_secret: str) -> InstagramCredential:
        grant = self.provider.exchange_code(code, app_secret)
        requested_at = self._clock()
        token = self.provider.extend_token(grant.access_token, app_secret)
        issued_at = self._clock()
        identity = self._identity(token.access_token)
        credential = InstagramCredential(
            token.access_token,
            self.provider.config.app_id,
            grant.authorization_user_id,
            identity.user_id,
            identity.username,
            grant.scopes,
            issued_at,
            requested_at + timedelta(seconds=token.expires_in),
        )
        if self._clock() >= credential.expires_at:
            raise AuthError("expired_credential")
        self._save(credential)
        _LOG.info(
            "instagram_auth_completed", extra={"event": "instagram_auth_completed"}
        )
        return credential

    def local_credential(self) -> InstagramCredential:
        failed = False
        credential = None
        try:
            credential = self.store.load()
            if credential is not None:
                credential.__post_init__()
        except Exception:
            failed = True
        if failed:
            raise AuthError("credential_metadata")
        if credential is None:
            raise AuthError("missing_credential")
        now = self._clock()
        _aware(now)
        if credential.app_id != self.provider.config.app_id:
            raise AuthError("app_binding")
        if not credential.issued_at <= now < credential.expires_at:
            raise AuthError("expired_credential")
        return credential

    def verify_identity_read_only(self) -> InstagramCredential:
        """Explicit remote GET using local metadata; never refresh or persist tokens."""
        credential = self.local_credential()
        self._identity(credential.access_token, credential.user_id)
        return credential

    def get_valid_credential(self) -> InstagramCredential:
        credential = self.local_credential()
        now = self._clock()
        if credential.expires_at - now <= timedelta(days=7):
            if now - credential.issued_at < timedelta(hours=24):
                raise AuthError("refresh_ineligible")
            refreshed = self.provider.refresh_token(credential.access_token)
            issued_at = self._clock()
            self._identity(refreshed.access_token, credential.user_id)
            credential = replace(
                credential,
                access_token=refreshed.access_token,
                issued_at=issued_at,
                expires_at=now + timedelta(seconds=refreshed.expires_in),
            )
            if self._clock() >= credential.expires_at:
                raise AuthError("expired_credential")
            self._save(credential)
            _LOG.info(
                "instagram_token_refreshed",
                extra={"event": "instagram_token_refreshed"},
            )
        else:
            self._identity(credential.access_token, credential.user_id)
        if self._clock() >= credential.expires_at:
            raise AuthError("expired_credential")
        return credential

    def client(self) -> InstagramClient:
        return InstagramClient(self.get_valid_credential().access_token)

    def disconnect(self) -> None:
        failed = False
        try:
            self.store.delete()
        except Exception:
            failed = True
        if failed:
            raise AuthError("secure_store")
        _LOG.info(
            "instagram_credential_removed",
            extra={"event": "instagram_credential_removed"},
        )
