"""Fixed Classic geometry and the approved library's automatic wrapping contract."""

import csv
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

from ask_every_day.classic_prototype import SAMPLE_POST
from ask_every_day.domain import Post, RendererConfig
from ask_every_day.layout import MAX_LINES, TEXT_WIDTH, LayoutError, layout_prompt
from ask_every_day.renderer import RenderError, render_svg


@pytest.mark.parametrize(
    "prompt",
    [
        "Why?",
        SAMPLE_POST.prompt,
        '  Which  words, "quoted" or (bracketed), would you choose?  ',
        "Would you say yes -- or no?",
    ],
)
def test_exact_offsets_punctuation_and_determinism(prompt: str) -> None:
    lines = layout_prompt(prompt)
    assert layout_prompt(prompt) == lines
    assert "".join(line.text for line in lines) == prompt
    assert lines[0].start == 0
    assert lines[-1].end == len(prompt)
    for previous, current in zip(lines, lines[1:]):
        assert previous.end == current.start
        assert previous.text.endswith(" ")
    for line in lines:
        assert line.text == prompt[line.start : line.end]
        assert line.width <= TEXT_WIDTH


def test_short_prompt_one_centered_line() -> None:
    lines = layout_prompt("Why?")
    assert len(lines) == 1
    assert lines[0].baseline == 688


def test_approved_corpus_fits_and_renders_without_manual_layout() -> None:
    source = Path(__file__).parents[1] / "data/prompts.csv"
    with source.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 400
    for row in rows:
        assert row["status"] == "Approved"
        post = Post(
            row["id"],
            row["prompt"],
            row["category"],
            row["subcategory"],
            row["template"],
            row["highlight_text"] or None,
        )
        lines = layout_prompt(post.prompt)
        assert 1 <= len(lines) <= MAX_LINES
        assert all(line.width <= TEXT_WIDTH for line in lines)
        assert lines[0].baseline - 76 >= 350
        assert lines[-1].baseline + 24 <= 970
        root = ElementTree.fromstring(render_svg(post, RendererConfig()))
        prompt_lines = [
            text
            for text in root.findall(".//{http://www.w3.org/2000/svg}text")
            if text.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
        ]
        assert "".join("".join(line.itertext()) for line in prompt_lines) == post.prompt
        assert len(prompt_lines) == len(lines)


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "   ",
        "W" * 100,
        "word " * 200,
        "line\nbreak",
        "tab\tgap",
        "cafe\u0301",
        "hello 😀",
    ],
)
def test_unsupported_content_fails_clearly(prompt: str) -> None:
    with pytest.raises(LayoutError):
        layout_prompt(prompt)


def test_renderer_wraps_layout_failure_with_cause() -> None:
    with pytest.raises(RenderError) as caught:
        render_svg(
            replace(SAMPLE_POST, prompt="W" * 100, highlight_text=None),
            RendererConfig(),
        )
    assert isinstance(caught.value.__cause__, LayoutError)


def test_standalone_punctuation_stays_with_preceding_word() -> None:
    lines = layout_prompt("Would you say yes -- or no ?")
    assert all(any(character.isalnum() for character in line.text) for line in lines)
    assert "yes --" in "\n".join(line.text for line in lines)
    assert "no ?" in lines[-1].text
