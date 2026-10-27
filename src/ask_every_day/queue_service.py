"""Select eligible runtime content and persist one complete constrained queue."""

import logging

from ask_every_day.domain import QueueEntry
from ask_every_day.publication_service import (
    InvalidTransition,
    PublicationState,
    publication_state,
)
from ask_every_day.queue_generation import generate_queue
from ask_every_day.queue_rules import QueueRules
from ask_every_day.recovery_domain import validate_checkpoint
from ask_every_day.repository import Repository

_LOGGER = logging.getLogger(__name__)


class ExistingQueueError(ValueError):
    """Explicit regeneration is required before replacing a nonempty queue."""


def generate_and_store_queue(
    repository: Repository,
    *,
    seed: int,
    rules: QueueRules = QueueRules(),
    regenerate: bool = False,
    max_attempts: int = 32,
) -> list[QueueEntry]:
    """Queue all no-attempt posts; regeneration replaces rather than merges."""
    try:
        with repository.transaction():
            existing = repository.list_queue()
            if existing and not regenerate:
                raise ExistingQueueError(
                    "Queue already exists; pass regenerate=True to replace it"
                )
            queued_ids = {entry.post_id for entry in existing}
            posts = repository.list_posts()
            if queued_ids - {post.id for post in posts}:
                raise InvalidTransition("Queue references unknown runtime posts")
            eligible = []
            for post in posts:
                history = repository.publication_history(post.id)
                for record in history:
                    validate_checkpoint(
                        record.publication, repository.get_checkpoint(record.id)
                    )
                state = publication_state(history, queued=post.id in queued_ids)
                if state in (PublicationState.READY, PublicationState.QUEUED):
                    eligible.append(post)
            ordered = generate_queue(
                eligible, seed=seed, rules=rules, max_attempts=max_attempts
            )
            entries = [
                QueueEntry(post.id, position)
                for position, post in enumerate(ordered, start=1)
            ]
            repository.replace_queue(entries)
    except Exception as exc:
        _LOGGER.warning(
            "Queue generation failed",
            extra={
                "event": "queue_generation_failed",
                "seed": seed,
                "error_type": type(exc).__name__,
            },
        )
        raise
    _LOGGER.info(
        "Generated queue committed",
        extra={
            "event": "queue_generated",
            "seed": seed,
            "queue_length": len(entries),
            "post_count": len(eligible),
            "excluded_count": len(posts) - len(eligible),
            "operation": "regenerate" if regenerate else "generate",
            "category_min_gap": rules.category_min_gap,
            "subcategory_min_gap": rules.subcategory_min_gap,
        },
    )
    return entries
