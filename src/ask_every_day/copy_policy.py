"""Pure caption and hashtag selection from caller-supplied editorial values."""

import re
from collections.abc import Sequence
from dataclasses import dataclass


def _text(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be blank")


def _identifier(value: str, prefix: str, digits: int) -> int:
    _text(value, "ID")
    if re.fullmatch(rf"{prefix}-[0-9]{{{digits}}}", value) is None:
        raise ValueError(f"ID must use {prefix} and {digits} digits")
    number = int(value[len(prefix) + 1 :])
    if number == 0:
        raise ValueError("ID number must be positive")
    return number


def _optional(value: str | None) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TypeError("Override must be a string or None")
    return value if value is not None and value.strip() else None


@dataclass(frozen=True)
class Caption:
    id: str
    caption: str
    active: bool = True

    def __post_init__(self) -> None:
        _identifier(self.id, "CAP", 3)
        _text(self.caption, "caption")
        if type(self.active) is not bool:
            raise TypeError("active must be bool")


@dataclass(frozen=True)
class HashtagSet:
    id: str
    name: str
    hashtags: tuple[str, ...]
    use_for: tuple[str, ...]
    active: bool = True

    def __post_init__(self) -> None:
        _identifier(self.id, "TAG", 3)
        _text(self.name, "name")
        if type(self.active) is not bool:
            raise TypeError("active must be bool")
        for field in (self.hashtags, self.use_for):
            if not isinstance(field, tuple):
                raise TypeError("hashtags and use_for must be tuples")
            if not field:
                raise ValueError("hashtags and use_for must not be empty")
            for item in field:
                _text(item, "editorial item")
            if len({item.casefold() for item in field}) != len(field):
                raise ValueError("Duplicate editorial item")
        if any(re.fullmatch(r"#[A-Za-z0-9_]+", tag) is None for tag in self.hashtags):
            raise ValueError("Hashtags must start with # and contain letters/digits/_")
        if "All categories" in self.use_for and self.use_for != ("All categories",):
            raise ValueError("General fallback must use only All categories")


def select_caption(
    post_id: str, captions: Sequence[Caption], *, override: str | None = None
) -> str:
    """Use exact nonblank override, else cycle active captions in immutable ID order."""
    number = _identifier(post_id, "AED", 4)
    override = _optional(override)
    if override is not None:
        return override
    if len({item.id for item in captions}) != len(captions):
        raise ValueError("Duplicate caption IDs")
    active = sorted(
        (item for item in captions if item.active), key=lambda item: item.id
    )
    if not active:
        raise ValueError("No active captions")
    return active[(number - 1) % len(active)].caption


def select_hashtag_set(
    category: str,
    sets: Sequence[HashtagSet],
    *,
    explicit_set: str | None = None,
    enabled: bool = True,
) -> HashtagSet | None:
    """Resolve exact ID/name, category, then General; disabled returns no set."""
    _text(category, "category")
    if type(enabled) is not bool:
        raise TypeError("enabled must be bool")
    if len({item.id for item in sets}) != len(sets) or len(
        {item.name for item in sets}
    ) != len(sets):
        raise ValueError("Hashtag IDs and names must be unique")
    explicit_set = _optional(explicit_set)
    if explicit_set is not None:
        matches = [item for item in sets if explicit_set in (item.id, item.name)]
        if len(matches) != 1 or not matches[0].active:
            raise ValueError("Explicit hashtag set must identify one active set")
        return matches[0] if enabled else None
    if not enabled:
        return None
    matches = [item for item in sets if item.active and category in item.use_for]
    if not matches:
        matches = [
            item for item in sets if item.active and item.use_for == ("All categories",)
        ]
    if len(matches) > 1:
        raise ValueError("Ambiguous active hashtag mapping")
    return matches[0] if matches else None
