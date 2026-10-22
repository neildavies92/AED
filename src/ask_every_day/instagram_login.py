"""Browser authorization orchestration; no media or publishing operations."""

import time
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, Self

from ask_every_day.instagram_auth import (
    AuthConfig,
    AuthError,
    AuthorizationAttempt,
    AuthTransport,
    CredentialManager,
    CredentialStore,
    InstagramAuthClient,
    InstagramCredential,
    _identify,
    auth_transport,
)
from ask_every_day.instagram_callback import LocalCallbackReceiver
from ask_every_day.instagram_client import InstagramIdentity


class LoginStore(CredentialStore, Protocol):
    def app_secret(self) -> str: ...


class CallbackReceiver(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> None: ...

    def wait(self, attempt: AuthorizationAttempt) -> str: ...


def login(
    config: AuthConfig,
    store: LoginStore,
    *,
    browser: Callable[[str], bool] = webbrowser.open,
    receiver_factory: Callable[[AuthConfig], CallbackReceiver] = LocalCallbackReceiver,
    transport: AuthTransport = auth_transport,
    identify: Callable[[str], InstagramIdentity] = _identify,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
) -> InstagramCredential:
    attempt = None
    failure = "login"
    http_status = None
    uncertain = False
    try:
        secret = store.app_secret()
        attempt = AuthorizationAttempt(config, clock=monotonic)
        provider = InstagramAuthClient(config, transport=transport)
        manager = CredentialManager(provider, store, clock=clock, identify=identify)
        with receiver_factory(config) as receiver:
            if not browser(attempt.authorization_url()):
                raise AuthError("browser_startup")
            code = receiver.wait(attempt)
            return manager.connect(code, secret)
    except AuthError as error:
        failure = error.operation
        http_status = error.http_status
        uncertain = error.outcome_uncertain
    except Exception:
        pass
    finally:
        if attempt is not None:
            attempt.cancel()
    raise AuthError(failure, http_status=http_status, uncertain=uncertain)
