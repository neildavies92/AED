"""SQLite-to-assets acceptance, controlled failures and CLI contracts."""

import json
import logging
import socket
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from ask_every_day import batch_render
from ask_every_day.batch_render import (
    BatchRenderError,
    BatchRenderResult,
    render_assets,
)
from ask_every_day.content_import import import_snapshot
from ask_every_day.domain import AppConfig, Post
from ask_every_day.logging_config import JSONFormatter
from ask_every_day.repository import open_repository

DATA = Path(__file__).parents[1] / "data"


def config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        database_path=tmp_path / "posts.sqlite3",
        assets_path=tmp_path / "nested" / "assets",
        account_handle="@ask.every.day",
    )


def post(number: int = 1) -> Post:
    return Post(
        id=f"AED-{number:04d}",
        prompt="What feels like home?",
        category="Self-awareness",
        subcategory="Identity",
        template="Classic",
        highlight_text="home",
    )


def deny_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("Batch rendering must not access the network")


def test_production_400_post_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    settings = config(tmp_path)
    with open_repository(settings) as repo:
        import_snapshot(
            repo, DATA / "prompts.csv", DATA / "taxonomy.csv", DATA / "hashtags.csv"
        )
    # Reopen proves SQLite, not an in-memory import result, is the runtime source.
    with open_repository(settings) as repo:
        started = time.monotonic()
        result = render_assets(repo, settings)
        duration = time.monotonic() - started
    print(f"400-post real batch: {duration:.2f}s; 400 PNG + 400 JPEG")
    assert result == BatchRenderResult(400, 400, 400)
    assert {p.name for p in settings.assets_path.iterdir()} == {
        f"AED-{number:04d}{suffix}"
        for number in range(1, 401)
        for suffix in (".png", ".jpg")
    }
    for path in settings.assets_path.iterdir():
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            assert image.size == (1080, 1350)
            assert image.mode == "RGB"
            assert image.format == ("PNG" if path.suffix == ".png" else "JPEG")
        if path.suffix == ".jpg":
            assert path.stat().st_size <= 8_000_000


def test_rerun_replaces_expected_files_only(tmp_path: Path) -> None:
    settings = config(tmp_path)
    with open_repository(settings) as repo:
        repo.create_post(post())
        assert render_assets(repo, settings) == BatchRenderResult(1, 1, 1)
        (settings.assets_path / "notes.txt").write_text("keep")
        for suffix in ("png", "jpg"):
            (settings.assets_path / f"AED-0001.{suffix}").write_bytes(b"stale")
        assert render_assets(repo, settings) == BatchRenderResult(1, 1, 1)
    assert {p.name for p in settings.assets_path.iterdir()} == {
        "AED-0001.png",
        "AED-0001.jpg",
        "notes.txt",
    }
    assert (settings.assets_path / "notes.txt").read_text() == "keep"
    for suffix in ("png", "jpg"):
        with Image.open(
            BytesIO((settings.assets_path / f"AED-0001.{suffix}").read_bytes())
        ) as image:
            image.load()
            assert image.size == (1080, 1350)


def test_empty_repository_fails_before_directory_creation(tmp_path: Path) -> None:
    settings = config(tmp_path)
    with (
        open_repository(settings) as repo,
        pytest.raises(BatchRenderError, match="make import-content"),
    ):
        render_assets(repo, settings)
    assert not settings.assets_path.exists()


def test_order_failure_id_and_safe_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = config(tmp_path)
    order: list[str] = []
    original = batch_render.render_svg

    def render(value: Post, *args: object) -> str:
        order.append(value.id)
        if value.id == "AED-0002":
            raise RuntimeError("private prompt caption token payload")
        return original(value, settings.renderer)

    monkeypatch.setattr(batch_render, "render_svg", render)
    with open_repository(settings) as repo:
        repo.create_post(post(2))
        repo.create_post(post(1))
        with (
            caplog.at_level(logging.INFO),
            pytest.raises(BatchRenderError, match="AED-0002") as caught,
        ):
            render_assets(repo, settings)
    assert "private" not in str(caught.value)
    assert order == ["AED-0001", "AED-0002"]
    assert {p.name for p in settings.assets_path.iterdir()} == {
        "AED-0001.png",
        "AED-0001.jpg",
    }
    failure = next(
        record for record in caplog.records if record.msg == "batch_render_failed"
    )
    assert failure.__dict__["post_id"] == "AED-0002"
    assert failure.__dict__["post_count"] == 1
    encoded = "\n".join(JSONFormatter().format(record) for record in caplog.records)
    assert "private prompt" not in encoded and post().prompt not in encoded


