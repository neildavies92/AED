"""Deterministic, conservative Classic metrics; no system font access or I/O."""

import re
from dataclasses import dataclass

FONT_SIZE = 76
LINE_HEIGHT = 102
TEXT_WIDTH = 856
MAX_LINES = 6


class LayoutError(ValueError):
    """Content exceeds the supported Classic text geometry or character set."""


@dataclass(frozen=True)
class LayoutLine:
    """An exact source slice, including its trailing inter-word spaces."""

    text: str
    start: int
    end: int
    width: float
    baseline: int


def measure_text(text: str) -> float:
    """Estimate advances in hundredths of an em, conservatively for Classic.

    Negative letter spacing is deliberately not deducted. These are fixed bounds
    calibrated against Georgia, not a promise about arbitrary serif fallbacks.
    """
    units = 0
    for character in text:
        if not " " <= character <= "~":
            raise LayoutError("Classic currently supports printable ASCII text only")
        if character == " ":
            units += 32
        elif character in "ilI.,'!:;|`":
            units += 35
        elif character in "mwMW@%&":
            units += 100
        elif character.isupper():
            units += 80
        else:
            units += 60
    return units * FONT_SIZE / 100


def layout_prompt(prompt: str) -> tuple[LayoutLine, ...]:
    """Greedily wrap at spaces, retaining exact source offsets and punctuation.

    No shrinking, hyphenation, normalization, or automatic highlight styling.
    Supported text uses printable ASCII, fits six lines, and has no overwide word.
    """
    if not isinstance(prompt, str):
        raise TypeError("prompt must be a string")
    if not prompt.strip():
        raise LayoutError("prompt must contain text")
    measure_text(prompt)
    spans: list[tuple[int, int]] = []
    start = 0
    end = 0
    boundaries: list[int] = []
    for match in re.finditer(r"\S+ *", prompt):
        if boundaries and not any(character.isalnum() for character in match.group()):
            boundaries[-1] = match.end()
        else:
            boundaries.append(match.end())
    for candidate_end in boundaries:
        if measure_text(prompt[start:candidate_end]) <= TEXT_WIDTH:
            end = candidate_end
            continue
        if end == start:
            raise LayoutError("A word and its spacing exceed Classic text width")
        spans.append((start, end))
        start = end
        end = candidate_end
        if measure_text(prompt[start:end]) > TEXT_WIDTH:
            raise LayoutError("A word and its spacing exceed Classic text width")
    spans.append((start, end))
    if len(spans) > MAX_LINES:
        raise LayoutError("Prompt exceeds Classic's six-line text area")
    # Center the baseline range on the original three-line specimen's midpoint.
    first_baseline = 688 - (len(spans) - 1) * LINE_HEIGHT // 2
    return tuple(
        LayoutLine(
            prompt[start:end],
            start,
            end,
            measure_text(prompt[start:end]),
            first_baseline + index * LINE_HEIGHT,
        )
        for index, (start, end) in enumerate(spans)
    )
