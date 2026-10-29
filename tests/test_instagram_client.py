"""All provider calls use fake transports; no live credentials or network."""

import json
import logging
from urllib.parse import parse_qs, urlsplit

import pytest

from ask_every_day.instagram_client import (
    BASE_URL,
    APIError,
    Container,
    ContainerStatus,
    InstagramClient,
    InstagramIdentity,
    PublishedMedia,
    PublishingQuota,
    ResponseError,
    TransportError,
)
from ask_every_day.logging_config import JSONFormatter


@pytest.fixture(autouse=True)
def synthetic_docker_boundary(monkeypatch):
    monkeypatch.setattr("ask_every_day.production_runtime.docker_runtime", lambda: True)


class Fake:
    def __init__(self, data=None, status=200, error=None, raw=None):
        self.data = data
        self.status = status
        self.error = error
        self.raw = raw
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        if self.error is not None:
            raise self.error
        return self.status, self.raw if self.raw is not None else json.dumps(
            self.data
        ).encode()


def client(fake):
    return InstagramClient("synthetic-test-value", transport=fake, timeout=7)


@pytest.mark.parametrize("wrapped", [False, True])
def test_identity(wrapped):
    row = {"user_id": "00123", "username": "test", "account_type": "BUSINESS"}
    fake = Fake({"data": [row]} if wrapped else row)
    assert client(fake).identify_account() == InstagramIdentity(
        "00123", "test", "BUSINESS"
    )
    method, url, headers, body, timeout = fake.calls[0]
    assert method == "GET" and body is None and timeout == 7
    assert url.startswith(BASE_URL + "/me?")
    assert parse_qs(urlsplit(url).query) == {
        "fields": ["user_id,username,account_type"]
    }
    assert headers["Authorization"].startswith("Bearer ")
    assert "synthetic-test-value" not in url


def test_quota_provider_values():
    fake = Fake(
        {
            "data": [
                {
                    "quota_usage": 3,
                    "config": {"quota_total": 73, "quota_duration": 86400},
                }
            ]
        }
    )
    assert client(fake).publishing_quota("0123") == PublishingQuota(3, 73, 86400)
    assert urlsplit(fake.calls[0][1]).path.endswith("/0123/content_publishing_limit")
    assert parse_qs(urlsplit(fake.calls[0][1]).query) == {
        "fields": ["quota_usage,config"]
    }


@pytest.mark.parametrize(
    "copy",
    [{}, {"caption": "", "alt_text": ""}, {"caption": "é & + 🪞", "alt_text": "a=b"}],
)
def test_create_encoding(copy):
    fake = Fake({"id": "0009"})
    url = "https://example.test/image.jpg?signature=a+b&x=1"
    assert client(fake).create_image_container("123", url, **copy) == Container("0009")
    method, request_url, headers, body, timeout = fake.calls[0]
    assert (method, request_url, timeout) == ("POST", BASE_URL + "/123/media", 7)
    assert parse_qs(body.decode(), keep_blank_values=True) == {
        k: [v] for k, v in {"image_url": url, **copy}.items()
    }
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"


def test_publish_existing_distinct_id():
    fake = Fake({"id": "456"})
    assert client(fake).publish_container("123", "0009") == PublishedMedia("456")
    assert fake.calls[0][0:2] == ("POST", BASE_URL + "/123/media_publish")
    assert fake.calls[0][3] == b"creation_id=0009"
    assert len(fake.calls) == 1


@pytest.mark.parametrize("state", list(ContainerStatus))
def test_states(state):
    fake = Fake({"status_code": state, "status": "provider text ignored"})
    assert client(fake).container_status("09") is state
    assert parse_qs(urlsplit(fake.calls[0][1]).query) == {
        "fields": ["status_code,status"]
    }


