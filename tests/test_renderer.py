"""Production SVG boundary and its intentionally temporary layout seam."""

import logging
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from xml.etree import ElementTree

import pytest
from jinja2 import DictLoader

from ask_every_day import classic_prototype, renderer
from ask_every_day.classic_prototype import SAMPLE_LINES, SAMPLE_POST
from ask_every_day.domain import RendererConfig
from ask_every_day.renderer import RenderError, render_svg


def test_render_content_and_config_dimensions() -> None:
    svg = render_svg(SAMPLE_POST, RendererConfig(width=540, height=675))
    root = ElementTree.fromstring(svg)
    assert root.attrib["width"] == "540"
    assert root.attrib["height"] == "675"
    assert root.attrib["viewBox"] == "0 0 1080 1350"
    assert SAMPLE_POST.prompt in "".join(root.itertext())
    assert "@ask.every.day" in "".join(root.itertext())


def test_deterministic_and_independent_of_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = render_svg(SAMPLE_POST, RendererConfig())
    monkeypatch.chdir(tmp_path)
    assert render_svg(SAMPLE_POST, RendererConfig()) == expected
    assert render_svg(SAMPLE_POST, RendererConfig()).encode() == expected.encode()
    assert list(tmp_path.iterdir()) == []


def test_escapes_editorial_text() -> None:
    prompt = 'What do "more" & <less> mean to you\'re?'
    post = replace(SAMPLE_POST, prompt=prompt, highlight_text=None)
    root = ElementTree.fromstring(render_svg(post, RendererConfig()))
    assert root.findtext("{http://www.w3.org/2000/svg}title") == prompt
    assert "&amp;" in render_svg(post, RendererConfig())


def test_unsupported_and_mismatched_templates() -> None:
    with pytest.raises(RenderError, match="Unsupported template"):
        render_svg(SAMPLE_POST, RendererConfig(template="../../custom"))
    with pytest.raises(RenderError, match="must match"):
        render_svg(replace(SAMPLE_POST, template="Other"), RendererConfig())


@pytest.mark.parametrize(
    "templates",
    [
        {},
        {"classic.svg.jinja": "{{ absent }}"},
        {"classic.svg.jinja": "{% broken %}"},
        {"classic.svg.jinja": "<svg>"},
        {"classic.svg.jinja": "<html/>"},
    ],
)
def test_missing_or_broken_template(
    templates: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renderer, "PackageLoader", lambda *args: DictLoader(templates))
    with pytest.raises(RenderError):
        render_svg(SAMPLE_POST, RendererConfig())


def test_invalid_xml_character_rejected() -> None:
    with pytest.raises(RenderError, match="Classic layout failed"):
        render_svg(
            replace(SAMPLE_POST, prompt="A\x00B", highlight_text=None), RendererConfig()
        )


def test_specimen_calls_production_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    render = Mock(return_value="specimen")
    monkeypatch.setattr(renderer, "render_svg", render)
    assert classic_prototype.render_prototype(SAMPLE_POST, SAMPLE_LINES) == "specimen"
    render.assert_called_once_with(SAMPLE_POST, RendererConfig(), lines=SAMPLE_LINES)


def test_specimen_preserves_approved_svg() -> None:
    reference = Path(__file__).parents[1] / "docs/design/classic-reference.svg"
    assert render_svg(SAMPLE_POST, RendererConfig(), lines=SAMPLE_LINES) == (
        reference.read_text(encoding="utf-8")
    )


def test_structured_log_context_without_prompt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="ask_every_day.renderer"):
        render_svg(SAMPLE_POST, RendererConfig())
    record = caplog.records[-1]
    assert record.__dict__["event"] == "svg_rendered"
    assert record.__dict__["post_id"] == SAMPLE_POST.id
    assert record.__dict__["width"] == 1080
    assert SAMPLE_POST.prompt not in str(record.__dict__)
