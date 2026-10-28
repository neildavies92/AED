"""Local Docker probes cannot consume production credentials or publish media."""

import importlib.util
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

from ask_every_day import desktop_validation as validation
from ask_every_day.config import load_config
from ask_every_day.deployment import describe
from ask_every_day.scheduler_policy import digest, load_schedule
from tests.test_deployment import DATA
from tests.test_deployment import state as state

spec = importlib.util.spec_from_file_location(
    "aed_desktop", Path(__file__).resolve().parents[1] / "deployment/desktop.py"
)
assert spec and spec.loader
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def test_wrong_runtime_identity_never_accesses_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(validation.os, "geteuid", lambda: 501)
    backend = Mock()
    monkeypatch.setattr(validation, "FileSecretBackend", backend)
    with pytest.raises(ValueError, match="runtime_identity"):
        validation.storage_check(tmp_path, tmp_path)
    backend.assert_not_called()


@pytest.mark.skipif(os.geteuid() != 10001, reason="Actual Linux UID10001 acceptance")
def test_private_mount_roundtrip_and_permissive_mount_rejection(tmp_path):
    state, credentials = tmp_path / "state", tmp_path / "credentials"
    state.mkdir(mode=0o700)
    credentials.mkdir(mode=0o700)
    assert validation.storage_check(state, credentials)["private_mounts_valid"]
    assert list(credentials.iterdir()) == []
    credentials.chmod(0o755)
    with pytest.raises(ValueError, match="private_mount_required"):
        validation.storage_check(state, credentials)


def test_synthetic_staging_uses_only_generated_jpeg_and_closes(monkeypatch):
    observed = []

    @contextmanager
    def stage(repository, config, post_id):
        assert post_id == "AED-9999"
        assert repository.get_post(post_id).prompt == "Synthetic staging acceptance"
        assert (config.assets_path / "AED-9999.jpg").is_file()
        observed.append("opened")
        yield
        observed.append("closed")

    monkeypatch.setattr(validation, "stage_media", stage)
    assert validation.synthetic_staging()["instagram_requests"] is False
    assert observed == ["opened", "closed"]


def test_prepare_refuses_git_and_mutable_image(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(ValueError, match="outside_git"):
        host.prepare(Path("unused"), DATA, tmp_path / "runtime", "sha256:" + "a" * 64)
    with pytest.raises(ValueError, match="immutable_image"):
        host.prepare(Path("unused"), DATA, tmp_path.parent / "candidate", "latest")


def test_prepare_preserves_state_and_uses_1900_disabled_policy(
    state, tmp_path, monkeypatch
):
    from ask_every_day.deployment import config_text

    source = tmp_path / "source.toml"
    text = config_text(state).replace('"aed.sqlite3"', '"source.sqlite3"')
    source.write_text(text)
    monkeypatch.setattr(host.subprocess, "run", Mock())
    root = tmp_path / "desktop"
    host.prepare(source, DATA, root, "sha256:" + "a" * 64)
    assert digest(
        describe(load_config(root / "state/aed.toml"), root / "state/data")
    ) == digest(describe(state, DATA))
    assert not (root / "state/aed.sqlite3.schedule.json").exists()
    assert list((root / "credentials").iterdir()) == []
    assert load_schedule(root / "config/schedule.toml").hour == 19
    assert "ports:" not in (root / "compose.yaml").read_text()
    assert root.stat().st_mode & 0o777 == 0o700
