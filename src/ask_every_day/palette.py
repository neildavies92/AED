"""Validated brand colours and a deterministic, success-driven shuffled bag."""

import hashlib
import json
import re
from dataclasses import dataclass
from itertools import permutations

BACKGROUNDS = ("#E2EFF9", "#EDF4FB", "#D3E6F5")
TEXT = "#1C3651"
ACCENT = "#2F608C"


def colour(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Colour must be a hex string")
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", value) is None:
        raise ValueError("Colour must use #RRGGBB")
    return value.upper()


def contrast(first: str, second: str) -> float:
    def luminance(value: str) -> float:
        channels = [int(value[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [
            c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            for c in channels
        ]
        return sum(c * w for c, w in zip(linear, (0.2126, 0.7152, 0.0722)))

    light, dark = sorted(
        (luminance(colour(first)), luminance(colour(second))), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class PaletteConfig:
    backgrounds: tuple[str, ...] = BACKGROUNDS
    text_color: str = TEXT
    accent_color: str = ACCENT
    seed: int = 42

    def __post_init__(self) -> None:
        if not isinstance(self.backgrounds, tuple):
            raise TypeError("Palette backgrounds must be a tuple")
        if (
            len(self.backgrounds) != 3
            or len({colour(c) for c in self.backgrounds}) != 3
        ):
            raise ValueError("Palette requires exactly three distinct colours")
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise ValueError("Palette seed must be an integer from 0 through 2**63-1")
        for ink in (self.text_color, self.accent_color):
            if any(contrast(ink, bg) < 4.5 for bg in self.backgrounds):
                raise ValueError(
                    "Palette text/background contrast must be at least 4.5:1"
                )

    @property
    def key(self) -> str:
        return digest(
            {"backgrounds": [colour(c) for c in self.backgrounds], "seed": self.seed}
        )


@dataclass(frozen=True)
class Assignment:
    sequence: int
    bag_number: int
    slot: int
    bag: tuple[str, ...]
    palette_key: str

    def __post_init__(self) -> None:
        if any(type(n) is not int for n in (self.sequence, self.bag_number, self.slot)):
            raise ValueError("Invalid palette sequence")
        if self.sequence < 1 or self.bag_number < 0 or not 0 <= self.slot < 3:
            raise ValueError("Invalid palette sequence")
        if (
            not isinstance(self.bag, tuple)
            or len(self.bag) != 3
            or len(set(self.bag)) != 3
        ):
            raise ValueError("Invalid palette bag")
        if any(colour(c) != c for c in self.bag):
            raise ValueError("Persisted colours must be canonical hex")
        if re.fullmatch(r"[0-9a-f]{64}", self.palette_key) is None:
            raise ValueError("Invalid palette key")

    @property
    def background(self) -> str:
        return self.bag[self.slot]


def next_assignment(
    config: PaletteConfig,
    previous: Assignment | None,
    *,
    previous_background: str | None = None,
) -> Assignment:
    """Advance only from the last successful assignment, never from a Post ID."""
    sequence = previous.sequence + 1 if previous else 1
    if previous and previous.slot < 2:
        if set(previous.bag) != {colour(c) for c in config.backgrounds}:
            raise ValueError("palette_change_requires_bag_boundary")
        return Assignment(
            sequence,
            previous.bag_number,
            previous.slot + 1,
            previous.bag,
            previous.palette_key,
        )
    boundary = previous.background if previous else previous_background
    number = previous.bag_number + 1 if previous else 0
    candidates = [
        bag
        for bag in permutations(colour(c) for c in config.backgrounds)
        if bag[0] != boundary and (previous is None or bag != previous.bag)
    ]
    bag = min(
        candidates,
        key=lambda candidate: digest(["aed-palette-v1", config.key, number, candidate]),
    )
    return Assignment(sequence, number, 0, bag, config.key)
