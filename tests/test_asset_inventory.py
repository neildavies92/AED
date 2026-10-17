"""Strict inventory checks decode real images without modifying local state."""

import json
import logging
import socket
import sqlite3
import subprocess
import sys
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from ask_every_day.asset_inventory import (
    AssetInventoryError,
    AssetInventoryResult,
    validate_asset_inventory,
)
from ask_every_day.batch_render import render_assets
from ask_every_day.content_import import import_snapshot
from ask_every_day.domain import AppConfig, Post, QueueEntry
from ask_every_day.logging_config import JSONFormatter
from ask_every_day.repository import Repository, open_repository

DATA = Path(__file__).parents[1] / "data"


def image_bytes(
    format_name: str, size: tuple[int, int] = (1080, 1350), **kwargs: object
) -> bytes:
    stream = BytesIO()
    with Image.new("RGB", size, (244, 240, 232)) as image:
        image.save(stream, format=format_name, **kwargs)
    return stream.getvalue()


@pytest.fixture
def inventory(tmp_path: Path) -> tuple[AppConfig, Mock]:
    settings = AppConfig(
        database_path=tmp_path / "posts.sqlite3",
        assets_path=tmp_path / "assets",
        account_handle="@ask.every.day",
    )
    post = Post(
        id="AED-0001",
        prompt="Private editorial question?",
        category="A",
        subcategory="B",
        template="Classic",
    )
    with open_repository(settings) as repo:
        repo.create_post(post)
        repo.replace_queue([QueueEntry(post_id=post.id, position=1)])
    view = Mock(spec=Repository)
    view.list_posts.return_value = [post]
    settings.assets_path.mkdir()
    (settings.assets_path / "AED-0001.png").write_bytes(image_bytes("PNG"))
    (settings.assets_path / "AED-0001.jpg").write_bytes(image_bytes("JPEG"))
    return settings, view


