"""Durable colour/asset preparation coordinated with SQLite publication history."""

import hashlib
import json
import logging
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from ask_every_day.batch_render import _write_pair
from ask_every_day.domain import (
    AppConfig,
    Post,
    PublicationStatus,
    QueueEntry,
    RendererConfig,
)
from ask_every_day.jpeg_export import export_jpeg
from ask_every_day.media_staging import _jpeg
from ask_every_day.palette import Assignment, colour, next_assignment
from ask_every_day.png_export import export_png
from ask_every_day.publication_service import PublicationState, publication_state
from ask_every_day.renderer import render_svg
from ask_every_day.repository import PublicationRecord, Repository

_LOG = logging.getLogger(__name__)
_LIMIT = 1_048_576


class AssetError(ValueError):
    """Fixed diagnostics; never expose malformed ledger or editorial payloads."""


@dataclass(frozen=True)
class AssetRecord:
    assignment: Assignment
    svg_sha256: str
    jpeg_sha256: str
    png_sha256: str

    def __post_init__(self) -> None:
        for value in (self.svg_sha256, self.jpeg_sha256, self.png_sha256):
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
            ):
                raise ValueError("Invalid asset digest")


@dataclass
class Ledger:
    legacy_publications: list[int] = field(default_factory=list)
    previous_background: str | None = None
    assets: dict[str, AssetRecord] = field(default_factory=dict)


def ledger_path(config: AppConfig) -> Path:
    database = config.database_path.resolve(strict=True)
    return database.with_name(database.name + ".palette.json")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate ledger key")
        result[key] = value
    return result


