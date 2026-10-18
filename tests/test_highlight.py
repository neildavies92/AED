"""Source-span styling preserves the established layout and renderer contract."""

import csv
import logging
import struct
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

from ask_every_day.classic_prototype import SAMPLE_POST
from ask_every_day.domain import Post, RendererConfig
from ask_every_day.highlight import HighlightError, highlight_span, segment_line
from ask_every_day.layout import layout_prompt
from ask_every_day.png_export import export_png
from ask_every_day.renderer import RenderError, render_svg

SVG = "{http://www.w3.org/2000/svg}"
SPACE = "{http://www.w3.org/XML/1998/namespace}space"


@pytest.mark.parametrize(
    "prompt,highlight",
    [
        ("Why now?", "Why"),
        ("Why now?", "now"),
        ('Why "now" & <then>?', '"now" & <then>'),
        ("  Why  now?  ", " Why  now? "),
        (SAMPLE_POST.prompt, SAMPLE_POST.highlight_text),
        ("Why now?", None),
    ],
)
def test_exact_segments_and_offsets(prompt: str, highlight: str | None) -> None:
    svg = render_svg(
        replace(SAMPLE_POST, prompt=prompt, highlight_text=highlight), RendererConfig()
    )
    root = ElementTree.fromstring(svg)
    assert root.findtext(SVG + "title") == prompt
    prompt_group = root.findall(SVG + "g")[1]
    assert "".join(
        element.text or "" for element in prompt_group.iter(SVG + "tspan")
    ) == (highlight or "")
    span = highlight_span(prompt, highlight)
    lines = layout_prompt(prompt)
    segments = [segment_line(line, span) for line in lines]
    assert "".join("".join(parts) for parts in segments) == prompt
    assert "".join(parts[1] for parts in segments) == (highlight or "")
    for line, (prefix, selected, suffix) in zip(lines, segments):
        assert prefix + selected + suffix == prompt[line.start : line.end]
        if selected:
            assert span is not None
            assert selected == prompt[max(line.start, span[0]) : min(line.end, span[1])]


def test_line_start_end_and_crossing() -> None:
    lines = layout_prompt(SAMPLE_POST.prompt)
    assert len(lines) >= 3
    for highlight in (lines[1].text, lines[0].text[-4:] + lines[1].text[:5]):
        span = highlight_span(SAMPLE_POST.prompt, highlight)
        segments = [segment_line(line, span) for line in lines]
        assert "".join(parts[1] for parts in segments) == highlight
    assert sum(bool(parts[1]) for parts in segments) == 2


@pytest.mark.parametrize(
    "prompt,highlight",
    [
        ("Again again?", "again again"),
        ("echo echo", "echo"),
        ("banana", "ana"),
        ("A question?", ""),
        ("A question?", " "),
    ],
)
def test_invalid_and_ambiguous_highlights(prompt: str, highlight: str) -> None:
    with pytest.raises(HighlightError):
        highlight_span(prompt, highlight)


def test_domain_rejects_missing_highlight() -> None:
    with pytest.raises(ValueError):
        replace(SAMPLE_POST, highlight_text="missing")


def test_renderer_rejects_ambiguity_and_does_not_log_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(RenderError, match="unambiguous"):
        render_svg(
            replace(SAMPLE_POST, prompt="echo echo", highlight_text="echo"),
            RendererConfig(),
        )
    with caplog.at_level(logging.INFO):
        render_svg(SAMPLE_POST, RendererConfig())
    record = next(record for record in caplog.records if record.msg == "svg_rendered")
    assert record.__dict__["highlight_segment_count"] == 2
    assert SAMPLE_POST.prompt not in str(record.__dict__)
    assert SAMPLE_POST.highlight_text is not None
    assert SAMPLE_POST.highlight_text not in str(record.__dict__)


def test_all_approved_prompts_style_without_geometry_changes() -> None:
    with (Path(__file__).parents[1] / "data/prompts.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 400
    for row in rows:
        post = Post(
            row["id"],
            row["prompt"],
            row["category"],
            row["subcategory"],
            row["template"],
            row["highlight_text"] or None,
        )
        svg = render_svg(post, RendererConfig())
        assert svg == render_svg(post, RendererConfig())
        root = ElementTree.fromstring(svg)
        text_lines = [
            element
            for element in root.iter(SVG + "text")
            if element.get(SPACE) == "preserve"
        ]
        layout = layout_prompt(post.prompt)
        assert len(text_lines) == len(layout)
        for element, line in zip(text_lines, layout):
            assert element.get("x") == "112"
            assert element.get("y") == str(line.baseline)
            assert "".join(element.itertext()) == line.text
            assert line.width <= 856
            assert all(set(span.attrib) == {"fill"} for span in element)
        assert (
            "".join(
                span.text or ""
                for element in text_lines
                for span in element.findall(SVG + "tspan")
            )
            == post.highlight_text
        )


def test_real_highlighted_png_integration() -> None:
    config = RendererConfig()
    svg = render_svg(SAMPLE_POST, config)
    png = export_png(svg, config)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (1080, 1350)
