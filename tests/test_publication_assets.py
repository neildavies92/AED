"""Real render/SQLite integration; fake publication results only, no provider calls."""

import hashlib
import json
import logging
import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from ask_every_day import publication_assets as assets
from ask_every_day.batch_render import render_assets
from ask_every_day.domain import AppConfig, Post, QueueEntry
from ask_every_day.logging_config import JSONFormatter
from ask_every_day.publication_assets import (
    AssetError,
    asset_preview,
    ledger_path,
    read_ledger,
)
from ask_every_day.publication_service import PublicationService
from ask_every_day.repository import open_repository

NOW = datetime(2026, 10, 26, tzinfo=UTC)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        socket, "create_connection", Mock(side_effect=AssertionError("No network"))
    )


@pytest.fixture
def setup(tmp_path):
    config = AppConfig(tmp_path / "db.sqlite", tmp_path / "assets", "@ask.every.day")
    with open_repository(config) as repo:
        for i in range(1, 16):
            repo.create_post(
                Post(
                    f"AED-{i:04}",
                    f"What matters most to you {i}?",
                    "A",
                    "B",
                    "Classic",
                    "matters most",
                )
            )
        repo.replace_queue([QueueEntry(f"AED-{i:04}", i) for i in range(15, 0, -1)])
        yield repo, config


def prepare(repo, config, **kwargs) -> assets.AssetRecord:
    result = assets.prepare_next_asset(repo, config, **kwargs)
    assert result is not None
    return result


def ledger(config) -> assets.Ledger:
    result = read_ledger(config)
    assert result is not None
    return result


def success(repo, index=0):
    entry = repo.next_queue_entry()
    service = PublicationService(repo)
    attempt = service.begin_publish(entry.post_id, NOW + timedelta(seconds=index))
    service.record_success(
        attempt.id, NOW + timedelta(seconds=index), f"synthetic-{index}"
    )
    return entry.post_id


def preview(repo, config):
    entry = repo.next_queue_entry()
    return asset_preview(repo, config, repo.get_post(entry.post_id), entry)


def test_preparation_required_and_reused_byte_for_byte(setup):
    repo, config = setup
    render_assets(repo, config)
    assignment, actual = preview(repo, config)
    assert actual is None  # A valid ID-order draft is insufficient.
    selected = prepare(repo, config)
    assert selected is not None and selected.assignment == assignment
    jpeg = config.assets_path / "AED-0001.jpg"
    before = jpeg.read_bytes()
    stamp = jpeg.stat().st_mtime_ns
    assert preview(repo, config)[1] == hashlib.sha256(before).hexdigest()
    assert prepare(repo, config) == selected
    assert jpeg.read_bytes() == before and jpeg.stat().st_mtime_ns == stamp
    with Image.open(BytesIO(before)) as im:
        assert im.size == (1080, 1350) and im.mode == "RGB"
        pixel = im.getpixel((0, 0))
        assert isinstance(pixel, tuple)
        expected = tuple(bytes.fromhex(assignment.background[1:]))
        assert max(abs(a - b) for a, b in zip(pixel, expected)) <= 2
    assert repo.publication_history("AED-0001") == []


def test_failures_skips_and_restart_do_not_consume_success_slot(setup):
    repo, config = setup
    first = prepare(repo, config)
    success(repo, 1)
    second = prepare(repo, config)
    service = PublicationService(repo)
    failed = service.begin_publish("AED-0002", NOW + timedelta(seconds=2))
    service.record_failure(failed.id, "synthetic_known_failure")
    with open_repository(config) as reopened:
        third = prepare(reopened, config)
        assert third.assignment == second.assignment
        success(reopened, 3)
    assert first.assignment.background != third.assignment.background
    # Skip/reorder the prepared next Post; its ID does not determine the colour.
    fourth = prepare(repo, config)
    repo.replace_queue([QueueEntry("AED-0008", 1), QueueEntry("AED-0004", 2)])
    eighth = prepare(repo, config)
    assert eighth.assignment == fourth.assignment
    success(repo, 4)
    assert preview(repo, config)[1] is None  # Old reservation needs a new sequence.
    updated = prepare(repo, config)
    assert (
        updated.assignment.sequence == 4
        and updated.assignment.background != eighth.assignment.background
    )
    assert len(repo.publication_history("AED-0002")) == 1


