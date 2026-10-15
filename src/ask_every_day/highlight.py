"""Exact editorial highlight spans; no normalization, layout changes or I/O."""

from ask_every_day.layout import LayoutLine


class HighlightError(ValueError):
    """Highlight text is missing, blank or ambiguous in the original prompt."""


def highlight_span(prompt: str, highlight: str | None) -> tuple[int, int] | None:
    """Locate one exact occurrence, rejecting even overlapping repeat matches."""
    if highlight is None:
        return None
    if not isinstance(highlight, str):
        raise TypeError("highlight must be a string or None")
    if not highlight.strip():
        raise HighlightError("highlight must not be blank")
    start = prompt.find(highlight)
    if start < 0:
        raise HighlightError("highlight must occur exactly within prompt")
    if prompt.find(highlight, start + 1) >= 0:
        raise HighlightError("highlight must identify one unambiguous occurrence")
    return start, start + len(highlight)


def segment_line(
    line: LayoutLine, span: tuple[int, int] | None
) -> tuple[str, str, str]:
    """Intersect an original source span with a line's unchanged source slice."""
    if span is None:
        return line.text, "", ""
    start = max(line.start, span[0])
    end = min(line.end, span[1])
    if start >= end:
        return line.text, "", ""
    left, right = start - line.start, end - line.start
    return line.text[:left], line.text[left:right], line.text[right:]