@pytest.mark.parametrize("stage", ["jpeg", "write", "second_write", "replace"])
def test_failure_cleans_temps_and_preserves_complete_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    settings = config(tmp_path)
    settings.assets_path.mkdir(parents=True)
    png_path = settings.assets_path / "AED-0001.png"
    jpeg_path = settings.assets_path / "AED-0001.jpg"
    png_path.write_bytes(b"old PNG")
    jpeg_path.write_bytes(b"old JPEG")

    def fail(*args: object) -> None:
        raise OSError("private failure detail")

    if stage == "jpeg":
        monkeypatch.setattr(batch_render, "export_jpeg", fail)
    elif stage in {"write", "second_write"}:
        sync_calls = 0
        original_sync = batch_render.os.fsync

        def sync_file(descriptor: int) -> None:
            nonlocal sync_calls
            sync_calls += 1
            if sync_calls == (2 if stage == "second_write" else 1):
                fail()
            original_sync(descriptor)

        monkeypatch.setattr(batch_render.os, "fsync", sync_file)
    else:
        original_replace = batch_render.os.replace
        calls = 0

        def replace_file(source: Path, target: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                fail()
            original_replace(source, target)

        monkeypatch.setattr(batch_render.os, "replace", replace_file)
    with open_repository(settings) as repo:
        repo.create_post(post())
        with pytest.raises(BatchRenderError, match="AED-0001"):
            render_assets(repo, settings)
    assert {p.name for p in settings.assets_path.iterdir()} == {
        png_path.name,
        jpeg_path.name,
    }
    assert jpeg_path.read_bytes() == b"old JPEG"
    if stage != "replace":
        assert png_path.read_bytes() == b"old PNG"
    else:
        with Image.open(png_path) as image:
            image.load()
            assert image.format == "PNG" and image.size == (1080, 1350)


def test_cli_config_success_and_empty_failure(tmp_path: Path) -> None:
    settings = config(tmp_path)
    config_file = tmp_path / "custom.toml"
    config_file.write_text(
        'database_path = "posts.sqlite3"\nassets_path = "nested/assets"\n'
        'account_handle = "@ask.every.day"\n'
    )
    command = [
        sys.executable,
        "-m",
        "ask_every_day.batch_render",
        "--config",
        str(config_file),
    ]
    failed = subprocess.run(command, text=True, capture_output=True, check=False)
    assert failed.returncode == 1 and "make import-content" in failed.stderr
    with open_repository(settings) as repo:
        repo.create_post(post())
    success = subprocess.run(command, text=True, capture_output=True, check=False)
    assert success.returncode == 0, success.stderr
    assert "1 PNG and 1 JPEG" in success.stdout
    events = [json.loads(line)["event"] for line in success.stderr.splitlines()]
    assert (
        events.count("batch_render_started")
        == events.count("batch_render_completed")
        == 1
    )
    assert post().prompt not in success.stderr


def test_cli_render_failure_is_nonzero(tmp_path: Path) -> None:
    settings = config(tmp_path)
    config_file = tmp_path / "bad-render.toml"
    config_file.write_text(
        'database_path="posts.sqlite3"\nassets_path="nested/assets"\n'
        'account_handle="@ask.every.day"\n[renderer]\ntemplate="Other"\n'
    )
    with open_repository(settings) as repo:
        repo.create_post(post())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ask_every_day.batch_render",
            "--config",
            str(config_file),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "AED-0001" in result.stderr and post().prompt not in result.stderr
    assert list(settings.assets_path.iterdir()) == []
