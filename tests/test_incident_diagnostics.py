"""Offline regressions for the October 31 unattended preflight incident."""

import json
import logging
from unittest.mock import Mock

import pytest

from ask_every_day import scheduler_cli
from ask_every_day.instagram_client import APIError, InstagramClient, api_diagnostics
from ask_every_day.logging_config import JSONFormatter
from ask_every_day.publish_next import PublishError
from ask_every_day.repository import open_repository
from tests.test_publish_next import run as run


@pytest.mark.parametrize(
    "status,code,category",
    [
        (400, 190, "authentication"),
        (400, 100, "configuration"),
        (403, 200, "configuration"),
        (429, 4, "transient"),
        (503, 2, "transient"),
        (400, 999, "api_rejected"),
    ],
)
def test_meta_errors_are_safe_typed_and_never_retried(status, code, category, caplog):
    token = "SYNTHETIC_PRIVATE_SECRET"
    transport = Mock(
        return_value=(
            status,
            json.dumps(
                {
                    "error": {
                        "code": code,
                        "error_subcode": 463,
                        "type": "OAuthException",
                        "message": token,
                        "error_data": {"url": token},
                    }
                }
            ).encode(),
        )
    )
    with caplog.at_level(logging.WARNING), pytest.raises(APIError) as caught:
        InstagramClient(token, transport=transport).identify_account()
    error = caught.value
    assert error.http_status == status and not error.outcome_uncertain
    assert error.diagnostics["failure_category"] == category
    assert error.diagnostics["meta_error_code"] == code
    assert error.diagnostics["meta_error_subcode"] == 463
    assert error.__context__ is None
    assert token not in repr(error.diagnostics) + str(error) + "".join(
        JSONFormatter().format(r) for r in caplog.records
    )
    assert transport.call_count == 1


def test_untrusted_metadata_and_post_uncertainty():
    d = api_diagnostics(
        {
            "error": {
                "code": True,
                "error_subcode": "PRIVATE",
                "type": ["PRIVATE"],
                "message": "PRIVATE",
            }
        },
        400,
        "POST",
    )
    assert d["meta_error_code"] is None and d["meta_error_subcode"] is None
    assert d["meta_error_type"] == "UnknownProviderError"
    assert d["retryability"] == "reconcile_before_retry"
    assert "PRIVATE" not in repr(d)


def test_identity_rejection_preserves_queue_palette_and_checkpoints(run):
    plan = run.preview()
    before = run.config.database_path.read_bytes()
    palette = run.config.database_path.with_name(
        run.config.database_path.name + ".palette.json"
    )
    ledger = palette.read_bytes()
    failure = APIError("identify_account", 400, False)
    failure.diagnostics = api_diagnostics({"error": {"code": 190}}, 400, "GET")
    run.client.identify_account.side_effect = failure
    with pytest.raises(PublishError) as caught:
        run.publish()
    assert caught.value.attempt_id is None
    assert caught.value.diagnostics["meta_error_code"] == 190
    assert run.repo.list_publications() == [] and run.events == []
    assert run.preview() == plan and palette.read_bytes() == ledger
    assert run.config.database_path.read_bytes() == before
    run.client.create_image_container.assert_not_called()


def test_read_only_transaction_reports_read_without_database_writes(run, caplog):
    before = run.config.database_path.read_bytes()
    with (
        caplog.at_level(logging.INFO),
        open_repository(run.config, read_only=True) as repo,
    ):
        with repo.transaction():
            repo.list_publications()
    assert any(
        getattr(r, "event", None) == "repository_read_completed" for r in caplog.records
    )
    assert not any(
        getattr(r, "event", None) == "repository_write_committed"
        for r in caplog.records
    )
    assert run.config.database_path.read_bytes() == before


def test_cli_failure_envelope_preserves_preflight_certainty(run, monkeypatch, capsys):
    monkeypatch.setattr(scheduler_cli, "configure_logging", Mock())
    monkeypatch.setattr(
        scheduler_cli, "read_authorization", Mock(return_value=Mock(enabled=True))
    )
    failure = PublishError("preflight_failed")
    failure.diagnostics = {
        "http_status": 400,
        "failure_category": "authentication",
        "meta_error_code": 190,
    }
    monkeypatch.setattr(scheduler_cli, "load_config", lambda _: run.config)
    monkeypatch.setattr(scheduler_cli, "load_inputs", Mock())
    monkeypatch.setattr(scheduler_cli, "load_schedule", Mock())
    monkeypatch.setattr(scheduler_cli, "run_daily", Mock(side_effect=failure))
    with pytest.raises(SystemExit):
        scheduler_cli.main(["run"])
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "blocked" and result["schema_version"] == 1
    assert result["publication_state"] == "no_attempt" and result["local_day"]
    assert result["diagnostics"]["meta_error_code"] == 190


def test_credential_manager_keeps_meta_diagnostics_without_refresh(run):
    from ask_every_day.instagram_auth import AuthError, CredentialManager

    manager = object.__new__(CredentialManager)
    manager.local_credential = Mock(return_value=run.credentials.local_credential())
    failure = APIError("identify_account", 400, False)
    failure.diagnostics = api_diagnostics(
        {"error": {"code": 190, "error_subcode": 463}}, 400, "GET"
    )
    manager._identify = Mock(side_effect=failure)
    with pytest.raises(AuthError) as caught:
        manager.verify_identity_read_only()
    assert caught.value.diagnostics["meta_error_code"] == 190
    assert caught.value.diagnostics["http_status"] == 400
    assert caught.value.__context__ is None
    run.credentials.get_valid_credential.side_effect = caught.value
    with pytest.raises(PublishError) as published:
        run.publish()
    assert published.value.diagnostics["http_status"] == 400
    assert run.repo.list_publications() == []


def test_missing_attempt_id_after_begin_failure_is_not_proof_of_no_attempt():
    assert PublishError("begin_attempt_failed").publication_state == "unknown"
    assert (
        PublishError("publish_container_failed", attempt_id=2).publication_state
        == "reconciliation_required"
    )
