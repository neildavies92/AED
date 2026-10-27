"""Preview and execute one explicitly approved publication; never retry a POST."""

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, Self

from ask_every_day.caption_catalog import load_captions, load_profiles
from ask_every_day.content_import import load_hashtag_snapshot, load_snapshot
from ask_every_day.copy_policy import (
    Caption,
    CaptionProfile,
    HashtagSet,
    select_caption,
    select_hashtag_set,
)
from ask_every_day.domain import AppConfig, Post, QueueEntry
from ask_every_day.instagram_auth import InstagramCredential
from ask_every_day.instagram_client import (
    Container,
    ContainerStatus,
    InstagramClient,
    InstagramIdentity,
    PublishedMedia,
    PublishingQuota,
    _id,
)
from ask_every_day.media_staging import StagedMedia, stage_media
from ask_every_day.publication_assets import (
    AssetError,
    asset_preview,
    queued_post,
    validate_palette_success,
)
from ask_every_day.publication_checkpoints import CheckpointService
from ask_every_day.publication_service import (
    PublicationService,
    PublicationState,
)
from ask_every_day.recovery_domain import PublicationContext
from ask_every_day.repository import Repository

_LOG = logging.getLogger(__name__)


class PublishError(Exception):
    """Fixed diagnostic and known identifiers, never provider payloads or URLs."""

    def __init__(
        self,
        reason: str,
        *,
        attempt_id: int | None = None,
        container_id: str | None = None,
        media_id: str | None = None,
    ) -> None:
        self.reason = reason
        self.attempt_id = attempt_id
        self.container_id = container_id
        self.media_id = media_id
        super().__init__(reason)


@dataclass(frozen=True)
class CopyCatalog:
    captions: Sequence[Caption]
    profiles: dict[str, CaptionProfile]
    hashtags: Sequence[HashtagSet]

    @classmethod
    def load(cls, directory: Path) -> Self:
        # Profiles describe the approved snapshot, not just the remaining queue.
        posts = load_snapshot(
            directory / "prompts.csv",
            directory / "taxonomy.csv",
            directory / "hashtags.csv",
        )
        return cls(
            load_captions(directory / "captions.csv", directory / "taxonomy.csv"),
            load_profiles(directory / "caption_profiles.csv", [p.id for p in posts]),
            load_hashtag_snapshot(directory / "hashtags.csv"),
        )


@dataclass(frozen=True)
class PublishPlan:
    post: Post
    entry: QueueEntry
    account: str
    asset_path: Path
    asset_sha256: str | None
    caption: str
    hashtags: tuple[str, ...]
    background_color: str
    palette_sequence: int
    palette_key: str

    @property
    def fingerprint(self) -> str:
        payload = {**asdict(self), "asset_path": str(self.asset_path)}
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    @property
    def challenge(self) -> str:
        return f"PUBLISH {self.account} {self.post.id} {self.fingerprint[:12]}"


def preview_next(
    repository: Repository, config: AppConfig, catalog: CopyCatalog
) -> PublishPlan | None:
    """Read local state/copy/JPEG only; no credentials, network or queue mutation."""
    if config.account_handle != "@ask.every.day":
        raise PublishError("account_configuration")
    with repository.transaction():
        failure = None
        chosen = None
        try:
            chosen = queued_post(repository)
        except AssetError as error:
            failure = str(error)
        if failure:
            raise PublishError(failure)
        if chosen is None:
            return None
        post, entry = chosen
        selected = select_caption(
            post,
            catalog.captions,
            catalog.profiles.get(post.id),
            position=entry.position,
        )
        tags = select_hashtag_set(
            post.category, catalog.hashtags, explicit_set=post.hashtag_set_id
        )
        hashtags = tags.hashtags if tags else ()
        caption = selected.caption + ("\n\n" + " ".join(hashtags) if hashtags else "")
        if len(caption) > 2200 or len(re.findall(r"(?<!\w)#\w+", caption)) > 30:
            raise PublishError("caption_limits")
        assignment = None
        digest = None
        try:
            assignment, digest = asset_preview(repository, config, post, entry)
        except AssetError as error:
            failure = str(error)
        if failure:
            raise PublishError(failure)
        assert assignment is not None
        return PublishPlan(
            post,
            entry,
            config.account_handle,
            config.assets_path / f"{post.id}.jpg",
            digest,
            caption,
            hashtags,
            assignment.background,
            assignment.sequence,
            assignment.palette_key,
        )


class Credentials(Protocol):
    def local_credential(self) -> InstagramCredential: ...
    def get_valid_credential(self) -> InstagramCredential: ...


class PublishingClient(Protocol):
    def identify_account(self) -> InstagramIdentity: ...
    def publishing_quota(self, account_id: str) -> PublishingQuota: ...
    def create_image_container(
        self, account_id: str, image_url: str, *, caption: str | None = None
    ) -> Container: ...
    def container_status(self, container_id: str) -> ContainerStatus: ...
    def publish_container(
        self, account_id: str, container_id: str
    ) -> PublishedMedia: ...


class Stage(Protocol):
    def __call__(
        self,
        repository: Repository,
        config: AppConfig,
        post_id: str,
        *,
        expected_sha256: str | None = None,
    ) -> AbstractContextManager[StagedMedia]: ...