def test_many_successes_balanced_across_fresh_connections(setup):
    repo, config = setup
    colours = []
    for i in range(12):
        with open_repository(config) as r:
            asset = prepare(r, config)
            colours.append(asset.assignment.background)
            success(r, i)
    assert all(a != b for a, b in zip(colours, colours[1:]))
    for i in range(0, 12, 3):
        assert set(colours[i : i + 3]) == set(config.palette.backgrounds)
    assert len(ledger(config).assets) == 12


def test_bulk_render_preserves_prepared_and_all_attempted_bytes(setup):
    repo, config = setup
    prepare(repo, config)
    first = (config.assets_path / "AED-0001.jpg").read_bytes()
    result = render_assets(repo, config)
    assert (
        result.skipped_count == 1
        and (config.assets_path / "AED-0001.jpg").read_bytes() == first
    )
    success(repo)
    second = prepare(repo, config)
    pending = PublicationService(repo).begin_publish("AED-0002", NOW)
    second_bytes = (config.assets_path / "AED-0002.jpg").read_bytes()
    assert render_assets(repo, config).skipped_count == 2
    assert (config.assets_path / "AED-0002.jpg").read_bytes() == second_bytes
    with pytest.raises(AssetError, match="unresolved_publication"):
        prepare(repo, config)
    PublicationService(repo).record_failure(pending.id)
    assert render_assets(repo, config).skipped_count == 2
    assert second.assignment.sequence == 2


@pytest.mark.parametrize("change", ["jpeg", "template", "palette", "seed"])
def test_changed_render_inputs_or_bytes_require_explicit_preparation(
    setup, monkeypatch, change
):
    repo, config = setup
    prepare(repo, config)
    if change == "jpeg":
        (config.assets_path / "AED-0001.jpg").write_bytes(b"old or corrupt")
    if change == "template":
        original = assets.render_svg
        monkeypatch.setattr(
            assets, "render_svg", lambda *a, **k: original(*a, **k) + "\n"
        )
    if change == "palette":
        config = replace(
            config,
            palette=replace(
                config.palette, backgrounds=("#FFFFFF", "#EDF4FB", "#D3E6F5")
            ),
        )
    if change == "seed":
        config = replace(config, palette=replace(config.palette, seed=999))
    assert preview(repo, config)[1] is None
    current = prepare(repo, config)
    assert preview(repo, config)[1] == current.jpeg_sha256
    assert repo.publication_history("AED-0001") == []


def test_legacy_history_requires_explicit_boundary_and_preserves_records(setup):
    repo, config = setup
    post_id = success(repo)
    before = repo.publication_history(post_id)
    with pytest.raises(AssetError, match="palette_history_missing"):
        prepare(repo, config)
    first = prepare(repo, config, previous_background="#E2EFF9")
    assert first.assignment.background != "#E2EFF9"
    assert ledger(config).legacy_publications == [before[0].id]
    assert repo.publication_history(post_id) == before
    with pytest.raises(AssetError, match="palette_already_initialized"):
        prepare(repo, config, previous_background="#F4F0E8")
    ledger_path(config).unlink()
    with pytest.raises(AssetError, match="palette_history_missing"):
        prepare(repo, config)


def test_untracked_success_after_initialization_fails_closed(setup):
    repo, config = setup
    prepare(repo, config)
    success(repo)
    success(repo, 1)  # Existing low-level service bypassed asset preparation.
    with pytest.raises(AssetError, match="untracked_publication"):
        prepare(repo, config)


@pytest.mark.parametrize(
    "payload", [b"not json", b"{}", b'{"version":1,"version":1}', b"x" * 1_048_577]
)
def test_corrupt_ledger_never_overwrites_canonical_assets(setup, payload):
    repo, config = setup
    prepare(repo, config)
    before = (config.assets_path / "AED-0001.jpg").read_bytes()
    ledger_path(config).write_bytes(payload)
    with pytest.raises(AssetError, match="palette_ledger_invalid"):
        prepare(repo, config)
    assert (config.assets_path / "AED-0001.jpg").read_bytes() == before
    with pytest.raises(ValueError):
        render_assets(repo, config)


def test_ledger_symlink_fails_closed(setup, tmp_path):
    repo, config = setup
    target = tmp_path / "private.json"
    target.write_text("SYNTHETIC_PRIVATE")
    ledger_path(config).symlink_to(target)
    with pytest.raises(AssetError, match="palette_ledger_invalid"):
        prepare(repo, config)
    assert target.read_text() == "SYNTHETIC_PRIVATE"


