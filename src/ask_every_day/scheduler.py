"""Portable one-shot daily workflow; host timers provide triggers only."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ask_every_day.domain import AppConfig, PublicationStatus
from ask_every_day.instagram_client import InstagramClient
from ask_every_day.media_staging import stage_media
from ask_every_day.publication_assets import prepare_next_asset
from ask_every_day.publish_next import (
    Credentials,
    PublishError,
    PublishingClient,
    Stage,
    preview_next,
    publish_approved,
)
from ask_every_day.publish_next_cli import publication_lock
from ask_every_day.repository import open_repository
from ask_every_day.scheduler_policy import (
    LONDON,
    ApprovedInputs,
    ScheduleConfig,
    ScheduleError,
    daily_block,
    read_authorization,
    validate_authorization,
)

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionSummary:
    outcome: str
    local_day: str
    post_id: str | None = None
    attempt_id: int | None = None


def run_daily(
    config: AppConfig,
    inputs: ApprovedInputs,
    schedule: ScheduleConfig,
    credential_factory: Callable[[], Credentials],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    client_factory: Callable[[str], PublishingClient] = InstagramClient,
    stage: Stage = stage_media,
    sleep: Callable[[float], None] = time.sleep,
    reload_policy: Callable[[], tuple[AppConfig, ApprovedInputs, ScheduleConfig]]
    | None = None,
) -> ExecutionSummary:
    """Acquire one shared lock and perform zero or one checkpointed publication."""
    with publication_lock(config.database_path):
        now = clock()
        day = now.astimezone(LONDON).date()
        authorization = read_authorization(config)
        if authorization is None or not authorization.enabled:
            return ExecutionSummary("disabled", day.isoformat())
        # First inspect without migration, canonical asset writes or provider calls.
        with open_repository(config, read_only=True) as repository:
            validate_authorization(
                repository, config, inputs, schedule, authorization, now
            )
            if not schedule.in_window(now):
                return ExecutionSummary("outside_window", day.isoformat())
            block = daily_block(repository.list_publications(), now)
            if block:
                return ExecutionSummary(block, day.isoformat())
            if repository.next_queue_entry() is None:
                return ExecutionSummary("queue_empty", day.isoformat())
        with open_repository(config) as repository:

            def check(own_attempt: int | None = None) -> None:
                current = clock()
                if current.astimezone(LONDON).date() != day or not schedule.in_window(
                    current
                ):
                    raise ScheduleError("execution_window_closed")
                if reload_policy is not None and reload_policy() != (
                    config,
                    inputs,
                    schedule,
                ):
                    raise ScheduleError("policy_reapproval_required")
                fresh = read_authorization(config)
                if fresh is None or fresh != authorization:
                    raise ScheduleError("authorization_changed")
                validate_authorization(
                    repository,
                    config,
                    inputs,
                    schedule,
                    fresh,
                    current,
                    own_attempt=own_attempt,
                )
                block = daily_block(
                    repository.list_publications(), current, own_attempt=own_attempt
                )
                if block:
                    raise ScheduleError(block)

            def guard(own_attempt: int | None = None) -> None:
                try:
                    check(own_attempt)
                except ScheduleError as error:
                    raise PublishError(str(error)) from None

            guard()
            prepare_next_asset(repository, config)
            plan = preview_next(repository, config, inputs.catalog)
            if plan is None or plan.asset_sha256 is None:
                raise ScheduleError("prepared_asset_unavailable")
            guard()
            credentials = credential_factory()
            result = publish_approved(
                repository,
                config,
                inputs.catalog,
                plan,
                plan.challenge,
                credentials,
                client_factory=client_factory,
                stage=stage,
                clock=clock,
                sleep=sleep,
                dispatch_guard=guard,
            )
            record = repository.get_publication(result.attempt_id)
            if (
                record is None
                or record.publication.status is not PublicationStatus.PUBLISHED
            ):
                raise ScheduleError("publication_result_unverified")
        # Independent reopening verifies the durable result, not process memory.
        with open_repository(config, read_only=True) as reopened:
            record = reopened.get_publication(result.attempt_id)
            if (
                record is None
                or record.publication.external_id != result.media_id
                or record.publication.status is not PublicationStatus.PUBLISHED
            ):
                raise ScheduleError("publication_result_unverified")
        _LOG.info(
            "scheduled_publication_completed",
            extra={
                "event": "scheduled_publication_completed",
                "post_id": result.post_id,
                "publication_id": result.attempt_id,
                "operation": "published",
            },
        )
        return ExecutionSummary(
            "published", day.isoformat(), result.post_id, result.attempt_id
        )
