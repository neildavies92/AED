"""Offline participant in an owner-controlled host schedule transaction.

The host holds its admission lock; this process holds the existing publication
lock until launchd has been reconfigured. SQLite is opened read-only throughout.
"""

import argparse
import json
import select
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.publication_recovery import snapshot
from ask_every_day.publish_next_cli import publication_lock
from ask_every_day.repository import Repository, open_repository
from ask_every_day.scheduler_policy import (
    LONDON,
    ScheduleConfig,
    ScheduleError,
    daily_block,
    load_inputs,
    load_schedule,
    policy_key,
    read_authorization,
    save_authorization,
    validate_authorization,
)


def validate_change(
    repository: Repository,
    old: ScheduleConfig,
    new: ScheduleConfig,
    effective: date,
    now: datetime,
) -> None:
    if (
        replace(
            old, hour=new.hour, minute=new.minute, effective_from=effective.isoformat()
        )
        != new
        or new.window_minutes != 30
        or new.authorization_days != 30
        or effective < now.astimezone(LONDON).date()
    ):
        raise ScheduleError("schedule_time_only_change_required")
    rows = repository.list_publications()
    if any(
        snapshot(repository, row.id).report()["operator_action_required"]
        for row in rows
    ):
        raise ScheduleError("unresolved_publication_reconcile_required")
    block = daily_block(rows, now)
    if effective == now.astimezone(LONDON).date():
        if block:
            raise ScheduleError(block)
        local = now.astimezone(LONDON)
        target = local.replace(
            hour=new.hour, minute=new.minute, second=0, microsecond=0, fold=0
        )
        if (
            target.astimezone(UTC) <= now.astimezone(UTC)
            or target.astimezone(UTC).astimezone(LONDON) != target
        ):
            raise ScheduleError("same_day_schedule_must_be_future")
    if any(
        row.publication.status.value != "Published"
        and row.publication.attempted_at.astimezone(LONDON).date()
        == now.astimezone(LONDON).date()
        for row in rows
    ):
        raise ScheduleError("failed_attempt_requires_reconciliation_or_reapproval")


def participate(
    config_path: Path,
    schedule_path: Path,
    data: Path,
    hour: int,
    minute: int,
    effective: date,
    receive: Callable[[], str],
    emit: Callable[[dict[str, object]], None],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    config = load_config(config_path)
    with (
        publication_lock(config.database_path),
        open_repository(config, read_only=True) as repository,
    ):
        old = load_schedule(schedule_path)
        new = replace(
            old, hour=hour, minute=minute, effective_from=effective.isoformat()
        )
        inputs = load_inputs(data)
        now = clock()
        validate_change(repository, old, new, effective, now)
        authorization = read_authorization(config)
        reason = "disabled"
        preserved = False
        if authorization and authorization.enabled:
            try:
                validate_authorization(
                    repository, config, inputs, old, authorization, now
                )
                preserved = True
                reason = "valid_time_only_change"
            except ScheduleError as error:
                # Invalid approvals never become valid through a schedule edit.
                reason = str(error)
        new_key = policy_key(repository, config, inputs, new)
        emit(
            {
                "phase": "ready",
                "authorization_preserved": preserved,
                "reauthorization_reason": None if preserved else reason,
                "expires_at": authorization.expires_at.isoformat()
                if authorization
                else None,
            }
        )
        command = receive()
        if command != "commit":
            emit({"phase": "aborted"})
            return
        now = clock()
        validate_change(repository, old, new, effective, now)
        if (
            load_config(config_path) != config
            or load_schedule(schedule_path) != new
            or load_inputs(data) != inputs
            or read_authorization(config) != authorization
            or policy_key(repository, config, inputs, new) != new_key
        ):
            raise ScheduleError("schedule_change_preview_changed")
        if preserved:
            assert authorization is not None
            validate_authorization(repository, config, inputs, old, authorization, now)
            save_authorization(config, replace(authorization, policy_key=new_key))
        elif authorization and authorization.enabled:
            save_authorization(config, replace(authorization, enabled=False))
        emit({"phase": "committed", "authorization_preserved": preserved})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hour", type=int, required=True)
    parser.add_argument("--minute", type=int, required=True)
    parser.add_argument("--effective-day", type=date.fromisoformat, required=True)
    parser.add_argument("--config", type=Path, default=Path("/etc/aed/aed.toml"))
    parser.add_argument(
        "--schedule-config", type=Path, default=Path("/etc/aed/schedule.toml")
    )
    parser.add_argument("--data-dir", type=Path, default=Path("/var/lib/aed/data"))
    args = parser.parse_args()

    def receive():
        if not select.select([sys.stdin], [], [], 180)[0]:
            raise ScheduleError("schedule_change_timeout")
        return sys.stdin.readline(64).strip()

    def emit(value):
        print(json.dumps(value), flush=True)

    try:
        participate(
            args.config,
            args.schedule_config,
            args.data_dir,
            args.hour,
            args.minute,
            args.effective_day,
            receive,
            emit,
        )
    except Exception as error:
        emit(
            {
                "phase": "blocked",
                "reason": str(error)
                if isinstance(error, ScheduleError)
                else "schedule_change_failed",
            }
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
