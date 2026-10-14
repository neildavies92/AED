"""JPEG representation, source boundary and real renderer integration checks."""

import logging
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from ask_every_day.classic_prototype import SAMPLE_LINES, SAMPLE_POST
from ask_every_day.domain import RendererConfig
from ask_every_day.jpeg_export import JPEGExportError, export_jpeg, validate_jpeg
from ask_every_day.png_export import export_png
from ask_every_day.renderer import render_svg


def png(
    mode: str = "RGB", size: tuple[int, int] = (1080, 1350), **kwargs: object
) -> bytes:
    output = BytesIO()
    with Image.new(mode, size) as image:
        image.save(output, format="PNG", **kwargs)
    return output.getvalue()


def jpeg(
    mode: str = "RGB", size: tuple[int, int] = (1080, 1350), **kwargs: object
) -> bytes:
    output = BytesIO()
    with Image.new(mode, size) as image:
        image.save(output, format="JPEG", **kwargs)
    return output.getvalue()


def test_real_classic_pipeline_and_determinism() -> None:
    config = RendererConfig()
    svg = render_svg(SAMPLE_POST, config, lines=SAMPLE_LINES)
    archive = export_png(svg, config)
    result = export_jpeg(archive)
    assert result == export_jpeg(archive)
    assert archive.startswith(b"\x89PNG")
    assert len(result) < 8_000_000
    validate_jpeg(result)
    with Image.open(BytesIO(result)) as image:
        assert (image.format, image.mode, image.size) == ("JPEG", "RGB", (1080, 1350))
        assert "progressive" not in image.info
        assert "icc_profile" not in image.info and "exif" not in image.info


def test_transparency_flattens_onto_classic_background() -> None:
    with Image.open(BytesIO(export_jpeg(png("RGBA")))) as image:
        # JPEG rounding may alter each channel by one.
        pixel = image.getpixel((0, 0))
        assert isinstance(pixel, tuple)
        assert all(abs(a - b) <= 1 for a, b in zip(pixel, (244, 240, 232)))


@pytest.mark.parametrize("mode", ["L", "P", "LA"])
def test_unsupported_source_modes(mode: str) -> None:
    with pytest.raises(JPEGExportError, match="RGB or RGBA"):
        export_jpeg(png(mode))


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n"])
def test_bad_source(data: bytes) -> None:
    with pytest.raises(JPEGExportError):
        export_jpeg(data)


def test_wrong_source_format_and_dimensions() -> None:
    with pytest.raises(JPEGExportError, match="Source must be PNG"):
        export_jpeg(jpeg())
    with pytest.raises(JPEGExportError, match="1080x1350"):
        export_jpeg(png(size=(10, 10)))


def test_conflicting_color_metadata_is_rejected() -> None:
    with pytest.raises(JPEGExportError, match="profile overrides"):
        export_jpeg(png(icc_profile=b"untrusted profile"))
    for kind, value in [(b"gAMA", (100000).to_bytes(4)), (b"cHRM", bytes(32))]:
        info = PngImagePlugin.PngInfo()
        info.add(kind, value)
        with pytest.raises(JPEGExportError, match="profile overrides"):
            export_jpeg(png(pnginfo=info))


def test_srgb_chunk_is_accepted_and_other_metadata_stripped() -> None:
    info = PngImagePlugin.PngInfo()
    info.add(b"sRGB", b"\x00")
    info.add_text("Comment", "Private machine path and timestamp")
    result = export_jpeg(png(pnginfo=info))
    assert result == export_jpeg(png())
    assert b"Private machine" not in result


def test_bad_srgb_intent_is_rejected() -> None:
    info = PngImagePlugin.PngInfo()
    info.add(b"sRGB", b"\x04")
    with pytest.raises(JPEGExportError, match="intent"):
        export_jpeg(png(pnginfo=info))


def test_source_corruption_fails() -> None:
    source = png()
    with pytest.raises(JPEGExportError):
        export_jpeg(source[: len(source) // 2])


@pytest.mark.parametrize("mode", ["L", "CMYK"])
def test_output_mode_validation(mode: str) -> None:
    with pytest.raises(JPEGExportError, match="RGB JPEG"):
        validate_jpeg(jpeg(mode))


def test_output_size_dimensions_format_and_metadata_validation() -> None:
    for data in (
        png(),
        jpeg(size=(10, 10)),
        jpeg(icc_profile=b"profile"),
        jpeg(comment=b"private"),
    ):
        with pytest.raises(JPEGExportError):
            validate_jpeg(data)
    with pytest.raises(JPEGExportError, match="8,000,000"):
        validate_jpeg(b"\xff\xd8" + bytes(8_000_000) + b"\xff\xd9")
    data = jpeg()
    with pytest.raises(JPEGExportError):
        validate_jpeg(data[: len(data) // 2])
    with pytest.raises(JPEGExportError):
        validate_jpeg(b"\xff\xd8broken\xff\xd9")


def test_encoder_output_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    source = png()

    def broken_save(self: Image.Image, output: BytesIO, **kwargs: object) -> None:
        output.write(b"broken")

    monkeypatch.setattr(Image.Image, "save", broken_save)
    with pytest.raises(JPEGExportError):
        export_jpeg(source)


def test_no_application_writes_and_safe_structured_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.INFO, logger="ask_every_day.jpeg_export"):
        result = export_jpeg(png())
        with pytest.raises(JPEGExportError):
            export_jpeg(b"private payload")
    assert list(tmp_path.iterdir()) == []
    success, failure = caplog.records[-2:]
    assert success.__dict__["event"] == "jpeg_exported"
    assert success.__dict__["byte_size"] == len(result)
    assert failure.__dict__["event"] == "jpeg_export_failed"
    assert "private payload" not in caplog.text


def test_color_key_transparency_is_not_silently_discarded() -> None:
    with pytest.raises(JPEGExportError, match="transparency"):
        export_jpeg(png(transparency=(0, 0, 0)))
