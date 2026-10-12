"""Checks for the visual specimen, not a production renderer contract."""

from dataclasses import replace
from xml.etree import ElementTree

import pytest

from ask_every_day.classic_prototype import SAMPLE_LINES, SAMPLE_POST, render_prototype


def test_reference_svg() -> None:
    root = ElementTree.fromstring(render_prototype(SAMPLE_POST, SAMPLE_LINES))
    assert root.attrib["width"] == "1080"
    assert root.attrib["height"] == "1350"
    assert root.attrib["viewBox"] == "0 0 1080 1350"
    assert SAMPLE_POST.prompt in "".join(root.itertext())
    assert "@ask.every.day" in "".join(root.itertext())
    highlights = root.findall(".//{http://www.w3.org/2000/svg}tspan")
    assert " ".join(span.text for span in highlights if span.text) == (
        SAMPLE_POST.highlight_text
    )


def test_different_content_is_xml_escaped() -> None:
    post = replace(
        SAMPLE_POST, prompt='What does "enough" & <less> mean?', highlight_text="<less>"
    )
    root = ElementTree.fromstring(
        render_prototype(post, (('What does "enough" & ', "<less>", " mean?"),))
    )
    assert post.prompt in "".join(root.itertext())


def test_rejects_mismatched_manual_layout() -> None:
    with pytest.raises(ValueError, match="exact prompt"):
        render_prototype(SAMPLE_POST, (("Different words", "", ""),))
    with pytest.raises(ValueError, match="highlight_text"):
        render_prototype(SAMPLE_POST, ((SAMPLE_POST.prompt, "", ""),))
