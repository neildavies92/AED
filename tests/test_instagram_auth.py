"""OAuth and lifecycle tests use synthetic credentials and no external network."""

import json
import logging
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.message import Message
from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from ask_every_day import instagram_auth as auth
from ask_every_day.instagram_client import InstagramIdentity
from ask_every_day.logging_config import JSONFormatter

NOW = datetime(2026, 10, 22, tzinfo=UTC)
CONFIG = auth.AuthConfig("100000000000001", "https://callback.example.invalid/oauth/")
IDENTITY = InstagramIdentity("100000000000002", "ask.every.day", "Business")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError))


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, body, content_type):
        self.calls.append((method, url, body, content_type))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(data, status=200):
    return status, json.dumps(data).encode()


def grant(**changes):
    return response(
        {
            "data": [
                {
                    "access_token": "SYNTHETIC_SHORT_TOKEN",
                    "user_id": "100000000000003",
                    "permissions": ",".join(sorted(auth.SCOPES)),
                    **changes,
                }
            ]
        }
    )


def long_token(**changes):
    return response(
        {
            "access_token": "SYNTHETIC_LONG_TOKEN",
            "token_type": "bearer",
            "expires_in": 60 * 86400,
            **changes,
        }
    )


def credential(**changes):
    return auth.InstagramCredential(
        **{
            "access_token": "SYNTHETIC_SAVED_TOKEN",
            "app_id": CONFIG.app_id,
            "authorization_user_id": "100000000000003",
            "user_id": IDENTITY.user_id,
            "username": "ask.every.day",
            "scopes": auth.SCOPES,
            "issued_at": NOW - timedelta(days=10),
            "expires_at": NOW + timedelta(days=50),
            **changes,
        }
    )


class Store:
    def __init__(self, value=None):
        self.value = value
        self.saved = []

    def load(self):
        return self.value

    def save(self, credential):
        self.saved.append(credential)
        self.value = credential

    def delete(self):
        self.value = None


def manager(transport, store=None, identify=None, clock=lambda: NOW):
    return auth.CredentialManager(
        auth.InstagramAuthClient(CONFIG, transport=transport),
        store if store is not None else Store(),
        identify=identify if identify is not None else lambda token: IDENTITY,
        clock=clock,
    )


def test_authorization_url_state_entropy_and_exact_scopes():
    attempts = [auth.AuthorizationAttempt(CONFIG) for _ in range(2)]
    urls = [a.authorization_url() for a in attempts]
    fields = [parse_qs(urlsplit(url).query) for url in urls]
    assert urlsplit(urls[0])._replace(query="").geturl() == (
        "https://www.instagram.com/oauth/authorize"
    )
    assert fields[0]["client_id"] == [CONFIG.app_id]
    assert fields[0]["redirect_uri"] == [CONFIG.redirect_uri]
    assert set(fields[0]["scope"][0].split(",")) == auth.SCOPES
    assert fields[0]["enable_fb_login"] == ["false"]
    assert fields[0]["response_type"] == ["code"]
    assert fields[0]["state"] != fields[1]["state"]
    assert len(fields[0]["state"][0]) >= 43
    assert fields[0]["state"][0] not in repr(attempts[0])


def test_state_success_is_single_use():
    attempt = auth.AuthorizationAttempt(CONFIG)
    state = parse_qs(urlsplit(attempt.authorization_url()).query)["state"][0]
    query = urlencode({"state": state, "code": "SYNTHETIC_CODE"})
    assert attempt.consume(query) == "SYNTHETIC_CODE"
    with pytest.raises(auth.AuthError):
        attempt.consume(query)
    with pytest.raises(auth.AuthError):
        attempt.authorization_url()
    assert attempt._state == ""


def test_concurrent_duplicate_responses_can_consume_state_only_once():
    attempt = auth.AuthorizationAttempt(CONFIG)
    state = parse_qs(urlsplit(attempt.authorization_url()).query)["state"][0]
    query = urlencode({"state": state, "code": "SYNTHETIC_CODE"})

    def consume(_):
        try:
            return attempt.consume(query)
        except auth.AuthError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(consume, range(20)))
    assert results.count("SYNTHETIC_CODE") == 1
    assert results.count(None) == 19


