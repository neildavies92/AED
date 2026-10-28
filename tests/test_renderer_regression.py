"""Stable approved-post contracts across the automatic Classic image pipeline."""

import csv
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image

from ask_every_day.domain import Post, RendererConfig
from ask_every_day.jpeg_export import export_jpeg
from ask_every_day.layout import layout_prompt
from ask_every_day.png_export import export_png
from ask_every_day.renderer import render_svg

SVG = "{http://www.w3.org/2000/svg}"
SPACE = "{http://www.w3.org/XML/1998/namespace}space"
CASES = (
    ("AED-0030", (637, 739), 1),
    ("AED-0001", (586, 688, 790), 2),
    ("AED-0209", (535, 637, 739, 841), 2),
    ("AED-0236", (484, 586, 688, 790, 892), 2),
    ("AED-0238", (484, 586, 688, 790, 892), 2),
    ("AED-0364", (433, 535, 637, 739, 841, 943), 3),
)


@pytest.fixture(scope="module")
def approved_cases() -> dict[str, Post]:
    selected = {case[0] for case in CASES}
    source = Path(__file__).parents[1] / "data/prompts.csv"
    with source.open(encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["id"] in selected]
    assert len(rows) == len(selected)
    assert all(row["status"] == "Approved" for row in rows)
    return {
        row["id"]: Post(
            row["id"],
            row["prompt"],
            row["category"],
            row["subcategory"],
            row["template"],
            row["highlight_text"] or None,
        )
        for row in rows
    }


@pytest.mark.parametrize("post_id,baselines,highlight_count", CASES)
def test_automatic_svg_structure(
    approved_cases: dict[str, Post],
    post_id: str,
    baselines: tuple[int, ...],
    highlight_count: int,
) -> None:
    post = approved_cases[post_id]
    svg = render_svg(post, RendererConfig())
    assert svg.encode("utf-8") == render_svg(post, RendererConfig()).encode("utf-8")
    root = ElementTree.fromstring(svg)
    assert root.tag == SVG + "svg"
    assert {key: root.get(key) for key in ("width", "height", "viewBox")} == {
        "width": "1080",
        "height": "1350",
        "viewBox": "0 0 1080 1350",
    }
    assert root.findtext(SVG + "title") == post.prompt
    background = root.find(SVG + "rect")
    assert background is not None
    assert background.attrib == {"width": "1080", "height": "1350", "fill": "#E2EFF9"}
    groups = root.findall(SVG + "g")
    assert len(groups) == 3
    prompt_group = groups[1]
    assert prompt_group.attrib == {
        "font-family": "Liberation Serif, 'Times New Roman', serif",
        "font-size": "76",
        "fill": "#1C3651",
        "letter-spacing": "-2",
    }
    text_lines = list(prompt_group)
    assert len(text_lines) == len(baselines)
    assert tuple(int(line.attrib["y"]) for line in text_lines) == baselines
    assert (baselines[0] + baselines[-1]) / 2 == 688
    assert baselines[0] - 76 >= 350 and baselines[-1] + 24 <= 970
    assert all(b - a == 102 for a, b in zip(baselines, baselines[1:]))
    highlights: list[str] = []
    for element, line in zip(text_lines, layout_prompt(post.prompt)):
        assert element.tag == SVG + "text"
        assert element.attrib == {
            "x": "112",
            "y": str(line.baseline),
            SPACE: "preserve",
        }
        assert "".join(element.itertext()) == post.prompt[line.start : line.end]
        assert line.width <= 856
        assert len(element) == 1
        span = element[0]
        assert span.tag == SVG + "tspan" and span.attrib == {"fill": "#2F608C"}
        highlights.append(span.text or "")
    assert sum(bool(text) for text in highlights) == highlight_count
    assert "".join(highlights) == post.highlight_text
    assert "".join("".join(line.itertext()) for line in text_lines) == post.prompt
    header = groups[0]
    assert header.attrib == {
        "fill": "#1C3651",
        "font-family": "Lato, Arial, sans-serif",
    }
    brand = header.find(SVG + "text")
    assert brand is not None
    assert "".join(brand.itertext()) == "@ask.every.day"
    assert brand.attrib == {
        "id": "classic-brand",
        "x": "540",
        "y": "147",
        "text-anchor": "middle",
        "font-size": "23",
        "letter-spacing": "1.5",
    }
    assert [(span.text, span.get("fill")) for span in brand] == [
        ("@", "#2F608C"),
        ("ask.every.day", "#1C3651"),
    ]
    underline = header.find(SVG + "path")
    assert underline is not None
    assert underline.attrib == {
        "d": "M512 190 H568",
        "stroke": "#2F608C",
        "stroke-width": "2",
    }
    assert "".join(root.itertext()).count("@ask.every.day") == 1
    assert root.findtext(SVG + "desc") == "Ask Every Day. A question to sit with."
    assert groups[2].attrib == {
        "font-family": "Lato, Arial, sans-serif",
        "fill": "#1C3651",
    }
    footer = groups[2].findall(SVG + "text")
    assert [(text.get("x"), text.get("y"), text.text) for text in footer] == [
        ("112", "1138", "A QUESTION TO SIT WITH."),
    ]
    assert footer[0].attrib == {
        "x": "112",
        "y": "1138",
        "font-size": "20",
        "letter-spacing": "1.5",
    }


@pytest.mark.parametrize("post_id", [case[0] for case in CASES])
def test_automatic_raster_pipeline(
    approved_cases: dict[str, Post],
    post_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    config = RendererConfig()
    png = export_png(render_svg(approved_cases[post_id], config), config)
    jpeg = export_jpeg(png)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
    assert len(jpeg) <= 8_000_000
    for payload, format_name in ((png, "PNG"), (jpeg, "JPEG")):
        with Image.open(BytesIO(payload)) as image:
            image.verify()
        with Image.open(BytesIO(payload)) as image:
            image.load()
            assert image.format == format_name
            assert image.size == (1080, 1350)
            assert image.mode == "RGB" and image.getbands() == ("R", "G", "B")
            assert not image.getexif()
            assert (
                not {"transparency", "exif", "icc_profile", "comment"}
                & image.info.keys()
            )
            assert image.getcolors(maxcolors=1) is None
    assert list(tmp_path.rglob("*")) == []


def test_xml_sensitive_automatic_segments(approved_cases: dict[str, Post]) -> None:
    # Approved editorial text has no angle brackets; this derivative probes escaping.
    post = replace(
        approved_cases["AED-0030"],
        prompt='Why "less" & <more> today?',
        highlight_text='"less" & <more>',
    )
    svg = render_svg(post, RendererConfig())
    assert "&amp;" in svg and "&lt;more&gt;" in svg
    root = ElementTree.fromstring(svg)
    text_lines = [line for line in root.iter(SVG + "text") if line.get(SPACE)]
    assert "".join("".join(line.itertext()) for line in text_lines) == post.prompt
    assert (
        "".join(span.text or "" for line in text_lines for span in line)
        == post.highlight_text
    )
