"""Read-only evidence checks using temporary SQLite and synthetic JPEGs."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from PIL import Image

from ask_every_day.domain import AppConfig, Post, Publication, PublicationStatus
from ask_every_day.palette import next_assignment
from ask_every_day.palette_audit import audit
from ask_every_day.publication_assets import (
    AssetRecord,
    Ledger,
    _save_ledger,
    ledger_path,
    read_ledger,
)
from ask_every_day.repository import open_repository
from tests.test_publication_assets import prepare, success
from tests.test_publication_assets import setup as setup

NOW = datetime(2026, 11, 13, tzinfo=UTC)


def test_full_history_is_audited_and_latest_twenty_are_in_completion_order(tmp_path):
    config = AppConfig(tmp_path / "state.db", tmp_path / "assets", "@ask.every.day")
    config.assets_path.mkdir()
    ledger = Ledger()
    previous = None
    with open_repository(config) as repo:
        # Insert in reverse order so the audit must sort by completion, not row ID.
        for index in reversed(range(24)):
            post = Post(f"AED-{index + 1:04}", "Synthetic?", "A", "B", "Classic")
            repo.create_post(post)
            stamp = NOW + timedelta(minutes=index)
            repo.create_publication(
                Publication(post.id, PublicationStatus.PUBLISHED, stamp, stamp)
            )
    for index in range(24):
        post_id = f"AED-{index + 1:04}"
        previous = next_assignment(config.palette, previous)
        jpeg = config.assets_path / f"{post_id}.jpg"
        with Image.new("RGB", (1080, 1350), previous.background) as image:
            image.save(jpeg, quality=95, subsampling=0)
        ledger.assets[post_id] = AssetRecord(
            previous, "a" * 64, hashlib.sha256(jpeg.read_bytes()).hexdigest(), "b" * 64
        )
    _save_ledger(config, ledger)
    files = [config.database_path, ledger_path(config), *config.assets_path.iterdir()]
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in files}
    result = audit(config)
    assert result["status"] == "verified"
    assert result["complete_balanced_bags"] == 8
    assert set(result["tracked_colour_counts"].values()) == {8}
    latest = result["latest_publications"]
    assert [row["sequence"] for row in latest] == list(range(5, 25))
    assert [row["attempt_id"] for row in latest] == list(range(20, 0, -1))
    assert before == {
        path: (path.stat().st_mtime_ns, path.read_bytes()) for path in files
    }
    (config.assets_path / "AED-0001.jpg").unlink()
    assert audit(config)["status"] == "incomplete"


def test_legacy_missing_ledger_and_reordered_preparation(setup):
    repo, config = setup
    success(repo)
    assert audit(config)["issues"] == [{"code": "palette_ledger_missing"}]
    first = prepare(repo, config, previous_background="#F4F0E8")
    success(repo, 1)
    prepared = prepare(repo, config)
    result = audit(config)
    assert result["status"] == "verified"
    legacy, tracked = result["latest_publications"]
    assert legacy["tracking"] == "legacy" and legacy["background"] is None
    assert tracked["background"] == first.assignment.background
    assert tracked["jpeg"] == "verified"
    ledger = read_ledger(config)
    assert ledger is not None
    ledger.assets["AED-0003"] = replace(prepared, assignment=first.assignment)
    _save_ledger(config, ledger)
    result = audit(config)
    assert result["status"] == "verified" and result["stale_preparations"] == [
        "AED-0003"
    ]


def test_detects_changed_pixels_even_if_sidecar_hash_matches(setup):
    repo, config = setup
    first = prepare(repo, config)
    success(repo)
    path = config.assets_path / "AED-0001.jpg"
    with Image.new("RGB", (1080, 1350), "#FFFFFF") as image:
        image.save(path)
    result = audit(config)
    assert {issue["code"] for issue in result["issues"]} == {
        "canonical_jpeg_hash_mismatch",
        "background_mismatch",
    }
    _save_ledger(
        config,
        Ledger(
            assets={
                "AED-0001": replace(
                    first, jpeg_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
                )
            }
        ),
    )
    assert audit(config)["issues"] == [{"code": "background_mismatch", "attempt_id": 1}]