@pytest.mark.parametrize(
    "mode",
    [
        "missing_state",
        "wrong_state",
        "missing_code",
        "denied",
        "duplicate_state",
        "duplicate_code",
        "expired",
        "oversized",
        "unicode_state",
        "malformed",
    ],
)
def test_bad_callback_consumes_state_without_leaking(mode):
    now = [0.0]
    attempt = auth.AuthorizationAttempt(CONFIG, clock=lambda: now[0])
    state = parse_qs(urlsplit(attempt.authorization_url()).query)["state"][0]
    fields = {"state": state, "code": "SYNTHETIC_PRIVATE_CODE"}
    if mode == "missing_state":
        del fields["state"]
    if mode == "wrong_state":
        fields["state"] = "SYNTHETIC_WRONG_STATE"
    if mode == "missing_code":
        del fields["code"]
    if mode == "denied":
        fields = {
            "state": state,
            "error": "access_denied",
            "error_description": "PRIVATE",
        }
    if mode == "expired":
        now[0] = 300
    if mode == "unicode_state":
        fields["state"] = "private-é"
    query = urlencode(fields)
    if mode == "duplicate_state":
        query += "&state=PRIVATE"
    if mode == "duplicate_code":
        query += "&code=PRIVATE"
    if mode == "oversized":
        query += "x" * 8192
    if mode == "malformed":
        query += "&broken"
    with pytest.raises(auth.AuthError) as caught:
        attempt.consume(query)
    assert attempt._state == ""
    assert caught.value.__context__ is None
    assert "PRIVATE" not in str(caught.value)
    with pytest.raises(auth.AuthError):
        attempt.consume(urlencode({"state": state, "code": "SYNTHETIC_CODE"}))


def test_cancel_and_authorization_expiry():
    now = [0.0]
    attempt = auth.AuthorizationAttempt(CONFIG, clock=lambda: now[0])
    now[0] = 300
    with pytest.raises(auth.AuthError):
        attempt.authorization_url()
    assert attempt._state == ""
    attempt = auth.AuthorizationAttempt(CONFIG)
    attempt.cancel()
    with pytest.raises(auth.AuthError):
        attempt.authorization_url()


@pytest.mark.parametrize(
    "uri",
    [
        "http://127.0.0.1:8765/oauth/instagram/callback",
        "https://example.invalid",
        "https://user:password@example.invalid/callback",
        "https://example.invalid/c?state=x",
        "https://example.invalid/c#fragment",
        "https://example.invalid:bad/c",
        "PRIVATE\n",
    ],
)
def test_unverified_or_unsafe_redirects_fail_closed(uri):
    with pytest.raises(auth.AuthError) as caught:
        auth.AuthConfig(CONFIG.app_id, uri)
    assert uri not in str(caught.value)


def test_connect_uses_verified_flow_and_preserves_distinct_account_ids():
    transport = Transport(grant(), long_token())
    store = Store()
    identify = Mock(return_value=IDENTITY)
    result = manager(transport, store, identify).connect(
        "SYNTHETIC_CODE", "SYNTHETIC_APP_SECRET"
    )
    assert result.expires_at == NOW + timedelta(days=60)
    assert result.authorization_user_id != result.user_id
    assert store.saved == [result]
    identify.assert_called_once_with("SYNTHETIC_LONG_TOKEN")
    method, url, body, content_type = transport.calls[0]
    assert method == "POST" and url == "https://api.instagram.com/oauth/access_token"
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="grant_type"\r\n\r\nauthorization_code' in body
    assert b'name="code"\r\n\r\nSYNTHETIC_CODE' in body
    assert CONFIG.redirect_uri.encode() in body
    method, url, body, content_type = transport.calls[1]
    assert method == "GET" and body is None and content_type is None
    assert urlsplit(url).path == "/access_token"
    assert parse_qs(urlsplit(url).query) == {
        "grant_type": ["ig_exchange_token"],
        "client_secret": ["SYNTHETIC_APP_SECRET"],
        "access_token": ["SYNTHETIC_SHORT_TOKEN"],
    }
    for secret in ("SYNTHETIC_CODE", "SYNTHETIC_APP_SECRET", "SYNTHETIC_LONG_TOKEN"):
        assert secret not in repr(result)


@pytest.mark.parametrize("refresh", [False, True])
def test_token_receipt_time_does_not_overstate_refresh_age_or_expiry(refresh):
    now = [NOW]
    responses = [long_token()] if refresh else [grant(), long_token()]
    fake = Transport(*responses)

    def transport(method, url, body, content_type):
        result = fake(method, url, body, content_type)
        if method == "GET":
            now[0] += timedelta(seconds=10)
        return result

    store = Store(
        credential(
            issued_at=NOW - timedelta(days=55), expires_at=NOW + timedelta(days=5)
        )
    )
    instance = manager(transport, store, clock=lambda: now[0])
    result = (
        instance.get_valid_credential()
        if refresh
        else instance.connect("SYNTHETIC_CODE", "SYNTHETIC_APP_SECRET")
    )
    assert result.issued_at == NOW + timedelta(seconds=10)
    assert result.expires_at == NOW + timedelta(days=60)


