"""Full login orchestration with injected browser, receiver, store and provider."""

import json
from datetime import UTC, datetime
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from ask_every_day import instagram_login as module
from ask_every_day.instagram_auth import REDIRECT_URI, SCOPES, AuthConfig, AuthError
from ask_every_day.instagram_client import InstagramIdentity

NOW = datetime(2026, 10, 22, tzinfo=UTC)


@pytest.fixture
def flow():
    store = Mock()
    store.app_secret.return_value = "SYNTHETIC_PRIVATE_SECRET"
    store.value = None

    def save(credential):
        store.value = credential

    store.save.side_effect = save
    identity = Mock(
        return_value=InstagramIdentity("100000000000002", "ask.every.day", "BUSINESS")
    )
    data = {}
    calls = []

    class Receiver:
        closed = False
        attempt = None

        def __enter__(self):
            calls.append("start")
            return self

        def __exit__(self, *args):
            self.closed = True
            calls.append("close")

        def wait(self, attempt):
            calls.append("wait")
            self.attempt = attempt
            return attempt.consume(
                urlencode({"state": data["state"], "code": "SYNTHETIC_PRIVATE_CODE"})
            )

    receiver = Receiver()

    def browser(url):
        assert calls == ["start"]
        data.update({k: v[0] for k, v in parse_qs(urlsplit(url).query).items()})
        calls.append("browser")
        return True

    responses = [
        {
            "data": [
                {
                    "access_token": "SYNTHETIC_PRIVATE_SHORT",
                    "user_id": "100000000000003",
                    "permissions": ",".join(sorted(SCOPES)),
                }
            ]
        },
        {
            "access_token": "SYNTHETIC_PRIVATE_LONG",
            "token_type": "bearer",
            "expires_in": 60 * 86400,
        },
    ]

    def transport(method, url, body, content_type):
        calls.append(method)
        assert "media" not in url
        return 200, json.dumps(responses.pop(0)).encode()

    kwargs = dict(
        browser=browser,
        receiver_factory=lambda _: receiver,
        transport=transport,
        identify=identity,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    return (
        AuthConfig("100000000000001", REDIRECT_URI),
        store,
        receiver,
        kwargs,
        calls,
        data,
    )


def test_complete_login_verifies_identity_before_atomic_save(flow, caplog):
    config, store, receiver, kwargs, calls, data = flow
    result = module.login(config, store, **kwargs)
    assert calls == ["start", "browser", "wait", "POST", "GET", "close"]
    assert result.username == "ask.every.day" and result.issued_at == NOW
    assert set(data["scope"].split(",")) == SCOPES
    assert result.authorization_user_id != result.user_id
    store.app_secret.assert_called_once_with()
    store.save.assert_called_once_with(result)
    assert store.value is result
    kwargs["identify"].assert_called_once_with("SYNTHETIC_PRIVATE_LONG")
    assert receiver.closed and receiver.attempt._state == ""
    assert "SYNTHETIC_PRIVATE" not in caplog.text + repr(result)


@pytest.mark.parametrize(
    "mode",
    [
        "missing_secret",
        "store_read",
        "startup",
        "browser_false",
        "browser_error",
        "timeout",
        "rejected",
        "exchange",
        "extension",
        "identity",
        "wrong_account",
        "personal",
        "store_write",
        "cancel",
    ],
)
def test_every_login_failure_closes_receiver_without_partial_credential(
    flow, monkeypatch, mode, caplog
):
    config, store, receiver, kwargs, calls, _ = flow
    attempts = []
    factory = module.AuthorizationAttempt

    def capture(*args, **values):
        attempt = factory(*args, **values)
        attempts.append(attempt)
        return attempt

    monkeypatch.setattr(module, "AuthorizationAttempt", capture)
    if mode == "missing_secret":
        store.app_secret.side_effect = AuthError("missing_app_secret")
    if mode == "store_read":
        store.app_secret.side_effect = OSError("SYNTHETIC_PRIVATE")
    if mode == "startup":
        kwargs["receiver_factory"] = Mock(side_effect=AuthError("callback_startup"))
    if mode == "browser_false":
        kwargs["browser"] = Mock(return_value=False)
    if mode == "browser_error":
        kwargs["browser"] = Mock(side_effect=RuntimeError("SYNTHETIC_PRIVATE"))
    if mode == "timeout":
        monkeypatch.setattr(
            receiver, "wait", Mock(side_effect=AuthError("callback_timeout"))
        )
    if mode == "rejected":
        monkeypatch.setattr(
            receiver,
            "wait",
            lambda attempt: attempt.consume(
                "state=SYNTHETIC_WRONG_STATE&code=SYNTHETIC_PRIVATE_CODE"
            ),
        )
    if mode == "exchange":
        kwargs["transport"] = Mock(return_value=(400, b"SYNTHETIC_PRIVATE"))
    if mode == "extension":
        original = kwargs["transport"]

        def transport(*args):
            if args[0] == "GET":
                raise TimeoutError("SYNTHETIC_PRIVATE")
            return original(*args)

        kwargs["transport"] = transport
    if mode == "identity":
        kwargs["identify"].side_effect = RuntimeError("SYNTHETIC_PRIVATE")
    if mode == "wrong_account":
        kwargs["identify"].return_value = InstagramIdentity(
            "100000000000002", "wrong.account", "BUSINESS"
        )
    if mode == "personal":
        kwargs["identify"].return_value = InstagramIdentity(
            "100000000000002", "ask.every.day", "PERSONAL"
        )
    if mode == "store_write":
        store.save.side_effect = OSError("SYNTHETIC_PRIVATE")
    if mode == "cancel":
        kwargs["browser"] = Mock(side_effect=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt if mode == "cancel" else AuthError) as caught:
        module.login(config, store, **kwargs)
    assert "SYNTHETIC_PRIVATE" not in str(caught.value) + caplog.text
    assert caught.value.__context__ is None
    assert all(attempt._state == "" for attempt in attempts)
    if mode not in {"missing_secret", "store_read", "startup"}:
        assert receiver.closed and calls[-1] == "close"
    else:
        assert "browser" not in calls and "wait" not in calls
    if mode != "store_write":
        store.save.assert_not_called()
    else:
        store.save.assert_called_once()
    store.delete.assert_not_called()
    assert store.value is None


def test_identity_is_required_before_credential_persistence(flow):
    config, store, _, kwargs, _, _ = flow

    def identify(token):
        store.save.assert_not_called()
        return InstagramIdentity("100000000000002", "ask.every.day", "MEDIA_CREATOR")

    kwargs["identify"] = identify
    result = module.login(config, store, **kwargs)
    assert result.username == "ask.every.day"