def test_failed_asset_or_metadata_write_requires_repair_before_approval(
    setup, monkeypatch
):
    repo, config = setup
    with monkeypatch.context() as m:
        m.setattr(
            assets, "_save_ledger", Mock(side_effect=OSError("SYNTHETIC_PRIVATE"))
        )
        with pytest.raises(AssetError):
            prepare(repo, config)
    assert not ledger_path(config).exists() and preview(repo, config)[1] is None
    prepare(repo, config)
    assert preview(repo, config)[1]
    assert repo.publication_history("AED-0001") == []


def test_palette_logs_have_colour_sequence_and_queue_without_content(setup, caplog):
    repo, config = setup
    with caplog.at_level(logging.INFO):
        prepared = prepare(repo, config)
        preview(repo, config)
    records = [json.loads(JSONFormatter().format(r)) for r in caplog.records]
    selected = [r for r in records if r["event"] == "palette_selected"][-1]
    assert selected["palette_colour"] == prepared.assignment.background
    assert selected["post_id"] == "AED-0001" and selected["palette_sequence"] == 1
    assert selected["queue_position"] == 1
    assert "What matters" not in json.dumps(
        records
    ) and "matters most" not in json.dumps(records)


def test_queue_regeneration_uses_same_success_slot_for_new_next_post(setup):
    from ask_every_day.queue_rules import QueueRules
    from ask_every_day.queue_service import generate_and_store_queue

    repo, config = setup
    # Real queue constraints, with distinct categories to make any order eligible.
    for index, post in enumerate(repo.list_posts()):
        repo.update_post(replace(post, category=f"Category-{index}"))
    first = prepare(repo, config)
    success(repo)
    pending = prepare(repo, config)
    generate_and_store_queue(repo, seed=93, rules=QueueRules(1, 0), regenerate=True)
    after = prepare(repo, config)
    assert after.assignment == pending.assignment and after.assignment.sequence == 2
    assert after.assignment.background != first.assignment.background


def test_missing_png_repaired_but_valid_rerun_does_not_reencode(setup, monkeypatch):
    repo, config = setup
    prepare(repo, config)
    png = config.assets_path / "AED-0001.png"
    png.unlink()
    repaired = prepare(repo, config)
    assert (
        png.exists()
        and hashlib.sha256(png.read_bytes()).hexdigest() == repaired.png_sha256
    )
    monkeypatch.setattr(
        assets,
        "export_png",
        Mock(side_effect=AssertionError("Must reuse prepared bytes")),
    )
    assert prepare(repo, config) == repaired


def test_empty_queue_has_no_ledger_or_asset_side_effects(setup):
    repo, config = setup
    repo.clear_queue()
    assert assets.prepare_next_asset(repo, config) is None
    assert not ledger_path(config).exists() and not config.assets_path.exists()


