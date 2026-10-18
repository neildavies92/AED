"""PNG boundary checks without committing binary fixtures."""

import logging
import struct
import zlib
from pathlib import Path

import pytest

from ask_every_day.classic_prototype import SAMPLE_LINES, SAMPLE_POST
from ask_every_day.domain import RendererConfig
from ask_every_day.png_export import PNGExportError, export_png
from ask_every_day.renderer import render_svg

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'


def png_header(data: bytes) -> tuple[int, int, int, int]:
    """Validate PNG signature, chunk CRCs and compressed data; return IHDR values."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    compressed = bytearray()
    kinds: list[bytes] = []
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        crc = int.from_bytes(data[offset + 8 + length : offset + 12 + length])
        assert zlib.crc32(kind + payload) == crc
        kinds.append(kind)
        if kind == b"IDAT":
            compressed.extend(payload)
        offset += 12 + length
    assert kinds[0] == b"IHDR" and kinds[-1] == b"IEND"
    assert offset == len(data)
    assert zlib.decompress(compressed)
    width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
    return width, height, depth, colour


def test_classic_integration_is_opaque_and_deterministic() -> None:
    config = RendererConfig()
    svg = render_svg(SAMPLE_POST, config, lines=SAMPLE_LINES)
    first = export_png(svg, config)
    assert first == export_png(svg, config)
    # PNG colour type 2 is RGB with no alpha channel.
    assert png_header(first) == (1080, 1350, 8, 2)


def test_config_dimensions_and_transparency_are_preserved() -> None:
    assert png_header(export_png(SVG, RendererConfig(width=24, height=30))) == (
        24,
        30,
        8,
        6,
    )


@pytest.mark.parametrize("svg", ["", "<svg", "<html/>", "<svg/>"])
def test_malformed_or_non_svg_fails(svg: str) -> None:
    with pytest.raises(PNGExportError):
        export_png(svg, RendererConfig())


def test_entity_declarations_are_rejected() -> None:
    with pytest.raises(PNGExportError, match="entities"):
        export_png('<!DOCTYPE svg [<!ENTITY text "hello">]>' + SVG, RendererConfig())


def test_conversion_failure_is_chained_without_payload_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import cairosvg

    def fail(**kwargs: object) -> bytes:
        raise RuntimeError("private SVG payload")

    monkeypatch.setattr(cairosvg, "svg2png", fail)
    with caplog.at_level(logging.WARNING), pytest.raises(PNGExportError) as error:
        export_png(SVG, RendererConfig())
    assert isinstance(error.value.__cause__, RuntimeError)
    assert "private SVG payload" not in caplog.text
    assert caplog.records[-1].__dict__["event"] == "png_export_failed"


def test_no_application_file_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert export_png(SVG, RendererConfig())
    assert list(tmp_path.iterdir()) == []


def test_external_resources_are_not_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    import cairosvg.url

    def fail(*args: object, **kwargs: object) -> bytes:
        pytest.fail("Exporter attempted to fetch an external resource")

    monkeypatch.setattr(cairosvg.url, "urlopen", fail)
    image = SVG.replace(
        "/>",
        '><image href="https://example.invalid/private.png" width="10" '
        'height="10"/></svg>',
    )
    assert export_png(image, RendererConfig())


def test_structured_success_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="ask_every_day.png_export"):
        export_png(SVG, RendererConfig())
    record = caplog.records[-1]
    assert record.__dict__["event"] == "png_exported"
    assert record.__dict__["format"] == "png"
    assert record.__dict__["width"] == 1080
    assert record.__dict__["height"] == 1350
    assert "<svg" not in caplog.text


@pytest.mark.parametrize("size", [(1080, 1350), (540, 675)])
def test_centered_brand_raster_matches_unsplit_text_geometry(size):
    from io import BytesIO
    from xml.etree import ElementTree

    import cairosvg
    from PIL import Image, ImageChops

    config = RendererConfig(width=size[0], height=size[1])
    svg = render_svg(SAMPLE_POST, config)
    root = ElementTree.fromstring(svg)
    brand = root.find(".//{http://www.w3.org/2000/svg}text[@id='classic-brand']")
    assert brand is not None
    for span in brand:
        span.set("fill", "#343B35")
    actual = export_png(ElementTree.tostring(root, encoding="unicode"), config)
    text = "".join(brand.itertext())
    brand[:] = []
    brand.text = text
    # A single-colour, unsplit control uses CairoSVG's working native centering.
    expected = cairosvg.svg2png(
        bytestring=ElementTree.tostring(root),
        output_width=size[0],
        output_height=size[1],
    )
    assert isinstance(expected, bytes)
    with Image.open(BytesIO(actual)) as a, Image.open(BytesIO(expected)) as b:
        assert ImageChops.difference(a, b).getbbox() is None
