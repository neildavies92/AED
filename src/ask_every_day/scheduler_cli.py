"""Portable scheduler process boundary with explicit opt-in and safe diagnostics."""

import argparse
import json
import logging
import os
import stat
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.instagram_auth import CredentialManager
from ask_every_day.logging_config import JSONFormatter, configure_logging
from ask_every_day.production_runtime import (
    DockerPublisherRequired,
    require_docker_publisher,
)
from ask_every_day.publish_next import PublishError
from ask_every_day.publish_next_cli import credential_manager, publication_lock
from ask_every_day.repository import open_repository
from ask_every_day.scheduler import run_daily
from ask_every_day.scheduler_policy import (
    LONDON,
    ScheduleError,
    authorization_preview,
    daily_block,
    load_inputs,
    load_schedule,
    read_authorization,
    save_authorization,
    validate_authorization,
)

_LOG = logging.getLogger(__name__)


class _PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        descriptor = os.open(
            self.baseFilename,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            os.close(descriptor)
            raise ScheduleError("log_configuration")
        return os.fdopen(descriptor, "a", encoding="utf-8")


def _safe_provider_id(value: object) -> str | None:
    return (
        value
        if isinstance(value, str)
        and 0 < len(value) <= 128
        and value.isascii()
        and value.isdigit()
        else None
    )


def _bounded_log(path: Path) -> None:
    """Local adapter log; no launchd output paths that can grow without bounds."""
    if not path.is_absolute() or not path.parent.is_dir():
        raise ScheduleError("log_configuration")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ScheduleError("log_configuration")
    finally:
        os.close(descriptor)
    application = logging.getLogger("ask_every_day")
    handler = _PrivateRotatingHandler(path, maxBytes=1_048_576, backupCount=3)
    handler.setFormatter(JSONFormatter())
    for existing in list(application.handlers):
        if existing.name == "ask_every_day_json_stderr":
            application.removeHandler(existing)
    application.addHandler(handler)


def main(
    argv: Sequence[str] | None = None,
    *,
    credential_factory: Callable[[Path], CredentialManager] = credential_manager,
) -> None:
    parser = argparse.ArgumentParser(
        description="Inspect or execute one authorized daily publication"
    )
    parser.add_argument(
        "command",
        choices=("preview", "run", "status", "enable", "disable", "check-credentials"),
    )
    parser.add_argument("--config", type=Path, default=Path("aed.toml"))
    parser.add_argument("--schedule-config", type=Path, default=Path("schedule.toml"))
    parser.add_argument("--auth-config", type=Path, default=Path("instagram-auth.toml"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args(argv)
    configure_logging()
    failure = None
    known_ids: dict[str, object] = {}
    output: dict[str, object] = {}
    try:
        if args.command == "run":
            require_docker_publisher()
        if args.log_file is not None:
            _bounded_log(args.log_file)
        if args.command == "enable" and not (
            sys.stdin.isatty() and sys.stdout.isatty()
        ):
            raise ScheduleError("interactive_authorization_required")
        now = datetime.now(UTC)
        if args.command == "check-credentials":
            # No refresh, provider request, staging, SQL or policy mutation.
            credential = credential_factory(args.auth_config).local_credential()
            output = {
                "mode": "local_credential_check",
                "account": "@ask.every.day",
                "credential_ready": True,
                "expires_at": credential.expires_at.isoformat(),
            }
        else:
            config = load_config(args.config)
            if args.command == "disable":
                with publication_lock(config.database_path):
                    authorization = read_authorization(config)
                    if authorization is not None:
                        save_authorization(
                            config, replace(authorization, enabled=False)
                        )
                output = {"mode": "authorization_disabled", "enabled": False}
            elif args.command == "run":
                # Disabled requires no content, schedule, credential or network access.
                authorization = read_authorization(config)
                if authorization is None or not authorization.enabled:
                    output = {
                        "outcome": "disabled",
                        "local_day": now.astimezone(LONDON).date().isoformat(),
                    }
                else:
                    output = asdict(
                        run_daily(
                            config,
                            load_inputs(args.data_dir),
                            load_schedule(args.schedule_config),
                            lambda: credential_factory(args.auth_config),
                            reload_policy=lambda: (
                                load_config(args.config),
                                load_inputs(args.data_dir),
                                load_schedule(args.schedule_config),
                            ),
                        )
                    )
            elif args.command == "status" and not (
                (current := read_authorization(config)) and current.enabled
            ):
                output = {
                    "mode": "local_read_only",
                    "enabled": False,
                    "authorization_valid": False,
                }
            else:
                schedule = load_schedule(args.schedule_config)
                inputs = load_inputs(args.data_dir)
                if args.command == "enable":
                    with (
                        publication_lock(config.database_path),
                        open_repository(config, read_only=True) as repository,
                    ):
                        proposal = authorization_preview(
                            repository, config, inputs, schedule, now
                        )
                        print(
                            json.dumps(
                                {
                                    "mode": "authorization_proposal",
                                    "account": config.account_handle,
                                    "timezone": schedule.timezone,
                                    "daily_time": f"{schedule.hour:02}:{schedule.minute:02}",
                                    "window_minutes": schedule.window_minutes,
                                    "queue_count": len(proposal.queue),
                                    "policy_key": proposal.policy_key,
                                    "expires_at": proposal.expires_at.isoformat(),
                                    "challenge": proposal.challenge,
                                },
                                indent=2,
                            )
                        )
                        print(
                            "Authorize unattended future publications of this approved snapshot and queue. Type exactly: "
                            + proposal.challenge,
                            flush=True,
                        )
                        answer = input("> ")
                        if answer != proposal.challenge:
                            raise ScheduleError("authorization_confirmation_required")
                        # Rebuild while still holding lock to reject changes during prompt.
                        if load_config(args.config) != config:
                            raise ScheduleError("authorization_preview_changed")
                        if (
                            authorization_preview(
                                repository,
                                config,
                                load_inputs(args.data_dir),
                                load_schedule(args.schedule_config),
                                now,
                            )
                            != proposal
                        ):
                            raise ScheduleError("authorization_preview_changed")
                        save_authorization(config, proposal)
                        output = {
                            "mode": "authorization_enabled",
                            "enabled": True,
                            "policy_key": proposal.policy_key,
                            "expires_at": proposal.expires_at.isoformat(),
                        }
                else:
                    with open_repository(config, read_only=True) as repository:
                        if args.command == "preview":
                            proposal = authorization_preview(
                                repository, config, inputs, schedule, now
                            )
                            output = {
                                "mode": "local_read_only",
                                "enabled": bool(
                                    (current := read_authorization(config))
                                    and current.enabled
                                ),
                                "policy_key": proposal.policy_key,
                                "queue_count": len(proposal.queue),
                                "timezone": schedule.timezone,
                                "daily_time": f"{schedule.hour:02}:{schedule.minute:02}",
                                "window_minutes": schedule.window_minutes,
                                "expires_at": proposal.expires_at.isoformat(),
                                "in_window": schedule.in_window(now),
                                "daily_block": daily_block(
                                    repository.list_publications(), now
                                ),
                            }
                        else:
                            current = read_authorization(config)
                            assert current is not None
                            validate_authorization(
                                repository, config, inputs, schedule, current, now
                            )
                            output = {
                                "mode": "local_read_only",
                                "enabled": current.enabled,
                                "authorization_valid": True,
                                "expires_at": current.expires_at.isoformat(),
                                "in_window": schedule.in_window(now),
                                "daily_block": daily_block(
                                    repository.list_publications(), now
                                ),
                            }
        _LOG.info(
            "scheduler_command_completed",
            extra={
                "event": "scheduler_command_completed",
                "operation": str(output.get("outcome", output.get("mode"))),
            },
        )
        print(json.dumps(output, indent=2))
    except (KeyboardInterrupt, EOFError):
        failure = "scheduler_cancelled"
    except Exception as error:
        if isinstance(error, PublishError):
            known_ids = {
                "publication_id": error.attempt_id
                if type(error.attempt_id) is int and error.attempt_id > 0
                else None,
                "container_id": _safe_provider_id(error.container_id),
                "media_id": _safe_provider_id(error.media_id),
            }
        failure = (
            str(error)
            if isinstance(error, (ScheduleError, DockerPublisherRequired))
            else error.reason
            if isinstance(error, PublishError)
            else "scheduler_operation_failed"
        )
    if failure:
        _LOG.error(
            "scheduler_command_failed",
            extra={
                "event": "scheduler_command_failed",
                "operation": failure,
                "error_type": "ScheduleError",
                **known_ids,
            },
        )
        print(json.dumps({"outcome": "blocked", "reason": failure, **known_ids}))
        parser.exit(1, failure + "; no automatic POST retry; see docs/scheduler.md.\n")


if __name__ == "__main__":
    main()