def test_atomic_ledger_write_failure_preserves_previous_history(setup, monkeypatch):
    repo, config = setup
    prepare(repo, config)
    success(repo)
    before = ledger_path(config).read_bytes()
    original = assets.os.replace

    def fail_ledger(source, target):
        if Path(target) == ledger_path(config):
            raise OSError("SYNTHETIC_PRIVATE")
        return original(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(assets.os, "replace", fail_ledger)
        with pytest.raises(AssetError):
            prepare(repo, config)
    assert ledger_path(config).read_bytes() == before
    assert preview(repo, config)[1] is None
    assert list(config.database_path.parent.glob(".aed-palette-*")) == []
    prepare(repo, config)
    assert preview(repo, config)[1]


def test_renderer_failure_logs_safe_colour_context(setup, monkeypatch, caplog):
    repo, config = setup
    monkeypatch.setattr(
        assets,
        "export_png",
        Mock(side_effect=RuntimeError("SYNTHETIC_PRIVATE_EDITORIAL")),
    )
    with caplog.at_level(logging.INFO), pytest.raises(AssetError):
        prepare(repo, config)
    records = [json.loads(JSONFormatter().format(r)) for r in caplog.records]
    failure = next(r for r in records if r["event"] == "publication_asset_failed")
    assert failure["palette_colour"] in config.palette.backgrounds
    assert failure["palette_sequence"] == failure["queue_position"] == 1
    assert "SYNTHETIC_PRIVATE" not in json.dumps(records)


def test_seed_change_mid_bag_does_not_reset_balance(setup):
    repo, config = setup
    first = prepare(repo, config)
    success(repo)
    changed = replace(config, palette=replace(config.palette, seed=123))
    second = prepare(repo, changed)
    success(repo, 1)
    third = prepare(repo, changed)
    assert first.assignment.bag == second.assignment.bag == third.assignment.bag
    success(repo, 2)
    fourth = prepare(repo, changed)
    assert fourth.assignment.palette_key == changed.palette.key
    assert fourth.assignment.background != third.assignment.background


def test_changed_background_set_mid_bag_leaves_prepared_asset_intact(setup):
    repo, config = setup
    prepare(repo, config)
    success(repo)
    second = prepare(repo, config)
    before = ledger_path(config).read_bytes()
    changed = replace(
        config,
        palette=replace(config.palette, backgrounds=("#FFFFFF", "#EDF4FB", "#D3E6F5")),
    )
    with pytest.raises(AssetError, match="palette_change_requires_bag_boundary"):
        prepare(repo, changed)
    assert ledger_path(config).read_bytes() == before
    assert prepare(repo, config) == second


def test_prepare_cli_is_local_and_reports_expected_post(setup, monkeypatch, capsys):
    import sys

    from ask_every_day import render_next

    repo, config = setup
    monkeypatch.setattr(render_next, "load_config", Mock(return_value=config))
    monkeypatch.setattr(render_next, "configure_logging", Mock())
    monkeypatch.setattr(sys, "argv", ["render-next"])
    render_next.main()
    result = json.loads(capsys.readouterr().out)
    assert (
        result["post_id"] == "AED-0001"
        and result["background_color"] in config.palette.backgrounds
    )
    assert (
        result["palette_sequence"] == 1
        and result["asset_sha256"] == preview(repo, config)[1]
    )
    assert repo.publication_history("AED-0001") == []


def test_failed_prepare_cli_returns_sanitized_diagnostic(setup, monkeypatch, capsys):
    import sys

    from ask_every_day import render_next

    _, config = setup
    monkeypatch.setattr(
        render_next, "load_config", Mock(side_effect=ValueError("SYNTHETIC_PRIVATE"))
    )
    monkeypatch.setattr(render_next, "configure_logging", Mock())
    monkeypatch.setattr(sys, "argv", ["render-next"])
    with pytest.raises(SystemExit) as caught:
        render_next.main()
    assert caught.value.code == 1
    output = capsys.readouterr()
    assert "render_next_failed" in output.err and "SYNTHETIC_PRIVATE" not in output.err


@pytest.mark.parametrize("corruption", ["sequence", "adjacency"])
def test_inconsistent_success_metadata_fails_before_next_render(setup, corruption):
    repo, config = setup
    first = prepare(repo, config)
    success(repo)
    second = prepare(repo, config)
    success(repo, 1)
    data = json.loads(ledger_path(config).read_text())
    record = data["assets"]["AED-0002"]["assignment"]
    if corruption == "sequence":
        record["sequence"] = 1
    else:
        old_colour = first.assignment.background
        # Swap the bag values so its declared slot repeats the last success.
        bag = record["bag"]
        index = bag.index(old_colour)
        slot = record["slot"]
        bag[index], bag[slot] = bag[slot], bag[index]
    ledger_path(config).write_text(json.dumps(data))
    with pytest.raises(AssetError, match="palette_history_conflict"):
        prepare(repo, config)
    assert not (config.assets_path / "AED-0003.jpg").exists()
    assert second.assignment.sequence == 2


def test_fresh_processes_reuse_canonical_bytes_and_persisted_assignment(
    setup, tmp_path
):
    import subprocess
    import sys

    repo, config = setup
    settings = tmp_path / "local.toml"
    settings.write_text(
        "database_path=" + json.dumps(str(config.database_path)) + "\n"
        "assets_path=" + json.dumps(str(config.assets_path)) + "\n"
        'account_handle="@ask.every.day"\n'
    )
    command = [
        sys.executable,
        "-m",
        "ask_every_day.render_next",
        "--config",
        str(settings),
    ]
    first = subprocess.run(command, capture_output=True, text=True, check=True)
    before = (config.assets_path / "AED-0001.jpg").read_bytes()
    stamp = (config.assets_path / "AED-0001.jpg").stat().st_mtime_ns
    second = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert (config.assets_path / "AED-0001.jpg").read_bytes() == before
    assert (config.assets_path / "AED-0001.jpg").stat().st_mtime_ns == stamp
    assert preview(repo, config)[1] == json.loads(first.stdout)["asset_sha256"]
