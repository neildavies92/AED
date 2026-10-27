"""Read-only-first reconciliation and explicit local results; no remote POSTs."""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ask_every_day.domain import AppConfig, PublicationStatus
from ask_every_day.instagram_auth import InstagramCredential
from ask_every_day.instagram_client import (
    ContainerStatus,
    InstagramClient,
    InstagramIdentity,
    MediaDetails,
    _id,
)
from ask_every_day.publication_assets import _object, validate_palette_success
from ask_every_day.publication_service import PublicationService, publication_state
from ask_every_day.recovery_domain import (
    CheckpointPhase,
    PublicationCheckpoint,
    sha256_value,
    validate_checkpoint,
)
from ask_every_day.repository import PublicationRecord, Repository

_LOG = logging.getLogger(__name__)


class RecoveryError(ValueError):
    """Fixed diagnostics only; evidence/transport bodies never enter error output."""


@dataclass(frozen=True)
class RecoverySnapshot:
    record: PublicationRecord
    checkpoint: PublicationCheckpoint | None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                asdict(self), sort_keys=True, default=lambda value: value.isoformat()
            ).encode()
        ).hexdigest()

    def report(self, *, show_ids: bool = False) -> dict[str, object]:
        p, c = self.record.publication, self.checkpoint
        report: dict[str, object] = {
            "attempt_id": self.record.id,
            "post_id": p.post_id,
            "status": p.status.value,
            "checkpoint": c.phase.value if c else "legacy_uncheckpointed",
            "revision": c.revision if c else None,
            "resolution": c.resolution.value if c and c.resolution else None,
            "evidence_attached": bool(c and c.evidence_sha256),
            "checkpoint_at": c.checkpoint_at.isoformat() if c else None,
            "container_known": bool(c and c.container_id),
            "media_known": p.external_id is not None,
            "definitely_completed": p.status is PublicationStatus.PUBLISHED,
            "remote_outcome_uncertain": p.status is not PublicationStatus.PUBLISHED
            and not (
                c
                and (
                    c.phase is CheckpointPhase.PREPARED
                    or c.phase is CheckpointPhase.CLOSED
                )
            ),
            "operator_action_required": p.status is PublicationStatus.PUBLISHING
            or (c is None and p.status is PublicationStatus.FAILED),
        }
        if show_ids:
            report.update(
                account_id=c.account_id if c else None,
                container_id=c.container_id if c else None,
                media_id=p.external_id,
            )
        return report


def snapshot(repository: Repository, publication_id: int) -> RecoverySnapshot:
    if type(publication_id) is not int or publication_id < 1:
        raise RecoveryError("invalid_attempt_id")
    with repository.transaction():
        record = repository.get_publication(publication_id)
        if record is None:
            raise RecoveryError("unknown_attempt")
        checkpoint = repository.get_checkpoint(publication_id)
        validate_checkpoint(record.publication, checkpoint)
        publication_state(
            repository.publication_history(record.publication.post_id),
            queued=any(
                e.post_id == record.publication.post_id for e in repository.list_queue()
            ),
        )
        return RecoverySnapshot(record, checkpoint)


class RecoveryCredentials(Protocol):
    def local_credential(self) -> InstagramCredential: ...


class ReadClient(Protocol):
    def identify_account(self) -> InstagramIdentity: ...
    def container_status(self, container_id: str) -> ContainerStatus: ...
    def media_details(self, media_id: str) -> MediaDetails: ...


@dataclass(frozen=True)
class RemoteEvidence:
    account: InstagramIdentity
    container_status: ContainerStatus | None
    media: MediaDetails | None
    observed_at: datetime


