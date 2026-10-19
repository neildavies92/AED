"""Strict local CSV boundary for versioned caption policy; no database or network."""

import csv
from collections import Counter
from collections.abc import Collection, Sequence
from pathlib import Path

from ask_every_day.copy_policy import (
    Caption,
    CaptionProfile,
    compatible,
    validate_captions,
)
from ask_every_day.domain import Post

CAPTION_COLUMNS = (
    "id",
    "caption",
    "active",
    "category",
    "subcategory",
    "cta_role",
    "prompt_forms",
    "response_modes",
    "time_focus",
    "public_reply_required",
    "notes",
)
PROFILE_COLUMNS = (
    "post_id",
    "prompt_form",
    "response_mode",
    "time_focus",
    "public_reply_ok",
)


def _rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        if next(reader, []) != list(columns):
            raise ValueError(f"Unexpected snapshot columns: {path.name}")
        result = []
        for row in reader:
            if len(row) != len(columns):
                raise ValueError(
                    f"Malformed snapshot row: {path.name}:{reader.line_num}"
                )
            result.append(dict(zip(columns, row, strict=True)))
        return result


def _boolean(value: str) -> bool:
    if value not in {"TRUE", "FALSE"}:
        raise ValueError("Snapshot flags must use TRUE or FALSE")
    return value == "TRUE"


def load_captions(path: Path, taxonomy: Path) -> tuple[Caption, ...]:
    pairs = [
        (r["category"], r["subcategory"])
        for r in _rows(taxonomy, ("category", "subcategory"))
    ]
    if len(set(pairs)) != len(pairs) or not pairs:
        raise ValueError("Taxonomy must contain unique pairs")
    result = []
    for row in _rows(path, CAPTION_COLUMNS):
        item = Caption(
            row["id"],
            row["caption"],
            _boolean(row["active"]),
            row["category"],
            row["subcategory"],
            row["cta_role"],
            tuple(row["prompt_forms"].split("|")),
            tuple(row["response_modes"].split("|")),
            tuple(row["time_focus"].split("|")),
            _boolean(row["public_reply_required"]),
        )
        if (item.category, item.subcategory) not in [*pairs, ("*", "*")]:
            raise ValueError(f"Unknown caption taxonomy: {item.id}")
        result.append(item)
    validate_captions(result)
    return tuple(sorted(result, key=lambda item: item.id))


def load_profiles(path: Path, post_ids: Collection[str]) -> dict[str, CaptionProfile]:
    result: dict[str, CaptionProfile] = {}
    for row in _rows(path, PROFILE_COLUMNS):
        profile = CaptionProfile(
            row["post_id"],
            row["prompt_form"],
            row["response_mode"],
            row["time_focus"],
            _boolean(row["public_reply_ok"]),
        )
        if profile.post_id in result:
            raise ValueError("Duplicate caption profile Post ID")
        result[profile.post_id] = profile
    if set(result) != set(post_ids) or len(post_ids) != len(result):
        raise ValueError(
            "Caption profiles must match Post IDs exactly (missing/extra IDs)"
        )
    return dict(sorted(result.items()))


def profile_counts(profiles: Collection[CaptionProfile]) -> dict[str, dict[str, int]]:
    return {
        field: dict(sorted(Counter(str(getattr(p, field)) for p in profiles).items()))
        for field in PROFILE_COLUMNS[1:]
    }


def validate_reachability(
    posts: Sequence[Post],
    captions: Sequence[Caption],
    profiles: dict[str, CaptionProfile],
) -> None:
    """Every active caption must serve at least one real Post, ignoring overrides."""
    for caption in captions:
        if caption.active and not any(
            (caption.category, caption.subcategory)
            in ((post.category, post.subcategory), ("*", "*"))
            and compatible(caption, profiles[post.id])
            for post in posts
        ):
            raise ValueError(f"Unreachable active caption: {caption.id}")
