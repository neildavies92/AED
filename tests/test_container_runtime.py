"""Container adapter is offline by default and never duplicates the publisher."""

import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ask_every_day import container_runtime as cli
from tests.test_publish_next import run as run


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        socket,
        "create_connection",
        Mock(side_effect=AssertionError("network forbidden")),
    )
    monkeypatch.setattr(cli, "configure_logging", Mock())


def test_default_preview_does_not_load_credentials_or_mutate_state(
    run, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "load_config", Mock(return_value=run.config))
    monkeypatch.setattr(cli.CopyCatalog, "load", Mock(return_value=run.catalog))
    manager = Mock(side_effect=AssertionError("credentials forbidden"))
    monkeypatch.setattr(cli, "credential_factory", manager)
    before = run.config.database_path.read_bytes()
    cli.main([])
    report = json.loads(capsys.readouterr().out)
    assert (
        report["mode"] == "local_read_only"
        and report["post_id"] == run.preview().post.id
    )
    assert report["asset_ready"]
    assert run.config.database_path.read_bytes() == before
    manager.assert_not_called()


def test_status_is_read_only_and_does_not_load_copy_or_credentials(
    run, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "load_config", Mock(return_value=run.config))
    monkeypatch.setattr(
        cli.CopyCatalog, "load", Mock(side_effect=AssertionError("copy not needed"))
    )
    cli.main(["publication-status"])
    assert json.loads(capsys.readouterr().out) == {
        "mode": "local_read_only",
        "attempts": [],
    }


def test_missing_scheduler_blocks_without_substitute_publishing(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.importlib,
        "import_module",
        Mock(side_effect=ModuleNotFoundError("SYNTHETIC_PRIVATE")),
    )
    with pytest.raises(SystemExit) as caught:
        cli.main(["schedule-run"])
    assert caught.value.code == 1
    assert "SYNTHETIC_PRIVATE" not in capsys.readouterr().err


def test_scheduler_adapter_forwards_exact_common_contract_and_injects_backend(
    monkeypatch,
):
    entry = Mock()
    monkeypatch.setattr(
        cli.importlib, "import_module", Mock(return_value=SimpleNamespace(main=entry))
    )
    factory = Mock(return_value="synthetic-manager")
    monkeypatch.setattr(cli, "credential_factory", factory)
    cli.main(["schedule-preview", "--credential-dir", "/private/synthetic"])
    args, kwargs = entry.call_args
    assert args[0] == [
        "preview",
        "--config",
        "/etc/aed/aed.toml",
        "--auth-config",
        "/etc/aed/instagram-auth.toml",
        "--schedule-config",
        "/etc/aed/schedule.toml",
        "--data-dir",
        "/opt/aed/data",
    ]
    assert kwargs["credential_factory"](Path("auth.toml")) == "synthetic-manager"
    factory.assert_called_once_with(Path("auth.toml"), Path("/private/synthetic"))


def test_credential_bootstrap_requires_interactive_operator_before_input(
    monkeypatch, tmp_path, capsys
):
    auth = tmp_path / "auth.toml"
    auth.write_text(
        'app_id="103"\nredirect_uri="https://localhost:8765/oauth/instagram/callback"\n'
    )
    hidden = Mock(side_effect=AssertionError("must not prompt"))
    monkeypatch.setattr(cli.getpass, "getpass", hidden)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        cli.main(
            [
                "credential-bootstrap",
                "--auth-config",
                str(auth),
                "--credential-dir",
                str(tmp_path / "absent"),
            ]
        )
    hidden.assert_not_called()
    assert not (tmp_path / "absent").exists()
    assert "SYNTHETIC" not in capsys.readouterr().err


def test_packaging_has_no_live_state_copy_or_automatic_scheduler():
    root = Path(__file__).parents[1]
    dockerfile = (root / "Dockerfile").read_text()
    assert "USER 10001:10001" in dockerfile and 'CMD ["preview"]' in dockerfile
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "COPY . " not in dockerfile and "COPY data/" in dockerfile
    assert "EXPOSE " not in dockerfile
    ignore = (root / ".dockerignore").read_text().splitlines()
    assert ignore[1] == "**"
    assert "!aed.toml" not in ignore and "!.env" not in ignore
    compose = (root / "container/compose.yaml").read_text()
    assert "read_only: true" in compose and "command: [preview]" in compose
    assert "create_host_path: false" in compose and "ports:" not in compose


def test_interactive_bootstrap_uses_hidden_validated_input_and_never_echoes_token(
    monkeypatch, tmp_path, capsys
):
    from dataclasses import asdict
    from datetime import timedelta

    from ask_every_day.file_credential_store import FileCredentialStore
    from tests.test_credential_store import credential

    auth = tmp_path / "auth.toml"
    auth.write_text(
        f'app_id="{credential().app_id}"\nredirect_uri="https://localhost:8765/oauth/instagram/callback"\n'
    )
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    model = credential()
    data = asdict(model)
    data.update(
        version=1,
        scopes=sorted(model.scopes),
        issued_at=model.issued_at.isoformat(),
        expires_at=model.expires_at.isoformat(),
    )
    monkeypatch.setattr(
        cli,
        "datetime",
        SimpleNamespace(now=lambda tz: model.issued_at + timedelta(days=1)),
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    hidden = Mock(return_value=json.dumps(data))
    monkeypatch.setattr(cli.getpass, "getpass", hidden)
    cli.main(
        [
            "credential-bootstrap",
            "--auth-config",
            str(auth),
            "--credential-dir",
            str(private),
        ]
    )
    output = capsys.readouterr()
    assert json.loads(output.out.splitlines()[-1])["credential_stored"]
    assert model.access_token not in output.out + output.err
    assert FileCredentialStore(model.app_id, private).load() == model
    hidden.assert_called_once()


def test_bad_interactive_import_preserves_prior_credential(
    monkeypatch, tmp_path, capsys
):
    from ask_every_day.file_credential_store import FileCredentialStore
    from tests.test_credential_store import credential

    auth = tmp_path / "auth.toml"
    auth.write_text(
        f'app_id="{credential().app_id}"\nredirect_uri="https://localhost:8765/oauth/instagram/callback"\n'
    )
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    store = FileCredentialStore(credential().app_id, private)
    store.save(credential())
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        cli.getpass,
        "getpass",
        Mock(return_value='{"version":1,"version":1,"token":"SYNTHETIC_PRIVATE"}'),
    )
    with pytest.raises(SystemExit):
        cli.main(
            [
                "credential-bootstrap",
                "--auth-config",
                str(auth),
                "--credential-dir",
                str(private),
            ]
        )
    output = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE" not in output.out + output.err
    assert store.load() == credential()


def test_scheduler_persistent_log_path_is_forwarded_without_new_policy(monkeypatch):
    entry = Mock()
    monkeypatch.setattr(
        cli.importlib, "import_module", Mock(return_value=SimpleNamespace(main=entry))
    )
    cli.main(["schedule-run", "--log-file", "/var/lib/aed/scheduler.jsonl"])
    assert entry.call_args.args[0][-2:] == [
        "--log-file",
        "/var/lib/aed/scheduler.jsonl",
    ]


def test_log_flag_cannot_silently_promise_persistence_for_other_commands():
    with pytest.raises(SystemExit) as caught:
        cli.main(["preview", "--log-file", "/var/lib/aed/scheduler.jsonl"])
    assert caught.value.code == 2