def remote_inspection(
    current: RecoverySnapshot,
    credentials: RecoveryCredentials,
    *,
    media_id: str | None = None,
    client_factory: Callable[[str], ReadClient] = InstagramClient,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RemoteEvidence:
    """Only documented Instagram Login GETs, with no credential refresh/write."""
    checkpoint = current.checkpoint
    if checkpoint is None:
        raise RecoveryError("legacy_missing_account_binding")
    result = None
    try:
        credential = credentials.local_credential()
        now = clock()
        if not credential.issued_at <= now < credential.expires_at:
            raise RecoveryError("credential_not_ready")
        if (
            credential.user_id != checkpoint.account_id
            or credential.username != checkpoint.username
        ):
            raise RecoveryError("account_binding")
        client = client_factory(credential.access_token)
        identity = client.identify_account()
        if (
            identity.user_id != checkpoint.account_id
            or identity.username != checkpoint.username
            or identity.account_type.casefold() not in {"business", "media_creator"}
        ):
            raise RecoveryError("account_binding")
        state = (
            client.container_status(checkpoint.container_id)
            if checkpoint.container_id
            else None
        )
        if state is not None and not isinstance(state, ContainerStatus):
            raise RecoveryError("invalid_container_status")
        requested = media_id or current.record.publication.external_id
        media = client.media_details(_id(requested)) if requested else None
        if media is not None:
            media.__post_init__()
            if (
                media.id != requested
                or media.owner_id != checkpoint.account_id
                or media.media_type != "IMAGE"
                or media.published_at < current.record.publication.attempted_at
                or media.published_at > now
            ):
                raise RecoveryError("contradictory_media_evidence")
        result = RemoteEvidence(identity, state, media, now)
    except Exception:
        pass
    if result is None:
        _LOG.warning(
            "publication_reconciliation_lookup_failed",
            extra={
                "event": "publication_reconciliation_lookup_failed",
                "publication_id": current.record.id,
                "post_id": current.record.publication.post_id,
                "error_type": "RecoveryError",
            },
        )
        raise RecoveryError("remote_evidence_unavailable_or_conflicting")
    return result


@dataclass(frozen=True)
class ResponseEvidence:
    attempt_id: int
    account_id: str
    container_id: str
    media_id: str
    source: str
    sha256: str

    def __post_init__(self) -> None:
        if type(self.attempt_id) is not int or self.attempt_id < 1:
            raise RecoveryError("invalid_response_evidence")
        for value in (self.account_id, self.container_id, self.media_id):
            _id(value)
        sha256_value(self.sha256)
        if self.source != "captured_publish_response":
            raise RecoveryError("unsupported_response_evidence")


def load_response_evidence(path: Path) -> ResponseEvidence:
    result = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            payload = stream.read(16_385)
        if len(payload) > 16_384:
            raise ValueError
        data = json.loads(payload, object_pairs_hook=_object)
        if set(data) != {
            "attempt_id",
            "account_id",
            "container_id",
            "media_id",
            "source",
        }:
            raise ValueError
        if type(data["attempt_id"]) is not int or data["attempt_id"] < 1:
            raise ValueError
        for name in ("account_id", "container_id", "media_id"):
            _id(data[name])
        if data["source"] != "captured_publish_response":
            raise ValueError
        result = ResponseEvidence(**data, sha256=hashlib.sha256(payload).hexdigest())
    except Exception:
        pass
    if result is None:
        raise RecoveryError("invalid_response_evidence")
    return result


@dataclass(frozen=True)
class RecoveryPlan:
    action: str
    snapshot: RecoverySnapshot
    remote: RemoteEvidence
    response: ResponseEvidence | None

    @property
    def challenge(self) -> str:
        c = self.snapshot.checkpoint
        assert c is not None
        token = hashlib.sha256(
            json.dumps(
                asdict(self), sort_keys=True, default=lambda value: value.isoformat()
            ).encode()
        ).hexdigest()[:16]
        return f"RECOVER {self.action.upper()} {self.snapshot.record.id} @{c.username} {token}"


def plan_recovery(
    current: RecoverySnapshot,
    remote: RemoteEvidence,
    action: str,
    response: ResponseEvidence | None = None,
) -> RecoveryPlan:
    c, r = current.checkpoint, current.record
    if c is None or r.publication.status is not PublicationStatus.PUBLISHING:
        raise RecoveryError("attempt_not_recoverable")
    if remote.account.user_id != c.account_id or remote.account.username != c.username:
        raise RecoveryError("account_binding")
    if action == "published":
        if c.phase is not CheckpointPhase.PUBLISH_INTENT or response is None:
            raise RecoveryError("publish_mapping_required")
        response.__post_init__()
        if (
            response.attempt_id != r.id
            or response.account_id != c.account_id
            or response.container_id != c.container_id
            or remote.media is None
            or remote.media.id != response.media_id
            or remote.media.owner_id != c.account_id
            or remote.container_status is not ContainerStatus.PUBLISHED
        ):
            raise RecoveryError("contradictory_response_mapping")
    elif action == "failed":
        if response is not None:
            raise RecoveryError("unexpected_response_evidence")
        if c.phase is not CheckpointPhase.PREPARED and not (
            c.phase is CheckpointPhase.CONTAINER_KNOWN
            and remote.container_status
            in (ContainerStatus.ERROR, ContainerStatus.EXPIRED)
        ):
            raise RecoveryError("remote_absence_not_proven")
    else:
        raise RecoveryError("invalid_recovery_action")
    return RecoveryPlan(action, current, remote, response)


def apply_recovery(
    repository: Repository,
    config: AppConfig,
    plan: RecoveryPlan,
    confirmation: str,
    credentials: RecoveryCredentials,
    *,
    client_factory: Callable[[str], ReadClient] = InstagramClient,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Caller holds the publication lock; operator attests exact captured mapping."""
    if confirmation != plan.challenge:
        raise RecoveryError("recovery_confirmation_required")
    if config.account_handle != "@ask.every.day":
        raise RecoveryError("account_configuration")
    if snapshot(repository, plan.snapshot.record.id) != plan.snapshot:
        raise RecoveryError("stale_recovery_snapshot")
    fresh = remote_inspection(
        plan.snapshot,
        credentials,
        media_id=plan.response.media_id if plan.response else None,
        client_factory=client_factory,
        clock=clock,
    )
    if (fresh.account, fresh.container_status, fresh.media) != (
        plan.remote.account,
        plan.remote.container_status,
        plan.remote.media,
    ):
        raise RecoveryError("remote_evidence_changed")
    plan_recovery(plan.snapshot, fresh, plan.action, plan.response)
    c = plan.snapshot.checkpoint
    assert c is not None
    service = PublicationService(repository)
    expected = (plan.snapshot.record, c)
    if plan.action == "published":
        assert plan.response is not None and fresh.media is not None
        service.record_success(
            c.publication_id,
            fresh.media.published_at,
            plan.response.media_id,
            expected=expected,
            evidence_sha256=plan.response.sha256,
            checkpoint_at=clock(),
            validate_result=lambda: validate_palette_success(
                repository,
                config,
                plan.snapshot.record.publication.post_id,
                c.asset_sha256,
            ),
        )
    else:
        error = (
            "operator_abort"
            if c.phase is CheckpointPhase.PREPARED
            else (
                "container_error"
                if fresh.container_status is ContainerStatus.ERROR
                else "container_expired"
            )
        )
        service.record_failure(
            c.publication_id, error, expected=expected, checkpoint_at=clock()
        )
    _LOG.info(
        "publication_recovery_committed",
        extra={
            "event": "publication_recovery_committed",
            "publication_id": c.publication_id,
            "post_id": plan.snapshot.record.publication.post_id,
            "operation": "recover_" + plan.action,
            "checkpoint_revision": c.revision + 1,
        },
    )


def backup_state(config: AppConfig, directory: Path) -> Path:
    """Caller holds publication_lock; copy SQLite and its sidecar without migration."""
    target = None
    failed = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = Path(tempfile.mkdtemp(prefix="publication-state-", dir=directory))
        database = config.database_path.resolve(strict=True)
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        ) as source:
            with closing(sqlite3.connect(target / "state.sqlite3")) as destination:
                source.backup(destination)
                version = destination.execute("PRAGMA user_version").fetchone()[0]
                if (
                    destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or destination.execute("PRAGMA foreign_key_check").fetchall()
                ):
                    raise ValueError
        os.chmod(target / "state.sqlite3", 0o600)
        palette = database.with_name(database.name + ".palette.json")
        palette_present = False
        try:
            descriptor = os.open(palette, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            descriptor = None
        if descriptor is not None:
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError
                payload = stream.read(1_048_577)
            if len(payload) > 1_048_576:
                raise ValueError
            (target / "state.sqlite3.palette.json").write_bytes(payload)
            os.chmod(target / "state.sqlite3.palette.json", 0o600)
            palette_present = True
        manifest = {
            "schema_version": version,
            "palette_present": palette_present,
            "files": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in target.iterdir()
            },
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        )
        for path in target.iterdir():
            os.chmod(path, 0o600)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        for directory_path in (target, target.parent):
            descriptor = os.open(directory_path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception:
        failed = True
    if failed:
        if target is not None:
            shutil.rmtree(target)
        raise RecoveryError("publication_state_backup_failed")
    assert target is not None
    return target.resolve()
