"""Canonical manual acceptance with synthetic credentials/provider and real state."""

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from ask_every_day import canonical_renderer as canonical
from ask_every_day import (
    container_runtime,
    publication_assets,
    publish_next_cli,
    scheduler_policy,
)
from ask_every_day.publication_service import PublicationService
from ask_every_day.recovery_domain import PublicationContext
from tests.test_publish_next import NOW
from tests.test_publish_next import run as run


@pytest.fixture
def fonts(monkeypatch):
    expected = json.loads(
        Path(canonical.__file__).with_name("editorial-fonts.json").read_text()
    )
    rows = [{**row, "exact_family": True, "style_matches": True} for row in expected]
    monkeypatch.setattr(canonical, "docker_runtime", lambda: True)
    monkeypatch.setattr(canonical, "inventory", lambda: rows)
    return rows


def test_native_renderer_is_rejected_before_font_or_credential_access(monkeypatch):
    monkeypatch.setattr(canonical, "docker_runtime", lambda: False)
    inventory = Mock()
    monkeypatch.setattr(canonical, "inventory", inventory)
    with pytest.raises(canonical.CanonicalRendererError, match="docker_renderer"):
        canonical.verify_editorial_fonts()
    inventory.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "0" * 64),
        ("fontconfig_version", "1"),
        ("style_matches", False),
        ("exact_family", False),
    ],
)
def test_font_substitution_or_version_drift_fails_closed(fonts, field, value):
    fonts[0][field] = value
    with pytest.raises(canonical.CanonicalRendererError, match="font_files"):
        canonical.verify_editorial_fonts()


@pytest.mark.skipif(
    sys.platform != "linux" or not Path("/.dockerenv").is_file(),
    reason="Real approved Docker font files",
)
def test_actual_approved_docker_font_files():
    assert len(canonical.verify_editorial_fonts()) == 8


def test_font_manifest_change_invalidates_scheduler_authorization(
    tmp_path, monkeypatch
):
    module = tmp_path / "scheduler_policy.py"
    module.write_text("# synthetic runtime")
    manifest = tmp_path / "editorial-fonts.json"
    manifest.write_text("[]")
    monkeypatch.setattr(scheduler_policy, "__file__", str(module))
    monkeypatch.setattr("ask_every_day.font_review.inventory", lambda: [])
    before = scheduler_policy.runtime_key()
    manifest.write_text('[{"sha256":"changed"}]')
    assert scheduler_policy.runtime_key() != before


def make_wrong_raster(run, monkeypatch):
    image = BytesIO()
    Image.new("RGB", (1080, 1350), "pink").save(image, format="PNG")
    with monkeypatch.context() as patch:
        patch.setattr(publication_assets, "export_png", lambda *a: image.getvalue())
        publication_assets.prepare_next_asset(run.repo, run.config, verify_raster=True)


def test_wrong_font_cache_is_rejected_and_only_unattempted_draft_repaired(
    run, monkeypatch
):
    run.publish()
    old = run.config.assets_path / "AED-0002.jpg"
    old_bytes = old.read_bytes()
    make_wrong_raster(run, monkeypatch)
    plan = run.preview()
    assert plan.asset_sha256 is not None  # Old SVG/hash-only check would accept it.
    assert not canonical.canonical_asset_valid(run.repo, run.config, plan)
    ledger = publication_assets.read_ledger(run.config)
    assert ledger is not None
    assignment = ledger.assets[plan.post.id].assignment
    publication_assets.prepare_next_asset(run.repo, run.config, verify_raster=True)
    repaired = run.preview()
    assert canonical.canonical_asset_valid(run.repo, run.config, repaired)
    assert repaired.asset_sha256 != plan.asset_sha256
    ledger = publication_assets.read_ledger(run.config)
    assert ledger is not None and ledger.assets[plan.post.id].assignment == assignment
    assert old.read_bytes() == old_bytes
    assert (
        run.repo.publication_history("AED-0002")[0].publication.status.value
        == "Published"
    )


def test_unresolved_asset_is_never_regenerated(run, monkeypatch):
    plan = run.preview()
    PublicationService(run.repo).begin_publish(
        plan.post.id,
        NOW,
        context=PublicationContext("103", "ask.every.day", plan.asset_sha256),
    )
    before = plan.asset_path.read_bytes()
    ledger = publication_assets.ledger_path(run.config).read_bytes()
    with pytest.raises(publication_assets.AssetError):
        publication_assets.prepare_next_asset(run.repo, run.config, verify_raster=True)
    assert plan.asset_path.read_bytes() == before
    assert publication_assets.ledger_path(run.config).read_bytes() == ledger


def setup_cli(run, monkeypatch):
    monkeypatch.setattr(publish_next_cli, "load_config", lambda _: run.config)
    monkeypatch.setattr(publish_next_cli.CopyCatalog, "load", lambda _: run.catalog)
    monkeypatch.setattr(publish_next_cli, "configure_logging", Mock())


