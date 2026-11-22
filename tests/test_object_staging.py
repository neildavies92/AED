"""Production staging and incident recovery boundaries using only synthetic state."""

import hashlib
import json
import socket
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.stub import Stubber

from ask_every_day import media_staging as tunnel
from ask_every_day import object_staging as origin
from ask_every_day.instagram_client import APIError, api_diagnostics
from ask_every_day.notifications import Notice
from ask_every_day.publication_recovery import snapshot
from ask_every_day.publish_next import PublishError
from ask_every_day.repository import open_repository
from tests.test_media_staging import Response
from tests.test_publish_next import NOW
from tests.test_publish_next import run as run

CFG = {
    "account_id": "1" * 32,
    "bucket": "aed-test",
    "access_key_id": "2" * 32,
    "secret_access_key": "3" * 64,
}


@pytest.fixture
def fake_time(monkeypatch):
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(
        tunnel, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )
    return clock


@pytest.mark.parametrize("exists", [True, False])
def test_conditional_put_and_two_signed_gets_use_same_bytes(
    monkeypatch, fake_time, exists
):
    from contextlib import closing

    with closing(origin._client(CFG)) as client, Stubber(client) as stub:
        key = "approved/AED-0013/digest.jpg"
        data = b"synthetic approved bytes"
        params = {
            "Bucket": CFG["bucket"],
            "Key": key,
            "Body": data,
            "ContentType": "image/jpeg",
            "CacheControl": "private, no-store",
            "IfNoneMatch": "*",
        }
        if exists:
            stub.add_client_error(
                "put_object",
                "PreconditionFailed",
                http_status_code=412,
                expected_params=params,
            )
        else:
            stub.add_response("put_object", {}, params)
        get = Mock(side_effect=lambda *args, **kwargs: Response(data))
        monkeypatch.setattr(tunnel, "build_opener", lambda *handlers: Mock(open=get))
        monkeypatch.setattr(origin, "time", tunnel.time)
        metrics = {}
        url = origin._object_operation(client, CFG["bucket"], key, data, metrics)
        assert metrics["origin_ready"] and metrics["attempt_count"] == 2
        assert fake_time == [3.0]
        assert [c.args[0].full_url for c in get.call_args_list] == [url] * 2
        parsed = urlsplit(url)
        assert parsed.hostname == CFG["account_id"] + ".r2.cloudflarestorage.com"
        assert parsed.path == "/aed-test/" + key
        assert parse_qs(parsed.query)["X-Amz-SignedHeaders"] == ["host"]
        assert parse_qs(parsed.query)["X-Amz-Expires"] == [str(25 * 3600)]
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("status", [403, 404, 503])
def test_permanent_resource_rejection_and_transient_outage(
    monkeypatch, fake_time, status
):
    verify = Mock(
        side_effect=tunnel._VerificationError("http_status", "rejected", status)
    )
    monkeypatch.setattr(tunnel, "_verify", verify)
    metrics = {}
    with pytest.raises(tunnel.StagingError):
        tunnel.wait_for_media("unused", b"jpeg", 0, 120, 10, metrics)
    assert metrics["http_status"] == status
    if status == 503:
        assert 1 < verify.call_count < 30 and fake_time == [120.0]
    else:
        assert verify.call_count == 1


def test_intermittent_recovery_needs_new_spaced_success_pair(monkeypatch, fake_time):
    verify = Mock(side_effect=[socket.gaierror(), None, TimeoutError(), None, None])
    monkeypatch.setattr(tunnel, "_verify", verify)
    metrics = {}
    tunnel.wait_for_media("unused", b"jpeg", 0, 120, 10, metrics)
    assert metrics["attempt_count"] == 5 and metrics["last_probe_failure"] == "timeout"
    assert metrics["origin_ready"] and fake_time[0] >= 6


def test_missing_secret_blocks_before_attempt_and_does_not_fallback(run, tmp_path):
    before = run.preview()
    with pytest.raises(PublishError) as caught:
        run.publish(stage=partial(origin.stage_approved, directory=tmp_path))
    assert caught.value.diagnostics["failure_category"] == "configuration"
    assert caught.value.diagnostics["media_post_dispatched"] is False
    assert caught.value.publication_state == "no_attempt"
    assert run.preview() == before
    assert run.repo.list_publications() == []
    run.client.create_image_container.assert_not_called()


