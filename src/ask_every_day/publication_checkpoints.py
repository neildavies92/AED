"""Short, monotonic SQLite checkpoints; never issue or resume remote operations."""

import logging
from dataclasses import replace
from datetime import datetime

from ask_every_day.domain import PublicationStatus
from ask_every_day.instagram_client import _id
from ask_every_day.publication_service import InvalidTransition, publication_state
from ask_every_day.recovery_domain import CheckpointPhase, PublicationCheckpoint
from ask_every_day.repository import Repository

_LOG = logging.getLogger(__name__)


class CheckpointService:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def _advance(
        self,
        publication_id: int,
        previous: CheckpointPhase,
        target: CheckpointPhase,
        at: datetime,
        *,
        container_id: str | None = None,
    ) -> PublicationCheckpoint:
        with self._repository.transaction():
            record = self._repository.get_publication(publication_id)
            checkpoint = self._repository.get_checkpoint(publication_id)
            if record is None or checkpoint is None:
                raise InvalidTransition("Missing durable publication context")
            publication_state(
                self._repository.publication_history(record.publication.post_id),
                queued=any(
                    e.post_id == record.publication.post_id
                    for e in self._repository.list_queue()
                ),
            )
            if (
                record.publication.status is not PublicationStatus.PUBLISHING
                or checkpoint.phase is not previous
            ):
                raise InvalidTransition("Stale or terminal publication checkpoint")
            result = replace(
                checkpoint,
                phase=target,
                checkpoint_at=at,
                revision=checkpoint.revision + 1,
                create_intent_at=at
                if target is CheckpointPhase.CREATE_INTENT
                else checkpoint.create_intent_at,
                publish_intent_at=at
                if target is CheckpointPhase.PUBLISH_INTENT
                else checkpoint.publish_intent_at,
                container_id=container_id
                if target is CheckpointPhase.CONTAINER_KNOWN
                else checkpoint.container_id,
            )
            self._repository.update_checkpoint(result)
        _LOG.info(
            "publication_checkpoint_committed",
            extra={
                "event": "publication_checkpoint_committed",
                "publication_id": publication_id,
                "post_id": record.publication.post_id,
                "checkpoint_phase": result.phase.value,
                "checkpoint_revision": result.revision,
            },
        )
        return result

    def create_intent(self, publication_id: int, at: datetime) -> PublicationCheckpoint:
        return self._advance(
            publication_id, CheckpointPhase.PREPARED, CheckpointPhase.CREATE_INTENT, at
        )

    def container_known(
        self, publication_id: int, container_id: str, at: datetime
    ) -> PublicationCheckpoint:
        return self._advance(
            publication_id,
            CheckpointPhase.CREATE_INTENT,
            CheckpointPhase.CONTAINER_KNOWN,
            at,
            container_id=_id(container_id),
        )

    def publish_intent(
        self, publication_id: int, at: datetime
    ) -> PublicationCheckpoint:
        return self._advance(
            publication_id,
            CheckpointPhase.CONTAINER_KNOWN,
            CheckpointPhase.PUBLISH_INTENT,
            at,
        )
