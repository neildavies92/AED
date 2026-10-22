"""Browser login and local credential setup/status with safe metadata output."""

import argparse
import getpass
import json
import logging
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ask_every_day.credential_store import KeychainCredentialStore
from ask_every_day.instagram_auth import AuthConfig, AuthError
from ask_every_day.instagram_login import login
from ask_every_day.logging_config import configure_logging

_LOG = logging.getLogger("ask_every_day.instagram_auth")


def load_auth_config(path: Path) -> AuthConfig:
    result = None
    try:
        with path.open("rb") as stream:
            payload = stream.read(8193)
        if len(payload) > 8192:
            raise ValueError
        data = tomllib.loads(payload.decode("utf-8"))
        required = {"app_id", "redirect_uri"}
        optional = {"tls_certificate", "tls_private_key"}
        if not required <= set(data) or set(data) - required - optional:
            raise ValueError
        for name in optional & set(data):
            if not isinstance(data[name], str) or not data[name]:
                raise ValueError
            data[name] = Path(data[name]).expanduser()
        result = AuthConfig(**data)
    except Exception:
        pass
    if result is None:
        raise AuthError("auth_configuration")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage local Instagram credentials")
    parser.add_argument("command", choices=("login", "bootstrap", "status", "logout"))
    parser.add_argument("--config", type=Path, default=Path("instagram-auth.toml"))
    args = parser.parse_args()
    configure_logging()
    failure = None
    output: dict[str, object] = {}
    try:
        config = load_auth_config(args.config)
        store = KeychainCredentialStore(config.app_id)
        if args.command == "login":
            credential = login(config, store)
            output = {
                "connected": True,
                "account": "@ask.every.day",
                "credential_status": "valid",
                "expires_at": credential.expires_at.isoformat(),
            }
        elif args.command == "bootstrap":
            if not sys.stdin.isatty():
                raise AuthError("interactive_input_required")
            store.set_app_secret(getpass.getpass("Instagram App Secret (hidden): "))
            output = {"app_secret_stored": True}
        elif args.command == "logout":
            store.delete()
            store.delete_app_secret()
            output = {"connected": False, "app_secret_stored": False}
            _LOG.info(
                "instagram_credential_removed",
                extra={"event": "instagram_credential_removed"},
            )
        else:
            credential = store.load()
            output = {"connected": credential is not None, "credential_valid": False}
            if credential is not None:
                now = datetime.now(UTC)
                output.update(
                    account="@ask.every.day",
                    credential_valid=credential.issued_at
                    <= now
                    < credential.expires_at,
                    expires_at=credential.expires_at.isoformat(),
                    refresh_due=credential.expires_at - now <= timedelta(days=7),
                )
    except KeyboardInterrupt:
        failure = "cancelled"
    except AuthError as error:
        failure = error.operation
    except Exception:
        failure = "local_operation"
    if failure:
        _LOG.error(
            "instagram_auth_failed",
            extra={
                "event": "instagram_auth_failed",
                "operation": failure,
                "error_type": "AuthError",
            },
        )
        message = {
            "localhost_certificate": "Configure trusted localhost TLS certificate files",
            "missing_app_secret": "Run make instagram-auth-bootstrap first",
            "browser_startup": "Could not open the system browser",
            "callback_timeout": "Instagram login timed out; start a fresh login",
            "cancelled": "Instagram login cancelled",
        }.get(failure, "Instagram credential operation failed; check local setup")
        parser.exit(1, message + "; see docs/instagram-auth.md.\n")
    print(json.dumps(output, separators=(",", ":")))


if __name__ == "__main__":
    main()