def test_manual_preview_is_read_only_and_never_loads_credentials(
    run, fonts, monkeypatch, capsys
):
    setup_cli(run, monkeypatch)
    factory = Mock(side_effect=AssertionError("credentials forbidden"))
    before = run.config.database_path.read_bytes()
    ledger = publication_assets.ledger_path(run.config).read_bytes()
    publish_next_cli.main([], credential_factory=factory, canonical_assets=True)
    report = json.loads(capsys.readouterr().out)
    assert report["canonical_renderer"] and report["asset_valid"]
    assert report["caption"] == run.preview().caption
    assert (
        report["asset_sha256"]
        == hashlib.sha256(run.preview().asset_path.read_bytes()).hexdigest()
    )
    assert len(report["fonts"]) == 8
    assert run.config.database_path.read_bytes() == before
    assert publication_assets.ledger_path(run.config).read_bytes() == ledger
    factory.assert_not_called()


def test_wrong_cached_preview_requires_explicit_prepare(
    run, fonts, monkeypatch, capsys
):
    setup_cli(run, monkeypatch)
    make_wrong_raster(run, monkeypatch)
    before = run.preview().asset_path.read_bytes()
    publish_next_cli.main([], credential_factory=Mock(), canonical_assets=True)
    report = json.loads(capsys.readouterr().out)
    assert not report["asset_valid"] and report["asset_sha256"] is None
    assert report["asset_action"] == "run_container_prepare_next"
    assert run.preview().asset_path.read_bytes() == before


def test_wrong_cached_confirmation_blocks_before_credentials(run, fonts, monkeypatch):
    setup_cli(run, monkeypatch)
    make_wrong_raster(run, monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    factory = Mock()
    before = run.preview().asset_path.read_bytes()
    with pytest.raises(SystemExit):
        publish_next_cli.main(
            ["--confirm"], credential_factory=factory, canonical_assets=True
        )
    factory.assert_not_called()
    assert not run.repo.list_publications()
    assert run.preview().asset_path.read_bytes() == before


def test_manual_confirmation_uses_existing_engine_outside_schedule_window(
    run, fonts, monkeypatch, capsys
):
    setup_cli(run, monkeypatch)
    factory = Mock(return_value=run.credentials)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: run.preview().challenge)
    original = publish_next_cli.publish_approved
    monkeypatch.setattr(
        publish_next_cli,
        "publish_approved",
        lambda *args, **kwargs: original(
            *args,
            **kwargs,
            client_factory=lambda _: run.client,
            stage=run.stage,
            clock=lambda: NOW,
            sleep=Mock(),
        ),
    )
    # NOW is 21:00: manual approval is independent of the 20:00–20:30 window.
    publish_next_cli.main(
        ["--confirm"], credential_factory=factory, canonical_assets=True
    )
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["state"] == "Published" and result["media_id"] == "105"
    assert run.repo.get_checkpoint(result["attempt_id"]).phase.value == "completed"
    assert run.repo.next_queue_entry().post_id == "AED-0001"
    assert run.events == ["open", "closed"]
    assert not run.config.database_path.with_name(
        run.config.database_path.name + ".schedule.json"
    ).exists()


def test_prompt_time_font_drift_blocks_before_post(run, fonts, monkeypatch):
    setup_cli(run, monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def changed(_):
        fonts[0]["sha256"] = "0" * 64
        return run.preview().challenge

    monkeypatch.setattr("builtins.input", changed)
    with pytest.raises(SystemExit):
        publish_next_cli.main(
            ["--confirm"],
            credential_factory=Mock(return_value=run.credentials),
            canonical_assets=True,
        )
    run.client.create_image_container.assert_not_called()
    run.client.publish_container.assert_not_called()


def test_container_manual_boundary_injects_file_backend(fonts, monkeypatch):
    monkeypatch.setattr(container_runtime, "configure_logging", Mock())
    entry = Mock()
    monkeypatch.setattr(publish_next_cli, "main", entry)
    factory = Mock(return_value="file-backend")
    monkeypatch.setattr(container_runtime, "credential_factory", factory)
    container_runtime.main(["manual-publish"])
    args, kwargs = entry.call_args
    assert "--confirm" in args[0] and "/var/lib/aed/manual.jsonl" in args[0]
    assert kwargs["canonical_assets"] is True
    assert kwargs["credential_factory"](Path("auth.toml")) == "file-backend"
    factory.assert_called_once_with(Path("auth.toml"), Path("/var/lib/aed-credentials"))


def test_manual_non_tty_refuses_before_credentials(run, fonts, monkeypatch):
    setup_cli(run, monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    factory = Mock()
    with pytest.raises(SystemExit):
        publish_next_cli.main(
            ["--confirm"], credential_factory=factory, canonical_assets=True
        )
    factory.assert_not_called()
    assert not run.repo.list_publications()
