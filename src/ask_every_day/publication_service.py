"""Local state and atomic checkpointed results; remote reads live in recovery."""

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from enum import StrEnum

from ask_every_day.domain import Publication, PublicationStatus, QueueEntry
from ask_every_day.instagram_client import _id
from ask_every_day.recovery_domain import (
    CheckpointPhase,
    PublicationCheckpoint,
    PublicationContext,
    Resolution,
    validate_checkpoint,
)
from ask_every_day.repository import PublicationRecord, Repository, to_microseconds

_LOGGER = logging.getLogger(__name__)


class PublicationState(StrEnum):
    READY = "Ready"
    QUEUED = "Queued"
    PUBLISHING = "Publishing"
    PUBLISHED = "Published"
    FAILED = "Failed"


class InvalidTransition(ValueError):
    """The local state does not permit the requested transition."""


_ALLOWED = {
    (PublicationState.READY, PublicationState.QUEUED),
    (PublicationState.QUEUED, PublicationState.PUBLISHING),
    (PublicationState.PUBLISHING, PublicationState.PUBLISHED),
    (PublicationState.PUBLISHING, PublicationState.FAILED),
}


def validate_transition(previous: PublicationState, target: PublicationState) -> None:
    """Published and Failed are terminal until explicit recovery policy exists."""
    if (previous, target) not in _ALLOWED:
        raise InvalidTransition(f"Cannot transition {previous.value} to {target.value}")


def publication_state(
    history: Sequence[PublicationRecord], *, queued: bool
) -> PublicationState:
    """Derive state without storing a second status beside attempt records."""
    if not history:
        return PublicationState.QUEUED if queued else PublicationState.READY
    if queued:
        raise InvalidTransition("Attempt history and queue membership conflict")
    ordered = sorted(
        history, key=lambda r: (to_microseconds(r.publication.attempted_at), r.id)
    )
    if list(history) != ordered or len({r.id for r in history}) != len(history):
        raise InvalidTransition("Invalid attempt chronology requires reconciliation")
    if len({r.publication.post_id for r in history}) != 1:
        raise InvalidTransition("Mixed post history requires reconciliation")
    if any(r.publication.status is not PublicationStatus.FAILED for r in history[:-1]):
        raise InvalidTransition(
            "Unresolved or terminal earlier attempt requires reconciliation"
        )
    return PublicationState(history[-1].publication.status.value)