@pytest.mark.parametrize(
    "url",
    [
        None,
        42,
        "",
        "http://example.test/a",
        "https:///a",
        "https://u:p@example.test/a",
        "https://u@example.test/a",
        "https://example.test:bad/a",
        "https://example.test/a#x",
        "https://example.test/\nsecret",
    ],
)
def test_bad_urls_no_request(url):
    fake = Fake()
    with pytest.raises(ValueError):
        client(fake).create_image_container("1", url)
    assert not fake.calls


@pytest.mark.parametrize("value", [None, 1, "", "../me", "１", "a", "1?x=y"])
def test_bad_ids(value):
    fake = Fake()
    for call in (
        lambda: client(fake).publishing_quota(value),
        lambda: client(fake).container_status(value),
        lambda: client(fake).publish_container("1", value),
    ):
        with pytest.raises(ValueError):
            call()
    assert not fake.calls


@pytest.mark.parametrize(
    "timeout", [True, None, "1", 0, -1, 61, float("nan"), float("inf")]
)
def test_bad_timeout(timeout):
    with pytest.raises(ValueError):
        InstagramClient("synthetic", timeout=timeout)


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 302])
def test_http_errors_no_retry(status):
    fake = Fake(status=status, raw=b"sensitive provider body")
    with pytest.raises(APIError) as error:
        client(fake).publish_container("1", "2")
    assert error.value.http_status == status
    assert error.value.outcome_uncertain
    assert len(fake.calls) == 1
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize(
    "error",
    [TimeoutError("sensitive"), ConnectionError("sensitive"), OSError("sensitive")],
)
@pytest.mark.parametrize("post", [False, True])
def test_network_errors_no_retry_or_sensitive_chain(error, post):
    fake = Fake(error=error)
    with pytest.raises(TransportError) as caught:
        if post:
            client(fake).publish_container("1", "2")
        else:
            client(fake).identify_account()
    assert caught.value.outcome_uncertain is post
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "sensitive" not in repr(caught.value)
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"not JSON",
        b"[]",
        b"null",
        b"{}",
        b'{"id":12}',
        b'{"id":"bad"}',
        b"\xff",
        b"x" * 1_048_577,
    ],
)
def test_bad_responses(raw):
    fake = Fake(raw=raw)
    with pytest.raises(ResponseError) as error:
        client(fake).publish_container("1", "2")
    assert error.value.__context__ is None
    assert error.value.outcome_uncertain
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "data", [{}, {"status_code": "NEW"}, {"status_code": None}, {"status_code": []}]
)
def test_invalid_state(data):
    with pytest.raises(ResponseError):
        client(Fake(data)).container_status("1")


@pytest.mark.parametrize(
    "data",
    [
        {"data": []},
        {
            "data": [
                {"quota_usage": True, "config": {"quota_total": 1, "quota_duration": 1}}
            ]
        },
        {"data": [{"quota_usage": 0, "rate_limit_settings": {}}]},
    ],
)
def test_bad_quota(data):
    with pytest.raises(ResponseError):
        client(Fake(data)).publishing_quota("1")


def test_error_envelope_and_safe_logs(caplog):
    fake = Fake({"error": {"message": "private caption and URL"}})
    instance = client(fake)
    with caplog.at_level(logging.INFO, logger="ask_every_day.instagram_client"):
        with pytest.raises(APIError):
            instance.create_image_container(
                "1", "https://example.test/private", caption="private caption"
            )
    assert "synthetic-test-value" not in repr(instance)
    for record in caplog.records:
        output = JSONFormatter().format(record)
        assert "private" not in output and "synthetic-test-value" not in output
        assert json.loads(output)["http_status"] == 200
        assert record.exc_info is None


def test_success_log(caplog):
    with caplog.at_level(logging.INFO, logger="ask_every_day.instagram_client"):
        client(Fake({"id": "1"})).publish_container("2", "3")
    assert caplog.records[-1].event == "instagram_request_completed"