@pytest.mark.parametrize(
    "payload",
    [
        b"not JSON PRIVATE",
        b"[]",
        b"{",
        b"x" * (auth._MAX_RESPONSE + 1),
        b'{"error":{"message":"PRIVATE"}}',
        b'{"data":[],"data":[],"PRIVATE":"PRIVATE"}',
    ],
)
def test_exchange_response_errors_are_sanitized_and_not_retried(payload, caplog):
    transport = Transport((200, payload))
    with caplog.at_level(logging.INFO):
        with pytest.raises(auth.AuthError) as caught:
            auth.InstagramAuthClient(CONFIG, transport=transport).exchange_code(
                "SYNTHETIC_CODE", "SYNTHETIC_APP_SECRET"
            )
    assert caught.value.outcome_uncertain is True
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert len(transport.calls) == 1
    output = (
        str(caught.value)
        + caplog.text
        + "".join(JSONFormatter().format(r) for r in caplog.records)
    )
    for text in (
        "PRIVATE",
        "SYNTHETIC_CODE",
        "SYNTHETIC_APP_SECRET",
        CONFIG.redirect_uri,
    ):
        assert text not in output


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 302, True, 999])
def test_http_failures_remain_bounded_and_uncertain(status):
    transport = Transport((status, b"PRIVATE"))
    with pytest.raises(auth.AuthError) as caught:
        auth.InstagramAuthClient(CONFIG, transport=transport).exchange_code(
            "SYNTHETIC_CODE", "SYNTHETIC_APP_SECRET"
        )
    assert len(transport.calls) == 1 and caught.value.outcome_uncertain
    assert caught.value.http_status == (
        status if type(status) is int and status < 600 else None
    )


def test_transport_error_discards_exception_chain_and_secrets():
    transport = Transport(TimeoutError("PRIVATE_TOKEN PRIVATE_CODE"))
    with pytest.raises(auth.AuthError) as caught:
        auth.InstagramAuthClient(CONFIG, transport=transport).exchange_code(
            "SYNTHETIC_CODE", "SYNTHETIC_APP_SECRET"
        )
    assert caught.value.__context__ is None
    assert "PRIVATE" not in str(caught.value)
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"data": []},
        {"data": [{}, {}]},
        {"data": [None]},
        {
            "data": [
                {
                    "access_token": "PRIVATE",
                    "user_id": "123",
                    "permissions": "instagram_business_basic",
                }
            ]
        },
    ],
)
def test_code_schema_and_missing_permissions_fail_closed(data):
    transport = Transport(response(data))
    with pytest.raises(auth.AuthError) as caught:
        auth.InstagramAuthClient(CONFIG, transport=transport).exchange_code(
            "SYNTHETIC_CODE", "SYNTHETIC_APP_SECRET"
        )
    assert caught.value.outcome_uncertain
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_in": True},
        {"expires_in": -1},
        {"expires_in": 60 * 86400 + 1},
        {"expires_in": "60"},
        {"token_type": "other"},
        {"access_token": ""},
    ],
)
def test_long_lived_schema_is_strict(changes):
    transport = Transport(long_token(**changes))
    with pytest.raises(auth.AuthError):
        auth.InstagramAuthClient(CONFIG, transport=transport).refresh_token(
            "SYNTHETIC_SAVED_TOKEN"
        )


@pytest.mark.parametrize(
    "identity",
    [
        InstagramIdentity(IDENTITY.user_id, "wrong.account", "Business"),
        InstagramIdentity(IDENTITY.user_id, "ask.every.day", "PERSONAL"),
        InstagramIdentity("PRIVATE", "ask.every.day", "Business"),
    ],
)
def test_wrong_identity_never_persists(identity):
    store = Store()
    with pytest.raises(auth.AuthError):
        manager(
            Transport(grant(), long_token()), store, lambda token: identity
        ).connect("SYNTHETIC_CODE", "SYNTHETIC_APP_SECRET")
    assert not store.saved


def test_valid_credential_is_revalidated_without_refresh():
    store = Store(credential())
    identify = Mock(return_value=IDENTITY)
    transport = Transport()
    instance = manager(transport, store, identify)
    assert instance.get_valid_credential() == store.value
    identify.assert_called_once_with("SYNTHETIC_SAVED_TOKEN")
    assert not transport.calls and not store.saved
    instance.disconnect()
    assert store.value is None


def test_manager_supplies_the_existing_client_without_publishing():
    transport = Transport()
    instance = manager(transport, Store(credential()))
    client = instance.client()
    assert isinstance(client, auth.InstagramClient)
    assert client._token == "SYNTHETIC_SAVED_TOKEN"
    assert not transport.calls