class PublicationService:
    """Transition persisted local state with explicit timestamps and atomic writes."""

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def _state(self, post_id: str) -> PublicationState:
        if not self._repository.post_exists(post_id):
            raise KeyError(post_id)
        history = self._repository.publication_history(post_id)
        for record in history:
            validate_checkpoint(
                record.publication, self._repository.get_checkpoint(record.id)
            )
        return publication_state(
            history,
            queued=any(e.post_id == post_id for e in self._repository.list_queue()),
        )

    def state(self, post_id: str) -> PublicationState:
        with self._repository.transaction():
            return self._state(post_id)

    @contextmanager
    def _transition(
        self, post_id: str, target: PublicationState, publication_id: int | None = None
    ) -> Iterator[None]:
        previous: PublicationState | None = None
        try:
            with self._repository.transaction():
                previous = self._state(post_id)
                validate_transition(previous, target)
                yield
        except Exception as exc:
            _LOGGER.warning(
                "Publication transition failed",
                extra={
                    "event": "publication_transition_failed",
                    "post_id": post_id,
                    "previous_state": previous,
                    "new_state": target,
                    "publication_id": publication_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _LOGGER.info(
            "Publication transition committed",
            extra={
                "event": "publication_transition_committed",
                "post_id": post_id,
                "previous_state": previous,
                "new_state": target,
                "publication_id": publication_id,
            },
        )

    def enqueue(self, entry: QueueEntry) -> None:
        with self._transition(entry.post_id, PublicationState.QUEUED):
            self._repository.replace_queue([*self._repository.list_queue(), entry])

    def begin_publish(
        self,
        post_id: str,
        attempted_at: datetime,
        *,
        context: PublicationContext | None = None,
    ) -> PublicationRecord:
        with self._transition(post_id, PublicationState.PUBLISHING):
            attempt = Publication(post_id, PublicationStatus.PUBLISHING, attempted_at)
            record = self._repository.create_publication(attempt)
            if context is not None:
                self._repository.create_checkpoint(
                    PublicationCheckpoint(
                        record.id,
                        context.account_id,
                        context.username,
                        context.asset_sha256,
                        CheckpointPhase.PREPARED,
                        attempted_at,
                    )
                )
            self._repository.remove_queue_entry(post_id)
        return record

    def _active(self, publication_id: int) -> PublicationRecord:
        record = self._repository.get_publication(publication_id)
        if record is None:
            raise KeyError(publication_id)
        if record.publication.status is not PublicationStatus.PUBLISHING:
            raise InvalidTransition("Only an active attempt can receive a result")
        return record

    def record_success(
        self,
        publication_id: int,
        published_at: datetime,
        external_id: str | None = None,
        *,
        expected: tuple[PublicationRecord, PublicationCheckpoint] | None = None,
        evidence_sha256: str | None = None,
        checkpoint_at: datetime | None = None,
        validate_result: Callable[[], None] | None = None,
    ) -> None:
        record = self._repository.get_publication(publication_id)
        if record is None:
            raise KeyError(publication_id)
        with self._transition(
            record.publication.post_id, PublicationState.PUBLISHED, publication_id
        ):
            current = self._active(publication_id)
            checkpoint = self._repository.get_checkpoint(publication_id)
            if expected is not None and (current, checkpoint) != expected:
                raise InvalidTransition("Stale recovery snapshot")
            if evidence_sha256 is not None and expected is None:
                raise InvalidTransition(
                    "Operator evidence requires an exact recovery snapshot"
                )
            if checkpoint is not None:
                if checkpoint.phase is not CheckpointPhase.PUBLISH_INTENT:
                    raise InvalidTransition(
                        "Published requires a durable publish intent"
                    )
                _id(external_id)
                if any(
                    r.id != publication_id
                    and r.publication.status is PublicationStatus.PUBLISHED
                    and r.publication.external_id == external_id
                    for r in self._repository.list_publications()
                ):
                    raise InvalidTransition(
                        "Media is already attached to a publication"
                    )
            self._repository.update_publication(
                publication_id,
                replace(
                    current.publication,
                    status=PublicationStatus.PUBLISHED,
                    published_at=published_at,
                    external_id=external_id,
                ),
            )
            if checkpoint is not None:
                self._repository.update_checkpoint(
                    replace(
                        checkpoint,
                        phase=CheckpointPhase.COMPLETED,
                        checkpoint_at=checkpoint_at or published_at,
                        revision=checkpoint.revision + 1,
                        resolution=Resolution.OPERATOR_RESPONSE
                        if evidence_sha256
                        else Resolution.PUBLISH_RESPONSE,
                        evidence_sha256=evidence_sha256,
                    )
                )
            if validate_result is not None:
                validate_result()

    def record_failure(
        self,
        publication_id: int,
        error: str | None = None,
        *,
        expected: tuple[PublicationRecord, PublicationCheckpoint] | None = None,
        checkpoint_at: datetime | None = None,
    ) -> None:
        record = self._repository.get_publication(publication_id)
        if record is None:
            raise KeyError(publication_id)
        with self._transition(
            record.publication.post_id, PublicationState.FAILED, publication_id
        ):
            current = self._active(publication_id)
            checkpoint = self._repository.get_checkpoint(publication_id)
            if expected is not None and (current, checkpoint) != expected:
                raise InvalidTransition("Stale recovery snapshot")
            resolution = None
            if checkpoint is not None:
                if (
                    checkpoint.phase is CheckpointPhase.PREPARED
                    and expected is not None
                    and error == "operator_abort"
                ):
                    resolution = Resolution.OPERATOR_ABORT
                elif checkpoint.phase is CheckpointPhase.CONTAINER_KNOWN and error in (
                    "container_error",
                    "container_expired",
                ):
                    resolution = Resolution(error)
                else:
                    raise InvalidTransition(
                        "Uncertain remote operation cannot become Failed"
                    )
            self._repository.update_publication(
                publication_id,
                replace(
                    current.publication,
                    status=PublicationStatus.FAILED,
                    error=error,
                ),
            )
            if checkpoint is not None:
                self._repository.update_checkpoint(
                    replace(
                        checkpoint,
                        phase=CheckpointPhase.CLOSED,
                        checkpoint_at=checkpoint_at or checkpoint.checkpoint_at,
                        revision=checkpoint.revision + 1,
                        resolution=resolution,
                    )
                )