def read_ledger(config: AppConfig) -> Ledger | None:
    """Bounded, strict metadata read. Missing history is handled explicitly below."""
    failure = False
    try:
        try:
            descriptor = os.open(
                ledger_path(config), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError
            payload = stream.read(_LIMIT + 1)
        if len(payload) > _LIMIT:
            raise ValueError
        data = json.loads(payload, object_pairs_hook=_object)
        if (
            set(data)
            != {"version", "legacy_publications", "previous_background", "assets"}
            or type(data["version"]) is not int
            or data["version"] != 1
        ):
            raise ValueError
        ids = data["legacy_publications"]
        if (
            not isinstance(ids, list)
            or any(type(n) is not int or n < 1 for n in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError
        boundary = data["previous_background"]
        if boundary is not None and colour(boundary) != boundary:
            raise ValueError
        if ids and boundary is None:
            raise ValueError
        if not isinstance(data["assets"], dict):
            raise ValueError
        assets = {}
        for post_id, value in data["assets"].items():
            if re.fullmatch(r"AED-[0-9]{4}", post_id) is None or post_id == "AED-0000":
                raise ValueError
            assignment = value.pop("assignment")
            assignment["bag"] = tuple(assignment["bag"])
            assets[post_id] = AssetRecord(Assignment(**assignment), **value)
        return Ledger(ids, boundary, assets)
    except Exception:
        failure = True
    if failure:
        raise AssetError("palette_ledger_invalid")
    raise AssertionError


def _save_ledger(config: AppConfig, ledger: Ledger) -> None:
    path = ledger_path(config)
    payload = (
        json.dumps({"version": 1, **asdict(ledger)}, sort_keys=True, indent=2).encode()
        + b"\n"
    )
    if len(payload) > _LIMIT:
        raise AssetError("palette_ledger_full")
    descriptor, temporary = tempfile.mkstemp(prefix=".aed-palette-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def queued_post(repository: Repository) -> tuple[Post, QueueEntry] | None:
    """Fail closed on unresolved attempts before choosing or preparing any asset."""
    queued = {entry.post_id for entry in repository.list_queue()}
    for post in repository.list_posts():
        state = publication_state(
            repository.publication_history(post.id), queued=post.id in queued
        )
        if state is PublicationState.PUBLISHING:
            raise AssetError("unresolved_publication")
    entry = repository.next_queue_entry()
    if entry is None:
        return None
    post = repository.get_post(entry.post_id)
    if post is None:
        raise AssetError("unknown_post")
    if (
        publication_state(repository.publication_history(post.id), queued=True)
        is not PublicationState.QUEUED
    ):
        raise AssetError("invalid_state")
    return post, entry


def _published(repository: Repository) -> list[PublicationRecord]:
    return sorted(
        (
            r
            for p in repository.list_posts()
            for r in repository.publication_history(p.id)
            if r.publication.status is PublicationStatus.PUBLISHED
        ),
        key=lambda r: (r.publication.published_at, r.id),
    )


def _history(
    repository: Repository, config: AppConfig, *, previous_background: str | None = None
) -> tuple[Ledger, Assignment | None]:
    published = _published(repository)
    ledger = read_ledger(config)
    if ledger is None:
        if published and previous_background is None:
            raise AssetError("palette_history_missing")
        ledger = Ledger(
            [r.id for r in published],
            colour(previous_background) if previous_background is not None else None,
        )
    elif previous_background is not None:
        raise AssetError("palette_already_initialized")
    if not set(ledger.legacy_publications) <= {r.id for r in published}:
        raise AssetError("palette_history_changed")
    previous = None
    for row in published:
        if row.id in ledger.legacy_publications:
            if previous is not None:
                raise AssetError("palette_history_changed")
            continue
        asset = ledger.assets.get(row.publication.post_id)
        if asset is None:
            raise AssetError("untracked_publication")
        current = asset.assignment
        expected_sequence = previous.sequence + 1 if previous else 1
        boundary = previous.background if previous else ledger.previous_background
        if current.sequence != expected_sequence or current.background == boundary:
            raise AssetError("palette_history_conflict")
        if (
            previous
            and current.palette_key == previous.palette_key
            and previous.slot < 2
        ):
            if (current.bag, current.slot, current.bag_number) != (
                previous.bag,
                previous.slot + 1,
                previous.bag_number,
            ):
                raise AssetError("palette_history_conflict")
        elif current.slot != 0 or current.bag_number != (
            previous.bag_number + 1 if previous else 0
        ):
            raise AssetError("palette_history_conflict")
        previous = current
    return ledger, previous


def themed_renderer(config: AppConfig, background: str) -> RendererConfig:
    return replace(
        config.renderer,
        background_color=colour(background),
        text_color=colour(config.palette.text_color),
        accent_color=colour(config.palette.accent_color),
    )


def _selection(
    repository: Repository, config: AppConfig, *, previous_background: str | None = None
):
    ledger, previous = _history(
        repository, config, previous_background=previous_background
    )
    blocked = False
    selected = None
    try:
        selected = next_assignment(
            config.palette, previous, previous_background=ledger.previous_background
        )
    except ValueError:
        blocked = True
    if blocked:
        raise AssetError("palette_change_requires_bag_boundary")
    assert selected is not None
    return ledger, selected


def asset_preview(
    repository: Repository, config: AppConfig, post: Post, entry: QueueEntry
) -> tuple[Assignment, str | None]:
    """Require both sequence metadata and exact canonical JPEG; never rasterize/write."""
    ledger, assignment = _selection(repository, config)
    svg = render_svg(post, themed_renderer(config, assignment.background))
    asset = ledger.assets.get(post.id)
    digest = None
    if (
        asset
        and asset.assignment == assignment
        and asset.svg_sha256 == hashlib.sha256(svg.encode()).hexdigest()
    ):
        try:
            actual = hashlib.sha256(_jpeg(repository, config, post.id)).hexdigest()
            if actual == asset.jpeg_sha256:
                digest = actual
        except (OSError, ValueError):
            pass
    _LOG.info(
        "palette_selected",
        extra={
            "event": "palette_selected",
            "post_id": post.id,
            "palette_colour": assignment.background,
            "palette_sequence": assignment.sequence,
            "queue_position": entry.position,
        },
    )
    return assignment, digest


def _png_hash(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError
        data = stream.read(8_000_001)
    if len(data) > 8_000_000:
        raise ValueError
    return hashlib.sha256(data).hexdigest()


def prepare_next_asset(
    repository: Repository, config: AppConfig, *, previous_background: str | None = None
) -> AssetRecord | None:
    """Caller holds the publish-next advisory lock. Never replace attempted assets."""
    post_id = None
    failed = False
    reason = "publication_asset_preparation_failed"
    result = None
    context: dict[str, object] = {}
    try:
        with repository.transaction():
            selected = queued_post(repository)
            if selected is None:
                return None
            post, entry = selected
            post_id = post.id
            ledger, assignment = _selection(
                repository, config, previous_background=previous_background
            )
            context = {
                "palette_colour": assignment.background,
                "palette_sequence": assignment.sequence,
                "queue_position": entry.position,
            }
            settings = themed_renderer(config, assignment.background)
            svg = render_svg(post, settings)
            svg_hash = hashlib.sha256(svg.encode()).hexdigest()
            old = ledger.assets.get(post.id)
            if old and old.assignment == assignment and old.svg_sha256 == svg_hash:
                try:
                    # A good canonical JPEG is reused byte-for-byte across restarts.
                    jpeg_hash = hashlib.sha256(
                        _jpeg(repository, config, post.id)
                    ).hexdigest()
                    if (
                        jpeg_hash == old.jpeg_sha256
                        and _png_hash(config.assets_path / f"{post.id}.png")
                        == old.png_sha256
                    ):
                        return old
                except (OSError, ValueError):
                    pass
            png = export_png(svg, settings)
            jpeg = export_jpeg(png)
            result = AssetRecord(
                assignment,
                svg_hash,
                hashlib.sha256(jpeg).hexdigest(),
                hashlib.sha256(png).hexdigest(),
            )
            config.assets_path.mkdir(parents=True, exist_ok=True)
            if config.assets_path.is_symlink():
                raise AssetError("assets_directory_symlink")
            _write_pair(config.assets_path, post.id, png, jpeg)
            ledger.assets[post.id] = result
            _save_ledger(config, ledger)
            _LOG.info(
                "publication_asset_prepared",
                extra={
                    "event": "publication_asset_prepared",
                    "post_id": post.id,
                    "palette_colour": assignment.background,
                    "palette_sequence": assignment.sequence,
                    "queue_position": entry.position,
                },
            )
    except (Exception, KeyboardInterrupt) as error:
        failed = True
        if isinstance(error, KeyboardInterrupt):
            reason = "render_next_cancelled"
        if isinstance(error, AssetError):
            reason = str(error)
    if failed:
        _LOG.error(
            "publication_asset_failed",
            extra={
                "event": "publication_asset_failed",
                "post_id": post_id,
                "error_type": "AssetError",
                "operation": reason,
                **context,
            },
        )
        raise AssetError(reason)
    return result