def test_refresh_is_eligible_once_then_persisted_and_avoided(caplog):
    old = credential(
        issued_at=NOW - timedelta(days=55), expires_at=NOW + timedelta(days=5)
    )
    store = Store(old)
    transport = Transport(long_token(access_token="SYNTHETIC_REFRESHED_TOKEN"))
    instance = manager(transport, store)
    with caplog.at_level(logging.INFO):
        result = instance.get_valid_credential()
        assert instance.get_valid_credential() == result
    assert result.issued_at == NOW and result.expires_at == NOW + timedelta(days=60)
    assert result.user_id == old.user_id and result.scopes == old.scopes
    assert len(store.saved) == 1 and len(transport.calls) == 1
    assert parse_qs(urlsplit(transport.calls[0][1]).query) == {
        "grant_type": ["ig_refresh_token"],
        "access_token": ["SYNTHETIC_SAVED_TOKEN"],
    }
    assert transport.calls[0][0] == "GET"
    assert "SYNTHETIC_REFRESHED_TOKEN" not in caplog.text


@pytest.mark.parametrize(
    "value, operation",
    [
        (None, "missing_credential"),
        (
            credential(issued_at=NOW - timedelta(days=60), expires_at=NOW),
            "expired_credential",
        ),
        (
            credential(
                issued_at=NOW + timedelta(seconds=1), expires_at=NOW + timedelta(days=1)
            ),
            "expired_credential",
        ),
        (credential(app_id="100000000000009"), "app_binding"),
        (
            credential(
                issued_at=NOW - timedelta(hours=1), expires_at=NOW + timedelta(hours=1)
            ),
            "refresh_ineligible",
        ),
    ],
)
def test_missing_expired_wrong_app_and_ineligible_tokens_fail_closed(value, operation):
    transport = Transport()
    with pytest.raises(auth.AuthError) as caught:
        manager(transport, Store(value)).get_valid_credential()
    assert caught.value.operation == operation
    assert not transport.calls


@pytest.mark.parametrize("failure", ["refresh", "identity", "account_id", "save"])
def test_refresh_failure_never_returns_or_saves_unverified_token(failure):
    old = credential(
        issued_at=NOW - timedelta(days=55), expires_at=NOW + timedelta(days=5)
    )
    store = Store(old)
    transport = Transport(
        TimeoutError("PRIVATE") if failure == "refresh" else long_token()
    )
    identify = Mock(return_value=IDENTITY)
    if failure == "identity":
        identify.side_effect = RuntimeError("PRIVATE")
    if failure == "account_id":
        identify.return_value = replace(IDENTITY, user_id="100000000000009")
    if failure == "save":
        store.save = Mock(side_effect=OSError("PRIVATE"))
    with pytest.raises(auth.AuthError) as caught:
        manager(transport, store, identify).get_valid_credential()
    assert caught.value.__context__ is None and "PRIVATE" not in str(caught.value)
    assert store.value == old and not store.saved


def test_token_expiring_during_identity_request_is_not_returned():
    value = credential(expires_at=NOW + timedelta(days=10))
    now = [NOW]

    def identify(token):
        now[0] = value.expires_at
        return IDENTITY

    with pytest.raises(auth.AuthError):
        manager(
            Transport(), Store(value), identify, lambda: now[0]
        ).get_valid_credential()


def test_native_transport_disables_proxies_redirects_and_bounds_reads(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock()
    response.code = 200
    response.read.return_value = b"{}"
    opener = Mock(open=Mock(return_value=response))
    build = Mock(return_value=opener)
    monkeypatch.setattr(auth, "build_opener", build)
    assert auth.auth_transport(
        "GET", "https://graph.instagram.com/example", None, None
    ) == (200, b"{}")
    assert build.call_args.args[0].proxies == {}
    redirect = build.call_args.args[1]
    assert redirect.redirect_request(None, None, None, None, None, None) is None
    response.read.assert_called_once_with(auth._MAX_RESPONSE + 1)
    assert opener.open.call_args.kwargs == {"timeout": 15}
    response.__exit__.assert_called_once()


def test_native_http_error_is_closed(monkeypatch):
    stream = BytesIO(b"PRIVATE")
    error = HTTPError(
        "https://example.invalid/?access_token=PRIVATE",
        400,
        "PRIVATE",
        Message(),
        stream,
    )
    monkeypatch.setattr(
        auth, "build_opener", lambda *args: Mock(open=Mock(side_effect=error))
    )
    status, body = auth.auth_transport("GET", error.url, None, None)
    assert status == 400 and body == b"PRIVATE" and stream.closed
