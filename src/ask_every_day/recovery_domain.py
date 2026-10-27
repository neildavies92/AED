"""Provider-specific recovery values; publication status remains in the domain."""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ask_every_day.domain import Publication, PublicationStatus, _aware
from ask_every_day.instagram_client import _id


class CheckpointPhase(StrEnum):
    PREPARED = "prepared"
    CREATE_INTENT = "create_intent"
    CONTAINER_KNOWN = "container_known"
    PUBLISH_INTENT = "publish_intent"
    COMPLETED = "completed"
    CLOSED = "closed"


class Resolution(StrEnum):
    PUBLISH_RESPONSE = "publish_response"
    OPERATOR_RESPONSE = "operator_response"
    CONTAINER_ERROR = "container_error"
    CONTAINER_EXPIRED = "container_expired"
    OPERATOR_ABORT = "operator_abort"


def sha256_value(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Expected a SHA256 digest")
    return value


@dataclass(frozen=True)
class PublicationContext:
    account_id: str
    username: str
    asset_sha256: str

    def __post_init__(self) -> None:
        _id(self.account_id)
        if self.username != "ask.every.day":
            raise ValueError("Unexpected publication account")
        sha256_value(self.asset_sha256)


@dataclass(frozen=True)
class PublicationCheckpoint:
    publication_id: int
    account_id: str
    username: str
    asset_sha256: str
    phase: CheckpointPhase
    checkpoint_at: datetime
    revision: int = 0
    create_intent_at: datetime | None = None
    publish_intent_at: datetime | None = None
    container_id: str | None = None
    resolution: Resolution | None = None
    evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if type(self.publication_id) is not int or self.publication_id < 1:
            raise ValueError("Expected a publication ID")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("Invalid checkpoint revision")
        PublicationContext(self.account_id, self.username, self.asset_sha256)
        if not isinstance(self.phase, CheckpointPhase):
            raise TypeError("Expected a checkpoint phase")
        if self.resolution is not None and not isinstance(self.resolution, Resolution):
            raise TypeError("Expected a recovery resolution")
        _aware(self.checkpoint_at, "checkpoint_at")
        for instant in (self.create_intent_at, self.publish_intent_at):
            if instant is not None:
                _aware(instant, "intent time")
                if instant > self.checkpoint_at:
                    raise ValueError("Intent follows checkpoint")
        if self.container_id is not None:
            _id(self.container_id)
        if self.evidence_sha256 is not None:
            sha256_value(self.evidence_sha256)
        if self.publish_intent_at is not None and (
            self.create_intent_at is None
            or self.publish_intent_at < self.create_intent_at
        ):
            raise ValueError("Invalid intent chronology")
        if self.phase is CheckpointPhase.PREPARED:
            valid = (
                self.create_intent_at
                is self.publish_intent_at
                is self.container_id
                is None
            )
        elif self.phase is CheckpointPhase.CREATE_INTENT:
            valid = (
                self.create_intent_at is not None
                and self.publish_intent_at is self.container_id is None
            )
        elif self.phase is CheckpointPhase.CONTAINER_KNOWN:
            valid = (
                self.create_intent_at is not None
                and self.container_id is not None
                and self.publish_intent_at is None
            )
        elif self.phase in (CheckpointPhase.PUBLISH_INTENT, CheckpointPhase.COMPLETED):
            valid = all(
                v is not None
                for v in (
                    self.create_intent_at,
                    self.publish_intent_at,
                    self.container_id,
                )
            )
        else:
            valid = self.publish_intent_at is None and (
                (
                    self.resolution is Resolution.OPERATOR_ABORT
                    and self.create_intent_at is self.container_id is None
                )
                or (
                    self.resolution
                    in (Resolution.CONTAINER_ERROR, Resolution.CONTAINER_EXPIRED)
                    and self.create_intent_at is not None
                    and self.container_id is not None
                )
            )
        if not valid:
            raise ValueError("Contradictory checkpoint fields")
        if self.phase is CheckpointPhase.COMPLETED:
            valid = self.resolution in (
                Resolution.PUBLISH_RESPONSE,
                Resolution.OPERATOR_RESPONSE,
            )
            valid = valid and (
                (self.resolution is Resolution.OPERATOR_RESPONSE)
                == (self.evidence_sha256 is not None)
            )
        elif self.phase is CheckpointPhase.CLOSED:
            valid = self.resolution in (
                Resolution.CONTAINER_ERROR,
                Resolution.CONTAINER_EXPIRED,
                Resolution.OPERATOR_ABORT,
            )
            valid = valid and self.evidence_sha256 is None
        else:
            valid = self.resolution is self.evidence_sha256 is None
        if not valid:
            raise ValueError("Contradictory checkpoint resolution")


def validate_checkpoint(
    publication: Publication, checkpoint: PublicationCheckpoint | None
) -> None:
    """A legacy record has no invented context; contradictory new context is invalid."""
    if checkpoint is None:
        return
    checkpoint.__post_init__()
    if checkpoint.phase is CheckpointPhase.COMPLETED:
        if publication.status is not PublicationStatus.PUBLISHED:
            raise ValueError("Completed checkpoint conflicts with publication")
        _id(publication.external_id)
    elif checkpoint.phase is CheckpointPhase.CLOSED:
        if publication.status is not PublicationStatus.FAILED:
            raise ValueError("Closed checkpoint conflicts with publication")
    elif (
        publication.status is not PublicationStatus.PUBLISHING
        or publication.external_id is not None
    ):
        raise ValueError("Active checkpoint conflicts with publication")
