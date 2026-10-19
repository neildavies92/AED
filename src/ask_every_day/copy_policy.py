"""Pure caption and hashtag selection from caller-supplied editorial values."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from ask_every_day.domain import Post


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


PROMPT_FORMS = frozenset({"question", "statement"})
RESPONSE_MODES = frozenset(
    {
        "reflect",
        "recall",
        "compare",
        "imagine",
        "decide",
        "notice",
        "appreciate",
        "relate",
        "act",
    }
)
TIME_FOCI = frozenset({"past", "present", "future", "timeless", "mixed"})
CTA_ROLES = frozenset({"reflect", "journal", "save_return", "comment_reply", "follow"})
QUIET_ROLES = ("reflect", "journal")


def _choices(values: tuple[str, ...], allowed: frozenset[str]) -> None:
    if not isinstance(values, tuple):
        raise TypeError("Compatibility values must be tuples")
    if values != ("*",) and (
        not values or len(set(values)) != len(values) or not set(values) <= allowed
    ):
        raise ValueError("Invalid compatibility values")


@dataclass(frozen=True)
class CaptionProfile:
    post_id: str
    prompt_form: str
    response_mode: str
    time_focus: str
    public_reply_ok: bool

    def __post_init__(self) -> None:
        _identifier(self.post_id, "AED", 4)
        for value, allowed in (
            (self.prompt_form, PROMPT_FORMS),
            (self.response_mode, RESPONSE_MODES),
            (self.time_focus, TIME_FOCI),
        ):
            if value not in allowed:
                raise ValueError("Invalid caption profile value")
        if type(self.public_reply_ok) is not bool:
            raise TypeError("public_reply_ok must be bool")


@dataclass(frozen=True)
class Caption:
    id: str
    caption: str
    active: bool = True
    category: str = "*"
    subcategory: str = "*"
    cta_role: str = "reflect"
    prompt_forms: tuple[str, ...] = ("*",)
    response_modes: tuple[str, ...] = ("*",)
    time_focus: tuple[str, ...] = ("*",)
    public_reply_required: bool = False

    def __post_init__(self) -> None:
        _identifier(self.id, "CAP", 3)
        for field in ("caption", "category", "subcategory"):
            _text(getattr(self, field), field)
        if (
            type(self.active) is not bool
            or type(self.public_reply_required) is not bool
        ):
            raise TypeError("Caption flags must be bool")
        if (self.category == "*") != (self.subcategory == "*"):
            raise ValueError("General taxonomy must use */*")
        if self.cta_role not in CTA_ROLES:
            raise ValueError("Invalid CTA role")
        if self.public_reply_required != (self.cta_role == "comment_reply"):
            raise ValueError("Only comment_reply must require public reply permission")
        _choices(self.prompt_forms, PROMPT_FORMS)
        _choices(self.response_modes, RESPONSE_MODES)
        _choices(self.time_focus, TIME_FOCI)


def normalized_caption(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def validate_captions(captions: Sequence[Caption]) -> None:
    if len({item.id for item in captions}) != len(captions):
        raise ValueError("Duplicate caption IDs")
    if len({normalized_caption(item.caption) for item in captions}) != len(captions):
        raise ValueError("Duplicate normalized caption text")


def compatible(caption: Caption, profile: CaptionProfile) -> bool:
    """Match editorial declarations only; never inspect or infer prompt meaning."""
    return (not caption.public_reply_required or profile.public_reply_ok) and all(
        values == ("*",) or value in values
        for values, value in (
            (caption.prompt_forms, profile.prompt_form),
            (caption.response_modes, profile.response_mode),
            (caption.time_focus, profile.time_focus),
        )
    )


@dataclass(frozen=True)
class CaptionSelection:
    caption: str
    caption_id: str | None
    cta_role: str | None
    general_fallback: bool = False


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


def caption_lanes(position: int | None) -> tuple[tuple[str, ...], ...]:
    """Explicit preview (None) is quiet; publication cadence uses one-based positions."""
    if position is None:
        return (QUIET_ROLES,)
    if type(position) is not int or position < 1:
        raise ValueError("Caption position must be a positive integer or explicit None")
    if position % 30 == 0:
        return (("follow",), ("save_return",), QUIET_ROLES)
    if position % 9 == 0:
        return (("save_return",), QUIET_ROLES)
    if position % 3 == 0:
        return (("comment_reply",), ("save_return",), QUIET_ROLES)
    return (QUIET_ROLES,)


def select_caption(
    post: Post,
    captions: Sequence[Caption],
    profile: CaptionProfile | None,
    *,
    position: int | None,
) -> CaptionSelection:
    """Exact override, then compatible lane, exact taxonomy, General, safe fallback.

    Within each lane, exact taxonomy precedes General. Only then try the next
    safe lane. ID-sorted candidates use (Post number + position - 2) modulo count;
    explicit quiet preview uses (Post number - 1). No state or I/O is involved.
    """
    if post.caption_override is not None:
        return CaptionSelection(post.caption_override, None, None)
    if profile is None or profile.post_id != post.id:
        raise ValueError(f"Missing or mismatched caption profile for {post.id}")
    lanes = caption_lanes(position)
    validate_captions(captions)
    eligible = [item for item in captions if item.active and compatible(item, profile)]
    offset = int(post.id[4:]) - 1 + (position - 1 if position is not None else 0)
    for lane in lanes:
        for pair in ((post.category, post.subcategory), ("*", "*")):
            candidates = sorted(
                (
                    item
                    for item in eligible
                    if item.cta_role in lane
                    and (item.category, item.subcategory) == pair
                ),
                key=lambda item: item.id,
            )
            if candidates:
                chosen = candidates[offset % len(candidates)]
                return CaptionSelection(
                    chosen.caption, chosen.id, chosen.cta_role, pair == ("*", "*")
                )
    raise ValueError(f"No compatible caption for {post.id}")


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
