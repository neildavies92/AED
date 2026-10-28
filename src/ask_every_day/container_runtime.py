"""Portable process adapter; scheduled publication belongs only to AED-027."""

import argparse
import getpass
import importlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.file_credential_store import FileCredentialStore
from ask_every_day.instagram_auth import (
    AuthError,
    CredentialManager,
    InstagramAuthClient,
)
from ask_every_day.instagram_auth_cli import load_auth_config
from ask_every_day.logging_config import configure_logging
from ask_every_day.publication_recovery import backup_state, snapshot
from ask_every_day.publish_next import CopyCatalog, preview_next
from ask_every_day.publish_next_cli import publication_lock
from ask_every_day.repository import open_repository

_LOG = logging.getLogger(__name__)


def credential_factory(path: Path, directory: Path) -> CredentialManager:
    config = load_auth_config(path)
    return CredentialManager(
        InstagramAuthClient(config), FileCredentialStore(config.app_id, directory)
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="AED container adapter (safe preview by default)"
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="preview",
        choices=(
            "preview",
            "publication-status",
            "backup",
            "credential-bootstrap",
            "credential-status",
            "credential-delete",
            "credential-verify",
            "monitor",
            "reconcile",
            "schedule-preview",
            "schedule-run",
            "schedule-status",
            "schedule-enable",
            "schedule-disable",
            "schedule-check-credentials",
        ),
    )
    parser.add_argument("--config", type=Path, default=Path("/etc/aed/aed.toml"))
    parser.add_argument(
        "--auth-config", type=Path, default=Path("/etc/aed/instagram-auth.toml")
    )
    parser.add_argument(
        "--schedule-config", type=Path, default=Path("/etc/aed/schedule.toml")
    )
    parser.add_argument("--data-dir", type=Path, default=Path("/opt/aed/data"))
    parser.add_argument(
        "--credential-dir", type=Path, default=Path("/var/lib/aed-credentials")
    )
    parser.add_argument("--backup-dir", type=Path, default=Path("/var/lib/aed/backups"))
    parser.add_argument(
        "--log-file", type=Path, help="Persistent bounded scheduler JSON log"
    )
    parser.add_argument("--attempt-id", type=int)
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--resolve", choices=("published", "failed"))
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--show-ids", action="store_true")
    args = parser.parse_args(argv)
    if args.command != "reconcile" and any(
        (
            args.attempt_id,
            args.remote,
            args.resolve,
            args.evidence,
            args.confirm,
            args.show_ids,
        )
    ):
        parser.error("recovery options require reconcile")
    if args.log_file is not None and not args.command.startswith("schedule-"):
        parser.error("--log-file requires a schedule-* command")
    configure_logging()
    failure = False
    _LOG.info(
        "container_command_started",
        extra={
            "event": "container_command_started",
            "operation": args.command.replace("-", "_"),
        },
    )
    try:
        if args.command == "reconcile":
            from ask_every_day.publication_recovery_cli import main as recover

            forwarded = [
                "reconcile",
                "--config",
                str(args.config),
                "--auth-config",
                str(args.auth_config),
            ]
            for key in ("attempt_id", "resolve", "evidence"):
                value = getattr(args, key)
                if value is not None:
                    forwarded.extend(["--" + key.replace("_", "-"), str(value)])
            for key in ("remote", "confirm", "show_ids"):
                if getattr(args, key):
                    forwarded.append("--" + key.replace("_", "-"))
            recover(
                forwarded,
                credential_factory=lambda path: credential_factory(
                    path, args.credential_dir
                ),
            )
            return
        if args.command == "monitor":
            from ask_every_day.deployment import main as deployment

            deployment(
                [
                    "monitor",
                    "--config",
                    str(args.config),
                    "--schedule-config",
                    str(args.schedule_config),
                ]
            )
            return
        if args.command.startswith("schedule-"):
            # No substitute publisher or policy: capability is present only after A merges.
            module = importlib.import_module("ask_every_day.scheduler_cli")
            forwarded = [
                args.command.removeprefix("schedule-"),
                "--config",
                str(args.config),
                "--auth-config",
                str(args.auth_config),
                "--schedule-config",
                str(args.schedule_config),
                "--data-dir",
                str(args.data_dir),
            ]
            if args.log_file is not None:
                forwarded.extend(["--log-file", str(args.log_file)])
            module.main(
                forwarded,
                credential_factory=lambda path: credential_factory(
                    path, args.credential_dir
                ),
            )
            return
        if args.command.startswith("credential-"):
            if args.command == "credential-verify":
                manager = credential_factory(args.auth_config, args.credential_dir)
                # Explicit command: identity GET and eligible token refresh; no media POST.
                credential = manager.get_valid_credential()
                print(
                    json.dumps(
                        {
                            "identity_verified": True,
                            "account": credential.username,
                            "expires_at": credential.expires_at.isoformat(),
                        }
                    )
                )
                return
            config = load_auth_config(args.auth_config)
            store = FileCredentialStore(config.app_id, args.credential_dir)
            if args.command == "credential-bootstrap":
                if not sys.stdin.isatty() or not sys.stdout.isatty():
                    raise AuthError("interactive_input_required")
                print(
                    "Store only an independently authorized credential in the private file backend. No Keychain export occurs."
                )
                # Reuse strict codec validation; no token or secret enters argv/env.
                from ask_every_day.credential_store import KeychainCredentialStore

                payload = getpass.getpass(
                    "Authorized credential JSON (hidden): "
                ).encode()

                class ImportBackend:
                    def read(self, service, account):
                        return payload

                    def write(self, service, account, value):
                        raise AuthError("read_only_import")

                    def delete(self, service, account):
                        raise AuthError("read_only_import")

                # Parse through the exact existing persisted-credential codec.
                imported = KeychainCredentialStore(
                    config.app_id, backend=ImportBackend()
                ).load()
                if imported is None:
                    raise AuthError("missing_credential")
                now = datetime.now(UTC)
                if not imported.issued_at <= now < imported.expires_at:
                    raise AuthError("expired_credential")
                store.save(imported)
                output = {"credential_stored": True, "account": "@ask.every.day"}
            elif args.command == "credential-delete":
                store.delete()
                store.delete_app_secret()
                output = {"credential_stored": False, "provider_grant_revoked": False}
            else:
                credential = store.load()
                now = datetime.now(UTC)
                output = {
                    "credential_stored": credential is not None,
                    "credential_valid": bool(
                        credential
                        and credential.issued_at <= now < credential.expires_at
                    ),
                }
            print(json.dumps(output))
            return
        config = load_config(args.config)
        if args.command == "backup":
            with publication_lock(config.database_path):
                saved = backup_state(config, args.backup_dir)
            print(json.dumps({"backup": str(saved)}))
            return
        with open_repository(config, read_only=True) as repository:
            if args.command == "publication-status":
                print(
                    json.dumps(
                        {
                            "mode": "local_read_only",
                            "attempts": [
                                snapshot(repository, r.id).report()
                                for r in repository.list_publications()
                            ],
                        }
                    )
                )
                return
            plan = preview_next(repository, config, CopyCatalog.load(args.data_dir))
            print(
                json.dumps(
                    {
                        "mode": "local_read_only",
                        "queue": "empty" if plan is None else "ready",
                        "post_id": plan.post.id if plan else None,
                        "asset_ready": bool(plan and plan.asset_sha256),
                        "background_color": plan.background_color if plan else None,
                    }
                )
            )
    except SystemExit:
        raise
    except (KeyboardInterrupt, EOFError):
        failure = True
    except Exception:
        failure = True
    if failure:
        _LOG.error(
            "container_operation_blocked",
            extra={
                "event": "container_operation_blocked",
                "operation": args.command.replace("-", "_"),
                "error_type": "RuntimeError",
            },
        )
        parser.exit(
            1,
            "Container operation blocked; check configuration, private storage and AED-027 availability; no automatic publish retry.\n",
        )


if __name__ == "__main__":
    main()
