"""Explicit daily policy and private, durable unattended authorization."""

import hashlib
import json
import os
import stat
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ask_every_day.content_import import load_snapshot
from ask_every_day.domain import AppConfig, Post, PublicationStatus, QueueEntry, _aware
from ask_every_day.png_export import export_png
from ask_every_day.publication_assets import _object, queued_post
from ask_every_day.publish_next import CopyCatalog
from ask_every_day.renderer import render_svg
from ask_every_day.repository import PublicationRecord, Repository

LONDON = ZoneInfo("Europe/London")
_LIMIT = 1_048_576


class ScheduleError(ValueError):
    """Fixed, content-free diagnostic for policy or local state failures."""


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, ensure_ascii=True).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ScheduleConfig:
    hour: int = 9
    minute: int = 0
    window_minutes: int = 30
    authorization_days: int = 30
    timezone: str = "Europe/London"

    def __post_init__(self) -> None:
        if (
            type(self.hour) is not int
            or not 0 <= self.hour < 24
            or type(self.minute) is not int
            or not 0 <= self.minute < 60
            or type(self.window_minutes) is not int
            or not 1 <= self.window_minutes <= 120
            or self.hour * 60 + self.minute + self.window_minutes > 1440
            or type(self.authorization_days) is not int
            or not 1 <= self.authorization_days <= 366
            or self.timezone != "Europe/London"
        ):
            raise ScheduleError("schedule_configuration")

    def in_window(self, now: datetime) -> bool:
        _aware(now, "now")
        local = now.astimezone(LONDON)
        minute = local.hour * 60 + local.minute
        return (
            self.hour * 60 + self.minute
            <= minute
            < (self.hour * 60 + self.minute + self.window_minutes)
        )


def load_schedule(path: Path) -> ScheduleConfig:
    result = None
    try:
        with path.open("rb") as stream:
            payload = stream.read(8193)
        if len(payload) > 8192:
            raise ValueError
        result = ScheduleConfig(**tomllib.loads(payload.decode()))
    except Exception:
        pass
    if result is None:
        raise ScheduleError("schedule_configuration")
    return result


@dataclass(frozen=True)
class ApprovedInputs:
    posts: Sequence[Post]
    catalog: CopyCatalog
    source_key: str


def load_inputs(directory: Path) -> ApprovedInputs:
    names = (
        "prompts.csv",
        "taxonomy.csv",
        "hashtags.csv",
        "captions.csv",
        "caption_profiles.csv",
    )
    sources = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in names
    }
    return ApprovedInputs(
        load_snapshot(*(directory / name for name in names[:3])),
        CopyCatalog.load(directory),
        digest(sources),
    )


def state_path(config: AppConfig) -> Path:
    database = config.database_path.resolve(strict=True)
    return database.with_name(database.name + ".schedule.json")