@pytest.mark.parametrize("timeout", [True, False])
def test_staged_object_and_checkpoint_survive_ambiguous_create(
    run, monkeypatch, timeout
):
    plan = run.preview()
    data = plan.asset_path.read_bytes()
    key = f"approved/{plan.post.id}/{hashlib.sha256(data).hexdigest()}.jpg"
    request = Mock(
        return_value=(
            "https://example.test/test?private",
            {"origin_ready": True, "attempt_count": 2, "elapsed_ms": 3100},
        )
    )
    monkeypatch.setattr(origin, "_request", request)
    monkeypatch.setattr(origin, "_configuration", lambda _: CFG)
    if timeout:
        error = APIError("create_image_container", 400, True)
        error.diagnostics = api_diagnostics(
            {
                "error": {
                    "type": "OAuthException",
                    "code": -2,
                    "error_subcode": 2207003,
                    "message": "PRIVATE",
                }
            },
            400,
            "POST",
        )
        run.client.create_image_container.side_effect = error
        with pytest.raises(PublishError) as caught:
            run.publish(stage=origin.stage_approved)
        issue = caught.value
        assert issue.publication_state == "reconciliation_required"
        assert issue.diagnostics["failure_category"] == "media_download_timeout"
        assert issue.diagnostics["media_post_dispatched"] is True
        notice = Notice(
            "publication_reconciliation_required",
            NOW,
            "a" * 32,
            plan.post.id,
            issue.attempt_id,
            "create_intent",
            "media_download_timeout",
            400,
            -2,
            diagnostics=issue.diagnostics,
        )
        payload = json.dumps(notice.payload())
        for term in (
            "2207003",
            "-2",
            "create_image_container",
            "Public GETs",
            "create_intent",
        ):
            assert term in payload
        assert "PRIVATE" not in payload and "?private" not in payload
        assert issue.attempt_id is not None
        with open_repository(run.config, read_only=True) as repo:
            report = snapshot(repo, issue.attempt_id).report()
        assert report["checkpoint"] == "create_intent" and report["revision"] == 1
        assert report["remote_outcome_uncertain"] and not report["container_known"]
        request.assert_called_once_with(CFG, key, data)
        run.client.publish_container.assert_not_called()
    else:
        result = run.publish(stage=origin.stage_approved)
        assert snapshot(run.repo, result.attempt_id).report()["definitely_completed"]
        assert [c.args for c in request.call_args_list] == [
            (CFG, key, data),
            (CFG, key, None),
        ]


def test_digest_changed_blocks_before_upload(run, monkeypatch):
    request = Mock()
    monkeypatch.setattr(origin, "_request", request)
    with pytest.raises(origin.OriginError):
        with origin.stage_approved(
            run.repo, run.config, run.preview().post.id, expected_sha256="0" * 64
        ):
            pytest.fail("changed bytes became ready")
    request.assert_not_called()


@pytest.mark.parametrize(
    "key,value",
    [
        ("account_id", "x.example"),
        ("bucket", "../secret"),
        ("secret_access_key", ""),
        ("endpoint", "http://localhost"),
    ],
)
def test_origin_configuration_is_constrained(key, value):
    with pytest.raises(ValueError):
        origin.settings(json.dumps({**CFG, key: value}).encode())


def test_spawned_worker_rejects_incomplete_configuration():
    # Real spawned worker; malformed settings are rejected before any request.
    with pytest.raises(origin.OriginError) as caught:
        origin._request({}, "probe/test.jpg", b"jpeg")
    assert caught.value.diagnostics["failure_category"] == "configuration"
    assert caught.value.diagnostics["attempt_count"] == 0


def test_explicit_probe_preserves_primary_error_if_cleanup_also_fails(
    monkeypatch, tmp_path
):
    origin.configure(tmp_path, json.dumps(CFG).encode())
    primary = origin.OriginError(
        {"operation": "public_verify", "failure_category": "tls"}
    )
    request = Mock(
        side_effect=[primary, origin.OriginError({"operation": "object_cleanup"})]
    )
    monkeypatch.setattr(origin, "_request", request)
    with pytest.raises(origin.OriginError) as caught:
        origin.probe(tmp_path)
    assert caught.value is primary
    first, second = [c.args for c in request.call_args_list]
    assert first[1].startswith("probe/") and first[2].startswith(b"\xff\xd8")
    assert second == (first[0], first[1], None)