@pytest.mark.parametrize("value,expected", [(429, True), (True, False), ("429", False)])
def test_http_status_logging_allowlist(value, expected):
    record = logging.makeLogRecord({"http_status": value})
    assert ("http_status" in json.loads(JSONFormatter().format(record))) is expected


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("Network access is forbidden in client tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def test_production_transport_request_and_bounded_read(monkeypatch):
    from ask_every_day import instagram_client as module

    calls = []

    class Response:
        code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("closed")

        def read(self, limit):
            assert limit == 1_048_577
            return b'{"id":"9"}'

    class Opener:
        def open(self, request, timeout):
            assert request.method == "POST"
            assert request.full_url == BASE_URL + "/1/media_publish"
            assert request.data == b"creation_id=2"
            assert request.get_header("Authorization") == "Bearer synthetic"
            assert timeout == 4
            calls.append("open")
            return Response()

    def build(handler):
        assert (
            handler.redirect_request(None, None, 302, None, None, "https://other.test")
            is None
        )
        return Opener()

    monkeypatch.setattr(module, "build_opener", build)
    assert InstagramClient("synthetic", timeout=4).publish_container(
        "1", "2"
    ) == PublishedMedia("9")
    assert calls == ["open", "closed"]


def test_production_http_error_closes_response(monkeypatch):
    from email.message import Message
    from io import BytesIO
    from urllib.error import HTTPError

    from ask_every_day import instagram_client as module

    stream = BytesIO(b"sensitive")

    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 401, "sensitive", Message(), stream)

    monkeypatch.setattr(module, "build_opener", lambda handler: Opener())
    with pytest.raises(APIError) as caught:
        InstagramClient("synthetic").identify_account()
    assert caught.value.http_status == 401
    assert caught.value.__context__ is None
    assert stream.closed


@pytest.mark.parametrize("token", [None, "", "a\nb", "a b", "é"])
def test_invalid_token(token):
    with pytest.raises(ValueError):
        InstagramClient(token)


def test_invalid_unicode_is_sanitized():
    fake = Fake()
    with pytest.raises(ValueError) as caught:
        client(fake).create_image_container(
            "1", "https://example.test/a", caption="private\ud800"
        )
    assert "private" not in str(caught.value)
    assert caught.value.__context__ is None
    assert not fake.calls


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"user_id": 1, "username": "a", "account_type": "BUSINESS"},
        {"user_id": "1", "username": None, "account_type": "BUSINESS"},
        {"data": [{}, {}]},
    ],
)
def test_invalid_identity(data):
    with pytest.raises(ResponseError):
        client(Fake(data)).identify_account()


def test_media_evidence_uses_only_documented_instagram_login_get_fields():
    fake = Fake(
        {
            "id": "105",
            "owner": {"id": "103"},
            "media_type": "IMAGE",
            "timestamp": "2026-10-26T21:00:00+0000",
        }
    )
    result = client(fake).media_details("105")
    assert result.id == "105" and result.owner_id == "103"
    offset = result.published_at.utcoffset()
    assert offset is not None and offset.total_seconds() == 0
    method, url, _, body, _ = fake.calls[0]
    assert method == "GET" and body is None
    assert url.startswith(BASE_URL + "/105?")
    assert parse_qs(urlsplit(url).query) == {
        "fields": ["id,owner,media_type,timestamp"]
    }


@pytest.mark.parametrize(
    "change",
    [
        {"id": 123},
        {"id": "１２３"},
        {"owner": None},
        {"owner": {"id": 123}},
        {"media_type": "UNKNOWN"},
        {"timestamp": "2026-10-26T21:00:00"},
        {"timestamp": "SYNTHETIC_PRIVATE_BODY"},
    ],
)
def test_media_evidence_malformed_response_is_sanitized(change):
    fake = Fake(
        {
            "id": "105",
            "owner": {"id": "103"},
            "media_type": "IMAGE",
            "timestamp": "2026-10-26T21:00:00+0000",
            **change,
        }
    )
    with pytest.raises(ResponseError) as caught:
        client(fake).media_details("105")
    assert not caught.value.outcome_uncertain and caught.value.__context__ is None
    assert "SYNTHETIC_PRIVATE" not in str(caught.value)