@dataclass(frozen=True)
class PublishResult:
    post_id: str
    attempt_id: int
    container_id: str
    media_id: str


def _provider_id(value: str) -> str:
    result = None
    try:
        result = _id(value)
    except ValueError:
        pass
    if result is None:
        raise PublishError("invalid_provider_result")
    return result


def publish_approved(
    repository: Repository,
    config: AppConfig,
    catalog: CopyCatalog,
    plan: PublishPlan,
    confirmation: str,
    credentials: Credentials,
    *,
    client_factory: Callable[[str], PublishingClient] = InstagramClient,
    stage: Stage = stage_media,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    dispatch_guard: Callable[[int | None], None] | None = None,
) -> PublishResult:
    """Caller holds the lock and obtains exact manual or validated policy approval."""
    attempt_id = None
    container_id = None
    media_id = None
    phase = "preflight"
    failure = None
    try:
        if confirmation != plan.challenge:
            raise PublishError("confirmation_required")
        if plan.asset_sha256 is None:
            raise PublishError("invalid_asset")
        if preview_next(repository, config, catalog) != plan:
            raise PublishError("preview_changed")
        credential = credentials.get_valid_credential()
        client = client_factory(credential.access_token)
        identity = client.identify_account()
        if (
            identity.username != "ask.every.day"
            or identity.user_id != credential.user_id
            or identity.account_type.casefold() not in {"business", "media_creator"}
        ):
            raise PublishError("account_binding")
        account_id = _provider_id(identity.user_id)
        quota = client.publishing_quota(account_id)
        if quota.usage >= quota.total:
            raise PublishError("quota_exhausted")
        phase = "staging"
        with stage(
            repository, config, plan.post.id, expected_sha256=plan.asset_sha256
        ) as media:
            if media.post_id != plan.post.id:
                raise PublishError("staged_post_mismatch")
            if preview_next(repository, config, catalog) != plan:
                raise PublishError("preview_changed")
            if dispatch_guard is not None:
                dispatch_guard(None)
            service = PublicationService(repository)
            phase = "begin_attempt"
            attempt_id = service.begin_publish(
                plan.post.id,
                clock(),
                context=PublicationContext(
                    account_id, identity.username, plan.asset_sha256
                ),
            ).id
            checkpoints = CheckpointService(repository)
            phase = "create_intent"
            checkpoints.create_intent(attempt_id, clock())
            if dispatch_guard is not None:
                dispatch_guard(attempt_id)
            phase = "create_container"
            container_id = _provider_id(
                client.create_image_container(
                    account_id, media.public_url, caption=plan.caption
                ).id
            )
            phase = "record_container"
            checkpoints.container_known(attempt_id, container_id, clock())
            phase = "container_readiness"
            for index in range(5):
                status = client.container_status(container_id)
                if status is ContainerStatus.FINISHED:
                    break
                if status in (ContainerStatus.ERROR, ContainerStatus.EXPIRED):
                    service.record_failure(
                        attempt_id,
                        f"container_{status.value.lower()}",
                        checkpoint_at=clock(),
                    )
                    raise PublishError("container_terminal")
                if status is not ContainerStatus.IN_PROGRESS:
                    raise PublishError("container_unresolved")
                if index == 4:
                    raise PublishError("container_timeout")
                sleep(60)
            if dispatch_guard is not None:
                dispatch_guard(attempt_id)
            phase = "publish_intent"
            checkpoints.publish_intent(attempt_id, clock())
            if dispatch_guard is not None:
                dispatch_guard(attempt_id)
            phase = "publish_container"
            media_id = _provider_id(
                client.publish_container(account_id, container_id).id
            )
            phase = "record_success"
            service.record_success(
                attempt_id,
                clock(),
                external_id=media_id,
                validate_result=lambda: validate_palette_success(
                    repository, config, plan.post.id, plan.asset_sha256
                ),
            )
            if service.state(plan.post.id) is not PublicationState.PUBLISHED:
                raise PublishError("local_result_unverified")
            phase = "cleanup"
    except BaseException as error:
        reason = (
            error.reason
            if isinstance(error, PublishError)
            else "interrupted"
            if isinstance(error, KeyboardInterrupt)
            else f"{phase}_failed"
        )
        failure = PublishError(
            reason, attempt_id=attempt_id, container_id=container_id, media_id=media_id
        )
    if failure is not None:
        _LOG.error(
            "publish_next_failed",
            extra={
                "event": "publish_next_failed",
                "post_id": plan.post.id,
                "publication_id": attempt_id,
                "palette_colour": plan.background_color,
                "palette_sequence": plan.palette_sequence,
                "queue_position": plan.entry.position,
                "operation": failure.reason,
                "error_type": "PublishError",
            },
        )
        raise failure
    assert attempt_id is not None and container_id is not None and media_id is not None
    _LOG.info(
        "publish_next_completed",
        extra={
            "event": "publish_next_completed",
            "post_id": plan.post.id,
            "publication_id": attempt_id,
            "palette_colour": plan.background_color,
            "palette_sequence": plan.palette_sequence,
            "queue_position": plan.entry.position,
        },
    )
    return PublishResult(plan.post.id, attempt_id, container_id, media_id)