def state(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_complete_read_only_inventory(
    inventory: tuple[AppConfig, Mock], caplog: pytest.LogCaptureFixture
) -> None:
    settings, view = inventory
    before = state(settings.assets_path.parent)
    with caplog.at_level(logging.INFO):
        assert validate_asset_inventory(view, settings) == AssetInventoryResult(1, 1, 1)
    assert state(settings.assets_path.parent) == before
    view.list_posts.assert_called_once_with()
    assert len(view.mock_calls) == 1
    assert "Private editorial" not in "\n".join(
        JSONFormatter().format(r) for r in caplog.records
    )


@pytest.mark.parametrize("removed", [("png",), ("jpg",), ("png", "jpg")])
def test_missing_assets(
    inventory: tuple[AppConfig, Mock], removed: tuple[str, ...]
) -> None:
    settings, view = inventory
    for extension in removed:
        (settings.assets_path / f"AED-0001.{extension}").unlink()
    with pytest.raises(AssetInventoryError, match="missing asset") as caught:
        validate_asset_inventory(view, settings)
    assert f"{len(removed)} issue(s)" in str(caught.value)


def test_absent_directory(inventory: tuple[AppConfig, Mock]) -> None:
    settings, view = inventory
    for path in settings.assets_path.iterdir():
        path.unlink()
    settings.assets_path.rmdir()
    with pytest.raises(AssetInventoryError, match="existing regular directory"):
        validate_asset_inventory(view, settings)
    assert not settings.assets_path.exists()


@pytest.mark.parametrize(
    "name",
    [
        "notes.txt",
        "AED-9999.jpg",
        "AED-0001.jpeg",
        "AED-0001.PNG",
        "aed-0001.jpg",
        "AED-0001-2.jpg",
        ".AED-0001-stale.tmp",
    ],
)
def test_unexpected_names(inventory: tuple[AppConfig, Mock], name: str) -> None:
    settings, view = inventory
    # Case-sensitive exact matching must also work on case-insensitive macOS disks.
    target = settings.assets_path / name
    if target.exists():
        canonical = settings.assets_path / (
            "AED-0001.png" if name.endswith("PNG") else "AED-0001.jpg"
        )
        canonical.rename(settings.assets_path / "rename-tmp")
        (settings.assets_path / "rename-tmp").rename(target)
    else:
        target.write_bytes(b"unexpected private bytes")
    before = state(settings.assets_path)
    with pytest.raises(AssetInventoryError, match="unexpected entry"):
        validate_asset_inventory(view, settings)
    assert state(settings.assets_path) == before


@pytest.mark.parametrize(
    "kind", ["nested", "expected_directory", "symlink", "root_symlink"]
)
def test_nonregular_entries(inventory: tuple[AppConfig, Mock], kind: str) -> None:
    settings, view = inventory
    target = settings.assets_path / "AED-0001.png"
    if kind == "nested":
        (settings.assets_path / "nested").mkdir()
    elif kind == "root_symlink":
        original = settings.assets_path.with_name("original")
        settings.assets_path.rename(original)
        settings.assets_path.symlink_to(original, target_is_directory=True)
    else:
        target.unlink()
        if kind == "symlink":
            target.symlink_to(settings.assets_path / "AED-0001.jpg")
        else:
            target.mkdir()
    with pytest.raises(AssetInventoryError):
        validate_asset_inventory(view, settings)


@pytest.mark.parametrize(
    "case",
    [
        "corrupt_png",
        "jpeg_as_png",
        "small_png",
        "truncated_png",
        "corrupt_jpeg",
        "png_as_jpeg",
        "small_jpeg",
        "metadata_jpeg",
    ],
)
def test_invalid_image_content(inventory: tuple[AppConfig, Mock], case: str) -> None:
    settings, view = inventory
    png = settings.assets_path / "AED-0001.png"
    jpeg = settings.assets_path / "AED-0001.jpg"
    replacements = {
        "corrupt_png": (png, b"private malformed bytes"),
        "jpeg_as_png": (png, jpeg.read_bytes()),
        "small_png": (png, image_bytes("PNG", (10, 10))),
        "truncated_png": (png, png.read_bytes()[:80]),
        "corrupt_jpeg": (jpeg, b"private malformed bytes"),
        "png_as_jpeg": (jpeg, png.read_bytes()),
        "small_jpeg": (jpeg, image_bytes("JPEG", (10, 10))),
        "metadata_jpeg": (jpeg, image_bytes("JPEG", comment=b"private comment")),
    }
    target, data = replacements[case]
    target.write_bytes(data)
    with pytest.raises(AssetInventoryError, match="invalid image") as caught:
        validate_asset_inventory(view, settings)
    assert "private" not in str(caught.value)


def test_unreadable_asset(
    inventory: tuple[AppConfig, Mock], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, view = inventory
    original = Path.read_bytes

    def read(path: Path) -> bytes:
        if path.suffix == ".png":
            raise PermissionError("private detail")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(AssetInventoryError, match="unreadable"):
        validate_asset_inventory(view, settings)


def test_duplicate_and_empty_expected_ids(inventory: tuple[AppConfig, Mock]) -> None:
    settings, view = inventory
    view.list_posts.return_value *= 2
    with pytest.raises(AssetInventoryError, match="Duplicate expected"):
        validate_asset_inventory(view, settings)
    view.list_posts.return_value = []
    with pytest.raises(AssetInventoryError, match="make import-content"):
        validate_asset_inventory(view, settings)


def test_bounded_deterministic_diagnostics(inventory: tuple[AppConfig, Mock]) -> None:
    settings, view = inventory
    for index in reversed(range(25)):
        (settings.assets_path / f"extra-{index:02d}").touch()
    messages = []
    for _ in range(2):
        with pytest.raises(AssetInventoryError) as caught:
            validate_asset_inventory(view, settings)
        messages.append(str(caught.value))
    assert messages[0] == messages[1]
    assert "25 issue(s); showing 10" in messages[0]
    assert "extra-09" in messages[0] and "extra-10" not in messages[0]


def test_real_import_batch_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("No network")

    monkeypatch.setattr(socket.socket, "connect", deny)
    settings = AppConfig(
        database_path=tmp_path / "data.sqlite3",
        assets_path=tmp_path / "assets",
        account_handle="@ask.every.day",
    )
    with open_repository(settings) as repo:
        import_snapshot(
            repo, DATA / "prompts.csv", DATA / "taxonomy.csv", DATA / "hashtags.csv"
        )
    with open_repository(settings) as repo:
        posts = repo.list_posts()
        assert len(posts) == 400
        selected = [p for p in posts if p.id in {"AED-0001", "AED-0236", "AED-0364"}]
    representative = replace(
        settings, database_path=tmp_path / "representative.sqlite3"
    )
    with open_repository(representative) as repo:
        assert repo.import_posts(selected).imported_count == 3
    with open_repository(representative) as repo:
        assert repo.list_posts() == selected
        assert render_assets(repo, representative).post_count == 3
        before = state(tmp_path)
        assert validate_asset_inventory(repo, representative) == AssetInventoryResult(
            3, 3, 3
        )
        assert state(tmp_path) == before


def test_cli_success_failure_and_no_db_creation(
    inventory: tuple[AppConfig, Mock],
) -> None:
    settings, _ = inventory
    config = settings.assets_path.parent / "aed.toml"
    config.write_text(
        'database_path="posts.sqlite3"\nassets_path="assets"\naccount_handle="@ask.every.day"\n'
    )
    command = [
        sys.executable,
        "-m",
        "ask_every_day.asset_inventory",
        "--config",
        str(config),
    ]
    before = state(config.parent)
    success = subprocess.run(command, capture_output=True, text=True, check=False)
    assert success.returncode == 0, success.stderr
    assert "Validated 1 Posts: 1 PNG and 1 JPEG" in success.stdout
    assert "asset_inventory_validated" in [
        json.loads(line)["event"] for line in success.stderr.splitlines()
    ]
    assert state(config.parent) == before
    (settings.assets_path / "AED-0001.png").unlink()
    failed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert failed.returncode == 1 and "missing asset" in failed.stderr
    settings.database_path.unlink()
    missing = subprocess.run(command, capture_output=True, text=True, check=False)
    assert missing.returncode == 1 and not settings.database_path.exists()


def test_cli_does_not_migrate_old_database(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE posts(id TEXT PRIMARY KEY); PRAGMA user_version=1;"
        )
    config = tmp_path / "aed.toml"
    config.write_text(
        'database_path="old.sqlite3"\nassets_path="assets"\naccount_handle="@ask.every.day"\n'
    )
    before = state(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ask_every_day.asset_inventory",
            "--config",
            str(config),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert state(tmp_path) == before