def runtime_key() -> str:
    from ask_every_day.font_review import inventory

    fonts = inventory() if sys.platform == "linux" else None
    package = Path(__file__).parent
    return digest(
        {
            **{"resolved_fonts": digest(fonts)},
            **{
                str(path.relative_to(package)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(package.rglob("*"))
                if path.is_file() and path.suffix in {".py", ".jinja", ".ttf", ".otf"}
            },
        }
    )


def rendering_key(config: AppConfig) -> str:
    specimen = Post(
        "AED-9999",
        "What feels like home when everything changes?",
        "Self-awareness",
        "Identity",
        "Classic",
        "home",
    )
    return hashlib.sha256(
        export_png(render_svg(specimen, config.renderer), config.renderer)
    ).hexdigest()


def policy_key(
    repository: Repository,
    config: AppConfig,
    inputs: ApprovedInputs,
    schedule: ScheduleConfig,
) -> str:
    posts = repository.list_posts()
    if config.account_handle != "@ask.every.day" or sorted(
        posts, key=lambda p: p.id
    ) != sorted(inputs.posts, key=lambda p: p.id):
        raise ScheduleError("approved_snapshot_drift")
    return digest(
        {
            "config": asdict(config),
            "database": str(config.database_path.resolve(strict=True)),
            "assets": str(config.assets_path.resolve()),
            "posts": [asdict(p) for p in sorted(posts, key=lambda p: p.id)],
            "copy": asdict(inputs.catalog),
            "sources": inputs.source_key,
            "schedule": asdict(schedule),
            "runtime": runtime_key(),
            "rendering": rendering_key(config),
        }
    )


@dataclass(frozen=True)
class Authorization:
    policy_key: str
    queue: tuple[QueueEntry, ...]
    history: tuple[tuple[int, str], ...]
    authorized_at: datetime
    expires_at: datetime
    enabled: bool = True

    def __post_init__(self) -> None:
        _aware(self.authorized_at, "authorized_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.authorized_at or type(self.enabled) is not bool:
            raise ScheduleError("authorization_invalid")
        hashes = [self.policy_key, *(value for _, value in self.history)]
        if any(
            not isinstance(v, str)
            or len(v) != 64
            or any(c not in "0123456789abcdef" for c in v)
            for v in hashes
        ):
            raise ScheduleError("authorization_invalid")
        if any(type(number) is not int or number < 1 for number, _ in self.history):
            raise ScheduleError("authorization_invalid")
        if len({n for n, _ in self.history}) != len(self.history) or len(
            {e.post_id for e in self.queue}
        ) != len(self.queue):
            raise ScheduleError("authorization_invalid")
        if list(self.queue) != sorted(self.queue, key=lambda e: e.position) or len(
            {e.position for e in self.queue}
        ) != len(self.queue):
            raise ScheduleError("authorization_invalid")

    @property
    def challenge(self) -> str:
        return "AUTHORIZE DAILY @ask.every.day " + digest(asdict(self))[:16]


def authorization_preview(
    repository: Repository,
    config: AppConfig,
    inputs: ApprovedInputs,
    schedule: ScheduleConfig,
    now: datetime,
) -> Authorization:
    with repository.transaction():
        if queued_post(repository) is None:
            raise ScheduleError("queue_empty")
        return Authorization(
            policy_key(repository, config, inputs, schedule),
            tuple(repository.list_queue()),
            tuple((r.id, digest(asdict(r))) for r in repository.list_publications()),
            now,
            now + timedelta(days=schedule.authorization_days),
        )


def read_authorization(config: AppConfig) -> Authorization | None:
    result = None
    try:
        try:
            descriptor = os.open(
                state_path(config), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise ValueError
            payload = stream.read(_LIMIT + 1)
        if len(payload) > _LIMIT:
            raise ValueError
        data = json.loads(payload, object_pairs_hook=_object)
        version = data.pop("version")
        if type(version) is not int or version != 1:
            raise ValueError
        # Exactly one currently supported format; reject extras and malformed fields.
        if set(data) != {
            "policy_key",
            "queue",
            "history",
            "authorized_at",
            "expires_at",
            "enabled",
        }:
            raise ValueError
        result = Authorization(
            data["policy_key"],
            tuple(QueueEntry(**entry) for entry in data["queue"]),
            tuple((row[0], row[1]) for row in data["history"] if len(row) == 2),
            datetime.fromisoformat(data["authorized_at"]),
            datetime.fromisoformat(data["expires_at"]),
            data["enabled"],
        )
        if len(result.history) != len(data["history"]):
            raise ValueError
    except Exception:
        result = None
    if result is None:
        raise ScheduleError("authorization_invalid")
    return result


def save_authorization(config: AppConfig, authorization: Authorization) -> None:
    """Caller holds publication_lock; fsync/replace, never partial or public state."""
    path = state_path(config)
    if path.is_symlink():
        raise ScheduleError("authorization_invalid")
    payload = json.dumps(
        {"version": 1, **asdict(authorization)}, default=lambda v: v.isoformat()
    ).encode()
    if len(payload) > _LIMIT:
        raise ScheduleError("authorization_full")
    descriptor, temporary = tempfile.mkstemp(prefix=".aed-schedule-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_authorization(
    repository: Repository,
    config: AppConfig,
    inputs: ApprovedInputs,
    schedule: ScheduleConfig,
    authorization: Authorization,
    now: datetime,
    *,
    own_attempt: int | None = None,
) -> None:
    _aware(now, "now")
    if (
        not authorization.enabled
        or not authorization.authorized_at <= now < authorization.expires_at
    ):
        raise ScheduleError("authorization_inactive")
    with repository.transaction():
        if policy_key(repository, config, inputs, schedule) != authorization.policy_key:
            raise ScheduleError("policy_reapproval_required")
        history = repository.list_publications()
        by_id = {r.id: r for r in history}
        if any(
            number not in by_id or digest(asdict(by_id[number])) != value
            for number, value in authorization.history
        ):
            raise ScheduleError("publication_history_changed")
        baseline = {number for number, _ in authorization.history}
        new = [r for r in history if r.id not in baseline]
        prefix = authorization.queue[: len(new)]
        if len(prefix) != len(new) or [r.publication.post_id for r in new] != [
            entry.post_id for entry in prefix
        ]:
            raise ScheduleError("approved_queue_changed")
        if any(
            r.publication.status is not PublicationStatus.PUBLISHED
            and not (
                r.id == own_attempt
                and r.publication.status is PublicationStatus.PUBLISHING
            )
            for r in new
        ):
            raise ScheduleError("attempt_requires_reconciliation_or_reapproval")
        if repository.list_queue() != list(authorization.queue[len(new) :]):
            raise ScheduleError("approved_queue_changed")
        for row in history:
            if (
                row.publication.status is PublicationStatus.PUBLISHING
                and row.id != own_attempt
            ):
                raise ScheduleError("unresolved_publication")


def daily_block(
    history: Sequence[PublicationRecord],
    now: datetime,
    *,
    own_attempt: int | None = None,
) -> str | None:
    _aware(now, "now")
    day = now.astimezone(LONDON).date()
    for row in history:
        p = row.publication
        if p.attempted_at > now or (
            p.published_at is not None and p.published_at > now
        ):
            raise ScheduleError("clock_precedes_publication_history")
        if row.id == own_attempt:
            continue
        if p.status is PublicationStatus.PUBLISHING:
            raise ScheduleError("unresolved_publication")
        if (
            p.published_at is not None
            and p.published_at.astimezone(LONDON).date() == day
        ):
            return "already_published_today"
        if p.attempted_at.astimezone(LONDON).date() == day:
            return "already_attempted_today"
    return None
