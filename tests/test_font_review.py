"""Offline synthetic metadata; no Microsoft binaries or licence acceptance."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from ask_every_day import font_review, scheduler_policy

spec = importlib.util.spec_from_file_location(
    "desktop_compose",
    Path(__file__).resolve().parents[1] / "deployment/desktop-compose.py",
)
assert spec and spec.loader
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


@pytest.fixture
def fonts(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.ttf"
    path.write_bytes(b"NOT_A_REAL_FONT")
    monkeypatch.setattr(font_review, "FONT_ROOT", tmp_path)

    def match(command, **kwargs):
        family, weight, slant = command[-1].split(":")
        return SimpleNamespace(
            stdout=f"{family}\tSynthetic\t65536\t{path}\t{weight.split('=')[1]}\t{slant.split('=')[1]}\t0\n"
        )

    monkeypatch.setattr(font_review.subprocess, "run", match)
    return path


def test_all_families_styles_metadata_hashes_without_binaries(fonts):
    rows = font_review.inventory(require_mounted=True)
    assert len(rows) == 8
    assert {r["requested_style"] for r in rows} == set(font_review.STYLES)
    assert all(r["exact_family"] and r["style_matches"] for r in rows)
    assert all(
        len(r["sha256"]) == 64 and r["fontconfig_version"] == "65536" for r in rows
    )
    assert "NOT_A_REAL_FONT" not in json.dumps(rows)


def test_missing_family_rejected_but_fallback_report_available(fonts, monkeypatch):
    original = font_review.subprocess.run

    def fallback(*args, **kwargs):
        result = original(*args, **kwargs)
        return SimpleNamespace(
            stdout=result.stdout.replace("Liberation Serif", "DejaVu Serif")
        )

    monkeypatch.setattr(font_review.subprocess, "run", fallback)
    assert not font_review.inventory()[0]["exact_family"]
    with pytest.raises(ValueError, match="unavailable"):
        font_review.inventory(require_mounted=True)


def test_synthetic_bold_not_accepted_for_missing_real_style(fonts, monkeypatch):
    original = font_review.subprocess.run
    monkeypatch.setattr(
        font_review.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout=original(*a, **k).stdout.replace("\t200\t", "\t80\t")
        ),
    )
    with pytest.raises(ValueError, match="unavailable"):
        font_review.inventory(require_mounted=True)


def test_matching_system_fonts_cannot_masquerade_as_private_overlay(fonts, monkeypatch):
    monkeypatch.setattr(font_review, "FONT_ROOT", fonts.parent / "elsewhere")
    with pytest.raises(ValueError, match="unavailable"):
        font_review.inventory(require_mounted=True)


def test_unavailable_fontconfig_fails_closed(monkeypatch):
    monkeypatch.setattr(
        font_review.subprocess, "run", Mock(side_effect=FileNotFoundError)
    )
    with pytest.raises(FileNotFoundError):
        font_review.inventory()


def test_changed_font_bytes_invalidate_policy_key(fonts, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    before = scheduler_policy.runtime_key()
    fonts.write_bytes(b"DIFFERENT_SYNTHETIC_FONT")
    assert scheduler_policy.runtime_key() != before


@pytest.mark.parametrize(
    "command",
    [
        "preview",
        "schedule-preview",
        "schedule-status",
        "schedule-run",
        "schedule-enable",
        "font-inventory",
    ],
)
def test_same_overlay_and_strict_preflight_for_every_command(
    tmp_path, monkeypatch, command
):
    (tmp_path / "compose.yaml").write_text("synthetic")
    (tmp_path / "fonts.compose.yaml").write_text("synthetic")
    (tmp_path / "fonts").mkdir()
    execute = Mock(return_value=Mock(stdout="unix:///synthetic/docker.sock"))
    monkeypatch.setattr(wrapper.subprocess, "run", execute)
    wrapper.run(tmp_path, [command])
    assert execute.call_count == 3
    assert all(
        str(tmp_path / "fonts.compose.yaml") in c.args[0]
        for c in execute.call_args_list[1:]
    )
    assert "--require-mounted" in execute.call_args_list[1].args[0]


def test_no_overlay_keeps_open_fallbacks(tmp_path, monkeypatch):
    (tmp_path / "compose.yaml").write_text("synthetic")
    execute = Mock(return_value=Mock(stdout="unix:///synthetic/docker.sock"))
    monkeypatch.setattr(wrapper.subprocess, "run", execute)
    wrapper.run(tmp_path, ["preview"])
    assert execute.call_count == 2
    assert "fonts.compose.yaml" not in " ".join(execute.call_args.args[0])


def test_operator_compose_is_local_and_ignores_shell_overrides(tmp_path, monkeypatch):
    (tmp_path / "compose.yaml").write_text("synthetic")
    monkeypatch.setenv("DOCKER_HOST", "ssh://remote.invalid")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setenv("COMPOSE_FILE", "/unreviewed.yaml")
    execute = Mock(return_value=Mock(stdout="unix:///synthetic/docker.sock"))
    monkeypatch.setattr(wrapper.subprocess, "run", execute)
    wrapper.run(tmp_path, ["preview"])
    for call in execute.call_args_list:
        assert call.args[0][:3] == ["docker", "--context", "desktop-linux"]
        assert (
            not {"DOCKER_HOST", "DOCKER_CONTEXT", "COMPOSE_FILE"}
            & call.kwargs["env"].keys()
        )
    assert "--env-file" in execute.call_args.args[0]
    execute.reset_mock()
    execute.return_value.stdout = "ssh://remote.invalid"
    with pytest.raises(ValueError, match="local_docker_desktop"):
        wrapper.run(tmp_path, ["preview"])
    assert execute.call_count == 1


@pytest.mark.parametrize("name", ["fonts", "fonts.compose.yaml"])
def test_partial_overlay_configuration_blocks(tmp_path, name):
    (tmp_path / "compose.yaml").write_text("synthetic")
    (tmp_path / name).mkdir()
    with pytest.raises(ValueError, match="required_together"):
        wrapper.compose_prefix(tmp_path)


def test_pixel_comparison_measures_instead_of_claiming_parity(tmp_path):
    first, second = tmp_path / "first.jpg", tmp_path / "second.jpg"
    Image.new("RGB", (1080, 1350), "white").save(first)
    Image.new("RGB", (1080, 1350), "black").save(second)
    assert font_review.compare(first, first)["pixel_equal"]
    assert not font_review.compare(first, second)["pixel_equal"]
    Image.new("RGB", (10, 10), "white").save(second)
    with pytest.raises(ValueError, match="dimensions"):
        font_review.compare(first, second)


def test_review_family_override_does_not_change_classic_template(tmp_path, monkeypatch):
    from ask_every_day.domain import Post, RendererConfig
    from ask_every_day.renderer import render_svg

    monkeypatch.setattr(
        font_review, "inventory", lambda **kwargs: [{"family": kwargs["families"][0]}]
    )
    monkeypatch.setattr(font_review, "export_png", lambda *args: b"synthetic-png")
    monkeypatch.setattr(font_review, "export_jpeg", lambda *args: b"synthetic-jpeg")
    output = tmp_path / "review"
    font_review.render(
        output,
        Path(__file__).resolve().parents[1] / "data",
        "#EDF4FB",
        body_family="Lato",
        header_family="Lato",
    )
    assert 'font-family="Lato"' in (output / "AED-0311.svg").read_text()
    original = render_svg(
        Post("AED-0001", "A question?", "Test", "Test", "Classic"), RendererConfig()
    )
    assert "Liberation Serif" in original and "Lato" in original
    assert list(tmp_path.iterdir()) == [output]
