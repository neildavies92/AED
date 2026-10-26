"""Immutable domain values, independent of persistence and external services.

Wrong runtime types raise TypeError; invalid values raise ValueError. Inputs are
never normalized. Cross-record rules and workflow transitions belong elsewhere.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from ask_every_day.palette import ACCENT, BACKGROUNDS, TEXT, PaletteConfig, colour


def _text(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty or whitespace")


def _post_id(value: str) -> None:
    _text(value, "post ID")
    if re.fullmatch(r"AED-[0-9]{4}", value) is None or value == "AED-0000":
        raise ValueError("post ID must use AED-0001 through AED-9999")


def _positive_int(value: int, field: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    if value < 1:
        raise ValueError(f"{field} must be positive")


def _aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True)
class Post:
    """Editorial content; an optional highlight is an exact, case-sensitive span."""

    id: str
    prompt: str
    category: str
    subcategory: str
    template: str
    highlight_text: str | None = None
    caption_override: str | None = None
    hashtag_set_id: str | None = None

    def __post_init__(self) -> None:
        _post_id(self.id)
        for field in ("prompt", "category", "subcategory", "template"):
            _text(getattr(self, field), field)
        if self.highlight_text is not None:
            _text(self.highlight_text, "highlight_text")
            if self.highlight_text not in self.prompt:
                raise ValueError("highlight_text must occur exactly within prompt")

        if self.caption_override is not None:
            _text(self.caption_override, "caption_override")
        if self.hashtag_set_id is not None:
            _text(self.hashtag_set_id, "hashtag_set_id")
            if (
                re.fullmatch(r"TAG-[0-9]{3}", self.hashtag_set_id) is None
                or self.hashtag_set_id == "TAG-000"
            ):
                raise ValueError("hashtag_set_id must use TAG-001 through TAG-999")


class PublicationStatus(StrEnum):
    """Attempt snapshots only; transition rules are deferred to AED-020."""

    PUBLISHING = "Publishing"
    PUBLISHED = "Published"
    FAILED = "Failed"


@dataclass(frozen=True)
class Publication:
    """One attempt, with optional provider-neutral result details.

    Repeated attempts may reference the same post. Identity and persistence of
    attempts are deferred to the schema ticket; timestamps are supplied by callers.
    """

    post_id: str
    status: PublicationStatus
    attempted_at: datetime
    published_at: datetime | None = None
    external_id: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        _post_id(self.post_id)
        if not isinstance(self.status, PublicationStatus):
            raise TypeError("status must be a PublicationStatus")
        _aware(self.attempted_at, "attempted_at")
        if self.published_at is not None:
            _aware(self.published_at, "published_at")
            if self.published_at.astimezone(UTC) < self.attempted_at.astimezone(UTC):
                raise ValueError("published_at must not precede attempted_at")
        for field in ("external_id", "error"):
            value = getattr(self, field)
            if value is not None:
                _text(value, field)
        if self.status is PublicationStatus.PUBLISHED:
            if self.published_at is None:
                raise ValueError("published_at is required for Published")
            if self.error is not None:
                raise ValueError("Published must not contain an error")
        elif self.published_at is not None:
            raise ValueError("published_at is only valid for Published")
        if self.error is not None and self.status is not PublicationStatus.FAILED:
            raise ValueError("error is only valid for Failed")


@dataclass(frozen=True)
class QueueEntry:
    """A post at a one-based position; queue-wide uniqueness is a later concern."""

    post_id: str
    position: int

    def __post_init__(self) -> None:
        _post_id(self.post_id)
        _positive_int(self.position, "position")


@dataclass(frozen=True)
class RendererConfig:
    """Rendering values only; no template lookup or image generation."""

    template: str = "Classic"
    width: int = 1080
    height: int = 1350
    background_color: str = BACKGROUNDS[0]
    text_color: str = TEXT
    accent_color: str = ACCENT

    def __post_init__(self) -> None:
        _text(self.template, "template")
        _positive_int(self.width, "width")
        _positive_int(self.height, "height")
        for value in (self.background_color, self.text_color, self.accent_color):
            colour(value)


@dataclass(frozen=True)
class AppConfig:
    """Explicit local settings; paths are neither resolved nor accessed.

    Path defaults, account loading and all configuration I/O belong to AED-003.
    Scheduling/timezone configuration is deferred until scheduling is implemented.
    """

    database_path: Path
    assets_path: Path
    account_handle: str
    renderer: RendererConfig = RendererConfig()
    palette: PaletteConfig = PaletteConfig()

    def __post_init__(self) -> None:
        for field in ("database_path", "assets_path"):
            if not isinstance(getattr(self, field), Path):
                raise TypeError(f"{field} must be a Path")
        _text(self.account_handle, "account_handle")
        if not isinstance(self.renderer, RendererConfig):
            raise TypeError("renderer must be a RendererConfig")
        if not isinstance(self.palette, PaletteConfig):
            raise TypeError("palette must be a PaletteConfig")
