"""Durable crash boundaries and explicit recovery use real commits and fake GETs."""

import json
import logging
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from ask_every_day.instagram_client import (
    ContainerStatus,
    MediaDetails,
    ResponseError,
    TransportError,
)
from ask_every_day.logging_config import JSONFormatter
from ask_every_day.publication_assets import AssetError, ledger_path
from ask_every_day.publication_checkpoints import CheckpointService
from ask_every_day.publication_recovery import (
    RecoveryError,
    ResponseEvidence,
    apply_recovery,
    load_response_evidence,
    plan_recovery,
    remote_inspection,
    snapshot,
)
from ask_every_day.publication_service import (
    InvalidTransition,
    PublicationService,
)
from ask_every_day.publish_next import PublishError
from ask_every_day.recovery_domain import (
    CheckpointPhase,
    PublicationCheckpoint,
    PublicationContext,
    Resolution,
)
from ask_every_day.repository import open_repository
from tests.test_publish_next import NOW, PRIVATE, URL
from tests.test_publish_next import run as run


def context(value) -> PublicationCheckpoint:
    checkpoint = value.checkpoint
    assert checkpoint is not None
    return checkpoint


def pending(run, phase=CheckpointPhase.PUBLISH_INTENT):
    plan = run.preview()
    attempt = PublicationService(run.repo).begin_publish(
        plan.post.id,
        NOW,
        context=PublicationContext("103", "ask.every.day", plan.asset_sha256),
    )
    service = CheckpointService(run.repo)
    if phase is not CheckpointPhase.PREPARED:
        service.create_intent(attempt.id, NOW)
    if phase in (CheckpointPhase.CONTAINER_KNOWN, CheckpointPhase.PUBLISH_INTENT):
        service.container_known(attempt.id, "104", NOW)
    if phase is CheckpointPhase.PUBLISH_INTENT:
        service.publish_intent(attempt.id, NOW)
    return snapshot(run.repo, attempt.id)


def proof(run, current, *, status=ContainerStatus.PUBLISHED, media=True):
    run.client.container_status.return_value = status
    run.client.media_details.return_value = MediaDetails("105", "103", "IMAGE", NOW)
    return remote_inspection(
        current,
        run.credentials,
        media_id="105" if media else None,
        client_factory=lambda _: run.client,
        clock=lambda: NOW,
    )


def response(current):
    return ResponseEvidence(
        current.record.id, "103", "104", "105", "captured_publish_response", "a" * 64
    )


def apply(run, plan, answer=None):
    return apply_recovery(
        run.repo,
        run.config,
        plan,
        plan.challenge if answer is None else answer,
        run.credentials,
        client_factory=lambda _: run.client,
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    "window,phase,container,media",
    [
        ("before_create_intent", "prepared", False, False),
        ("create_timeout", "create_intent", False, False),
        ("create_disconnect", "create_intent", False, False),
        ("create_bad_response", "create_intent", False, False),
        ("after_create_response", "create_intent", True, False),
        ("processing", "container_known", True, False),
        ("before_publish_intent", "container_known", True, False),
        ("publish_timeout", "publish_intent", True, False),
        ("publish_bad_response", "publish_intent", True, False),
        ("after_publish_response", "publish_intent", True, True),
    ],
)
def test_crash_windows_preserve_committed_context_and_never_replay(
    run, monkeypatch, window, phase, container, media
):
    if window == "before_create_intent":
        monkeypatch.setattr(
            CheckpointService, "create_intent", Mock(side_effect=KeyboardInterrupt)
        )
    elif window in ("create_timeout", "create_disconnect"):
        run.client.create_image_container.side_effect = TransportError(
            "create_image_container", None, True
        )
    elif window == "create_bad_response":
        run.client.create_image_container.side_effect = ResponseError(
            "create_image_container", 200, True
        )
    elif window == "after_create_response":
        monkeypatch.setattr(
            CheckpointService,
            "container_known",
            Mock(side_effect=sqlite3.OperationalError(PRIVATE)),
        )
    elif window == "processing":
        run.client.container_status.side_effect = KeyboardInterrupt
    elif window == "before_publish_intent":
        monkeypatch.setattr(
            CheckpointService,
            "publish_intent",
            Mock(side_effect=sqlite3.OperationalError(PRIVATE)),
        )
    elif window == "publish_timeout":
        run.client.publish_container.side_effect = TransportError(
            "publish_container", None, True
        )
    elif window == "publish_bad_response":
        run.client.publish_container.side_effect = ResponseError(
            "publish_container", 200, True
        )
    else:
        monkeypatch.setattr(
            run.repo,
            "update_publication",
            Mock(side_effect=sqlite3.OperationalError(PRIVATE)),
        )
    with pytest.raises(PublishError) as caught:
        run.publish()
    assert (
        bool(caught.value.container_id) == container
        and bool(caught.value.media_id) == media
    )
    assert caught.value.attempt_id is not None
    with open_repository(run.config, read_only=True) as reopened:
        current = snapshot(reopened, caught.value.attempt_id)
        assert current.record.publication.status.value == "Publishing"
        assert context(current).phase.value == phase
        assert (context(current).container_id is not None) == (
            phase in ("container_known", "publish_intent")
        )
        assert current.record.publication.external_id is None
    calls = list(run.client.mock_calls)
    with pytest.raises(PublishError, match="unresolved_publication"):
        run.publish()
    assert run.client.mock_calls == calls
    assert run.events == ["open", "closed"]
    assert len(run.repo.publication_history("AED-0002")) == 1


def test_intents_and_container_are_visible_before_remote_calls_without_write_locks(run):
    def check(expected):
        with open_repository(run.config) as other:
            assert context(snapshot(other, 1)).phase.value == expected
            with other.transaction():
                pass  # No SQLite writer is held across a network call.

    def create(*args, **kwargs):
        check("create_intent")
        from ask_every_day.instagram_client import Container

        return Container("104")

    def processing(*args):
        check("container_known")
        return ContainerStatus.FINISHED

    def publish(*args):
        check("publish_intent")
        from ask_every_day.instagram_client import PublishedMedia

        return PublishedMedia("105")

    run.client.create_image_container.side_effect = create
    run.client.container_status.side_effect = processing
    run.client.publish_container.side_effect = publish
    result = run.publish()
    current = snapshot(run.repo, result.attempt_id)
    assert context(current).phase is CheckpointPhase.COMPLETED
    assert (
        context(current).container_id == "104"
        and current.record.publication.external_id == "105"
    )
    assert (
        context(current).create_intent_at == context(current).publish_intent_at == NOW
    )
    assert context(current).resolution is Resolution.PUBLISH_RESPONSE
    assert context(current).revision == 4


def test_prepared_context_failure_rolls_back_attempt_and_queue(run, monkeypatch):
    monkeypatch.setattr(
        run.repo,
        "create_checkpoint",
        Mock(side_effect=sqlite3.OperationalError(PRIVATE)),
    )
    with pytest.raises(PublishError):
        run.publish()
    assert run.repo.publication_history("AED-0002") == []
    assert len(run.repo.list_queue()) == 2
    run.client.create_image_container.assert_not_called()


def test_completion_checkpoint_failure_rolls_back_media_id_and_published_atomically(
    run, monkeypatch
):
    original = run.repo.update_checkpoint

    def fail_completion(checkpoint):
        if checkpoint.phase is CheckpointPhase.COMPLETED:
            raise sqlite3.OperationalError(PRIVATE)
        return original(checkpoint)

    monkeypatch.setattr(run.repo, "update_checkpoint", fail_completion)
    with pytest.raises(PublishError) as caught:
        run.publish()
    assert caught.value.media_id == "105"
    current = snapshot(run.repo, 1)
    assert context(current).phase is CheckpointPhase.PUBLISH_INTENT
    assert (
        current.record.publication.status.value == "Publishing"
        and current.record.publication.external_id is None
    )


def test_cleanup_crash_after_success_cannot_undo_or_republish(run):
    @contextmanager
    def stage(*args, **kwargs):
        with run.stage(*args, **kwargs) as media:
            yield media
        raise KeyboardInterrupt

    with pytest.raises(PublishError):
        run.publish(stage=stage)
    current = snapshot(run.repo, 1)
    assert context(current).phase is CheckpointPhase.COMPLETED
    assert current.record.publication.status.value == "Published"
    assert run.preview().post.id == "AED-0001"
    run.client.publish_container.assert_called_once()


def test_read_only_inspection_and_published_mapping_are_not_implicit_recovery(run):
    current = pending(run)
    before = run.config.database_path.read_bytes()
    remote = proof(run, current)
    assert remote.container_status is ContainerStatus.PUBLISHED
    assert snapshot(run.repo, 1) == current
    assert run.config.database_path.read_bytes() == before
    with pytest.raises(RecoveryError, match="publish_mapping_required"):
        plan_recovery(current, remote, "published")
    run.client.create_image_container.assert_not_called()
    run.client.publish_container.assert_not_called()


def test_verified_operator_completion_is_atomic_and_preserves_palette(run):
    current = pending(run)
    before = ledger_path(run.config).read_bytes()
    remote = proof(run, current)
    plan = plan_recovery(current, remote, "published", response(current))
    apply(run, plan)
    with open_repository(run.config, read_only=True) as other:
        final = snapshot(other, 1)
        assert (
            final.record.publication.status.value == "Published"
            and final.record.publication.external_id == "105"
        )
        assert context(final).phase is CheckpointPhase.COMPLETED
        assert (
            context(final).evidence_sha256 == "a" * 64
            and context(final).resolution is Resolution.OPERATOR_RESPONSE
        )
    assert ledger_path(run.config).read_bytes() == before
    assert run.preview().palette_sequence == 2
    assert len(run.repo.list_queue()) == 1
    with pytest.raises(RecoveryError, match="stale_recovery_snapshot"):
        apply(run, plan)
    run.client.create_image_container.assert_not_called()
    run.client.publish_container.assert_not_called()


@pytest.mark.parametrize("answer", ["", "yes", "RECOVER PUBLISHED 1"])
def test_exact_confirmation_required_before_any_mutating_recovery(run, answer):
    current = pending(run)
    plan = plan_recovery(current, proof(run, current), "published", response(current))
    before = list(run.client.mock_calls)
    with pytest.raises(RecoveryError, match="recovery_confirmation_required"):
        apply(run, plan, answer)
    assert snapshot(run.repo, 1) == current and run.client.mock_calls == before


@pytest.mark.parametrize(
    "phase,status",
    [
        (CheckpointPhase.PREPARED, None),
        (CheckpointPhase.CONTAINER_KNOWN, ContainerStatus.ERROR),
        (CheckpointPhase.CONTAINER_KNOWN, ContainerStatus.EXPIRED),
    ],
)
def test_only_proven_pre_publish_failures_can_close_without_requeue(run, phase, status):
    current = pending(run, phase)
    remote = proof(run, current, status=status, media=False)
    plan = plan_recovery(current, remote, "failed")
    apply(run, plan)
    final = snapshot(run.repo, 1)
    assert final.record.publication.status.value == "Failed"
    assert context(final).phase is CheckpointPhase.CLOSED
    assert len(run.repo.list_queue()) == 1 and run.preview().palette_sequence == 1
    assert final.record.publication.post_id == "AED-0002"
    assert all(e.post_id != "AED-0002" for e in run.repo.list_queue())


@pytest.mark.parametrize(
    "phase,status",
    [
        (CheckpointPhase.CREATE_INTENT, None),
        (CheckpointPhase.PUBLISH_INTENT, ContainerStatus.ERROR),
        (CheckpointPhase.PUBLISH_INTENT, ContainerStatus.EXPIRED),
        (CheckpointPhase.PUBLISH_INTENT, ContainerStatus.FINISHED),
        (CheckpointPhase.PUBLISH_INTENT, ContainerStatus.PUBLISHED),
    ],
)
def test_post_uncertainty_is_never_converted_to_failed(run, phase, status):
    current = pending(run, phase)
    with pytest.raises(RecoveryError, match="remote_absence_not_proven"):
        plan_recovery(
            current, proof(run, current, status=status, media=False), "failed"
        )
    assert snapshot(run.repo, 1) == current


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", "999"),
        ("container_id", "999"),
        ("media_id", "999"),
        ("attempt_id", 99),
    ],
)
def test_captured_mapping_must_bind_exact_attempt_account_container_and_media(
    run, field, value
):
    current = pending(run)
    evidence = replace(response(current), **{field: value})
    with pytest.raises(RecoveryError, match="contradictory_response_mapping"):
        plan_recovery(current, proof(run, current), "published", evidence)


@pytest.mark.parametrize(
    "kind", ["lookup", "owner", "type", "time", "identity", "status"]
)
def test_unavailable_or_contradictory_remote_evidence_never_changes_local_state(
    run, kind
):
    current = pending(run)
    run.client.container_status.return_value = ContainerStatus.PUBLISHED
    run.client.media_details.return_value = MediaDetails("105", "103", "IMAGE", NOW)
    if kind == "lookup":
        run.client.container_status.side_effect = RuntimeError(PRIVATE)
    if kind == "owner":
        run.client.media_details.return_value = MediaDetails("105", "999", "IMAGE", NOW)
    if kind == "type":
        run.client.media_details.return_value = MediaDetails("105", "103", "VIDEO", NOW)
    if kind == "time":
        run.client.media_details.return_value = MediaDetails(
            "105", "103", "IMAGE", NOW + timedelta(days=1)
        )
    if kind == "identity":
        run.client.identify_account.return_value = Mock(
            user_id="999", username="wrong", account_type="Business"
        )
    if kind == "status":
        run.client.container_status.return_value = "UNKNOWN"
    with pytest.raises(RecoveryError) as caught:
        remote_inspection(
            current,
            run.credentials,
            media_id="105",
            client_factory=lambda _: run.client,
            clock=lambda: NOW,
        )
    assert caught.value.__context__ is None and PRIVATE not in str(caught.value)
    assert snapshot(run.repo, 1) == current


def test_conflicting_recovery_or_publish_intent_rejects_stale_approval(run):
    current = pending(run, CheckpointPhase.PREPARED)
    plan = plan_recovery(current, proof(run, current, media=False), "failed")
    with open_repository(run.config) as other:
        CheckpointService(other).create_intent(1, NOW)
    with pytest.raises(RecoveryError, match="stale_recovery_snapshot"):
        apply(run, plan)
    assert context(snapshot(run.repo, 1)).phase is CheckpointPhase.CREATE_INTENT


@pytest.mark.parametrize("corruption", ["missing", "digest", "sequence"])
def test_palette_disagreement_rolls_back_recovered_success(run, corruption):
    current = pending(run)
    plan = plan_recovery(current, proof(run, current), "published", response(current))
    path = ledger_path(run.config)
    if corruption == "missing":
        path.unlink()
    else:
        data = json.loads(path.read_text())
        asset = data["assets"]["AED-0002"]
        if corruption == "digest":
            asset["jpeg_sha256"] = "b" * 64
        else:
            asset["assignment"]["sequence"] = 9
        path.write_text(json.dumps(data))
    with pytest.raises(AssetError):
        apply(run, plan)
    assert snapshot(run.repo, 1) == current


def test_legacy_publishing_has_no_fabricated_identity_or_remote_lookup(run):
    record = PublicationService(run.repo).begin_publish("AED-0002", NOW)
    current = snapshot(run.repo, record.id)
    assert current.checkpoint is None and current.report()["remote_outcome_uncertain"]
    with pytest.raises(RecoveryError, match="legacy_missing_account_binding"):
        remote_inspection(
            current,
            run.credentials,
            client_factory=lambda _: run.client,
            clock=lambda: NOW,
        )
    run.credentials.local_credential.assert_not_called()
    assert run.client.mock_calls == []


def test_checkpoint_guards_preserve_history_and_prevent_invalid_transitions(run):
    current = pending(run)
    with pytest.raises(InvalidTransition):
        CheckpointService(run.repo).create_intent(1, NOW)
    with pytest.raises(InvalidTransition):
        PublicationService(run.repo).record_failure(1, "timeout")
    connection = run.repo._connection
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM publication_checkpoints")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE publication_checkpoints SET phase='prepared',create_intent_at=NULL,publish_intent_at=NULL,container_id=NULL,revision=revision+1"
        )
    connection.rollback()
    assert snapshot(run.repo, 1) == current


def test_recovery_snapshot_reopens_in_a_fresh_process_without_migration(run):
    pending(run)
    script = 'from pathlib import Path; import json; from ask_every_day.domain import AppConfig; from ask_every_day.repository import open_repository; from ask_every_day.publication_recovery import snapshot; import sys\nwith open_repository(AppConfig(Path(sys.argv[1]),Path(sys.argv[2]),"@ask.every.day"),read_only=True) as r: print(json.dumps(snapshot(r,1).report()))'
    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            script,
            str(run.config.database_path),
            str(run.config.assets_path),
        ],
        text=True,
    )
    assert json.loads(output)["checkpoint"] == "publish_intent"
    assert "103" not in output and PRIVATE not in output


def test_new_recovery_context_never_persists_or_logs_secrets_or_staging_urls(
    run, caplog
):
    run.client.publish_container.side_effect = RuntimeError(PRIVATE + URL)
    with caplog.at_level(logging.INFO), pytest.raises(PublishError):
        run.publish()
    dump = "\n".join(run.repo._connection.iterdump())
    logs = "\n".join(JSONFormatter().format(r) for r in caplog.records)
    assert PRIVATE not in dump + logs and URL not in dump + logs
    current = snapshot(run.repo, 1)
    assert context(current).phase is CheckpointPhase.PUBLISH_INTENT


@pytest.mark.parametrize(
    "data",
    [
        {"source": "caption_match"},
        {"attempt_id": True},
        {"media_id": 123},
        {"media_id": "１２３"},
        {"container_id": ""},
        {"account_id": "SYNTHETIC_PRIVATE"},
        {"access_token": PRIVATE},
    ],
)
def test_evidence_file_rejects_invalid_or_extra_fields(tmp_path, data):
    path = tmp_path / "evidence.json"
    payload = {
        "attempt_id": 1,
        "account_id": "103",
        "container_id": "104",
        "media_id": "105",
        "source": "captured_publish_response",
        **data,
    }
    path.write_text(json.dumps(payload))
    with pytest.raises(RecoveryError) as caught:
        load_response_evidence(path)
    assert caught.value.__context__ is None and PRIVATE not in str(caught.value)


def test_evidence_file_accepts_only_small_regular_structured_mapping(tmp_path):
    import hashlib

    path = tmp_path / "evidence.json"
    data = {
        "attempt_id": 1,
        "account_id": "103",
        "container_id": "104",
        "media_id": "105",
        "source": "captured_publish_response",
    }
    path.write_text(json.dumps(data))
    evidence = load_response_evidence(path)
    assert evidence.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(RecoveryError):
        load_response_evidence(link)
    path.write_text('{"attempt_id":1,"attempt_id":1}')
    with pytest.raises(RecoveryError):
        load_response_evidence(path)
    path.write_text("x" * 16385)
    with pytest.raises(RecoveryError):
        load_response_evidence(path)


def test_direct_restart_with_old_approved_plan_also_cannot_replay(run):
    from ask_every_day.publish_next import publish_approved

    old = run.preview()
    pending(run)
    with open_repository(run.config) as reopened, pytest.raises(PublishError):
        publish_approved(
            reopened,
            run.config,
            run.catalog,
            old,
            old.challenge,
            run.credentials,
            client_factory=lambda _: run.client,
            stage=run.stage,
            clock=lambda: NOW,
            sleep=Mock(),
        )
    assert run.client.mock_calls == []
    run.credentials.get_valid_credential.assert_not_called()


def test_inconsistent_checkpoint_blocks_queue_generation_and_inspection(run):
    from ask_every_day.queue_service import generate_and_store_queue

    current = pending(run)
    run.repo._connection.execute("UPDATE publications SET status='Failed' WHERE id=1")
    run.repo._connection.commit()
    queue = run.repo.list_queue()
    with pytest.raises(ValueError, match="checkpoint conflicts"):
        snapshot(run.repo, 1)
    with pytest.raises(ValueError, match="checkpoint conflicts"):
        generate_and_store_queue(run.repo, seed=42, regenerate=True)
    assert run.repo.list_queue() == queue
    assert context(current).phase is CheckpointPhase.PUBLISH_INTENT


def test_same_media_cannot_be_attached_to_another_attempt(run):
    from ask_every_day.domain import Publication, PublicationStatus

    current = pending(run)
    run.repo.create_publication(
        Publication(
            "AED-0003",
            PublicationStatus.PUBLISHED,
            NOW,
            published_at=NOW,
            external_id="105",
        )
    )
    with pytest.raises(InvalidTransition, match="already attached"):
        PublicationService(run.repo).record_success(1, NOW, "105")
    assert snapshot(run.repo, 1) == current


def test_sqlite_result_failure_during_recovery_preserves_durable_publish_intent(
    run, monkeypatch
):
    current = pending(run)
    plan = plan_recovery(current, proof(run, current), "published", response(current))
    monkeypatch.setattr(
        run.repo,
        "update_checkpoint",
        Mock(side_effect=sqlite3.OperationalError(PRIVATE)),
    )
    with pytest.raises(sqlite3.OperationalError):
        apply(run, plan)
    with open_repository(run.config, read_only=True) as reopened:
        assert snapshot(reopened, 1) == current
    run.client.publish_container.assert_not_called()


def test_changed_remote_evidence_rejects_recovery_without_local_mutation(run):
    current = pending(run)
    plan = plan_recovery(current, proof(run, current), "published", response(current))
    run.client.container_status.return_value = ContainerStatus.IN_PROGRESS
    with pytest.raises(RecoveryError, match="remote_evidence_changed"):
        apply(run, plan)
    assert snapshot(run.repo, 1) == current


def test_two_processes_cannot_hold_same_publication_lock_even_via_symlink(
    run, tmp_path
):
    script = 'from pathlib import Path; import sys; from ask_every_day.publish_next_cli import publication_lock\nwith publication_lock(Path(sys.argv[1])):\n print("LOCKED",flush=True)\n sys.stdin.readline()'
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(run.config.database_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None and process.stdin is not None
    try:
        assert process.stdout.readline().strip() == "LOCKED"
        alias = tmp_path / "alias.sqlite"
        alias.symlink_to(run.config.database_path)
        second = subprocess.run(
            [sys.executable, "-c", script, str(alias)],
            input="\n",
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert second.returncode != 0 and "another_publisher_running" in second.stderr
        process.stdin.write("\n")
        process.stdin.flush()
        process.communicate(timeout=5)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_concurrent_publishers_have_only_one_attempt_and_post_pair(run):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from ask_every_day.instagram_client import Container, PublishedMedia
    from ask_every_day.media_staging import StagedMedia
    from ask_every_day.publish_next import publish_approved

    barrier = Barrier(2)
    plan = run.preview()
    clients = []

    @contextmanager
    def stage(*args, **kwargs):
        barrier.wait(timeout=5)
        yield StagedMedia(plan.post.id, URL)

    def execute():
        client = Mock()
        client.identify_account.return_value = run.client.identify_account.return_value
        client.publishing_quota.return_value = run.client.publishing_quota.return_value
        client.create_image_container.return_value = Container("104")
        client.container_status.return_value = ContainerStatus.FINISHED
        client.publish_container.return_value = PublishedMedia("105")
        clients.append(client)
        with open_repository(run.config) as repository:
            try:
                return publish_approved(
                    repository,
                    run.config,
                    run.catalog,
                    plan,
                    plan.challenge,
                    run.credentials,
                    client_factory=lambda _: client,
                    stage=stage,
                    clock=lambda: NOW,
                    sleep=Mock(),
                )
            except PublishError:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert sum(c.create_image_container.call_count for c in clients) == 1
    assert sum(c.publish_container.call_count for c in clients) == 1
    assert len(run.repo.publication_history(plan.post.id)) == 1
    assert snapshot(run.repo, 1).record.publication.status.value == "Published"


def test_concurrent_reconciliation_cas_allows_only_one_committed_result(run):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    current = pending(run)
    plan = plan_recovery(current, proof(run, current), "published", response(current))
    barrier = Barrier(2)

    def execute():
        client = Mock()
        client.identify_account.return_value = run.client.identify_account.return_value
        client.container_status.return_value = ContainerStatus.PUBLISHED

        def media(_):
            barrier.wait(timeout=5)
            return MediaDetails("105", "103", "IMAGE", NOW)

        client.media_details.side_effect = media
        with open_repository(run.config) as repository:
            try:
                apply_recovery(
                    repository,
                    run.config,
                    plan,
                    plan.challenge,
                    run.credentials,
                    client_factory=lambda _: client,
                    clock=lambda: NOW,
                )
                return True
            except (RecoveryError, InvalidTransition):
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(), range(2)))
    assert results.count(True) == 1
    assert context(snapshot(run.repo, 1)).revision == 4
    assert len(run.repo.publication_history("AED-0002")) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"account_id": "abc"},
        {"account_id": 123},
        {"username": "other"},
        {"asset_sha256": "not-a-hash"},
        {"revision": True},
        {"publication_id": 0},
        {"phase": CheckpointPhase.PUBLISH_INTENT},
        {"phase": CheckpointPhase.CREATE_INTENT, "container_id": "104"},
        {"checkpoint_at": NOW.replace(tzinfo=None)},
    ],
)
def test_invalid_checkpoint_values_fail_closed(change):
    from ask_every_day.recovery_domain import PublicationCheckpoint

    with pytest.raises((ValueError, TypeError)):
        PublicationCheckpoint(
            **{
                "publication_id": 1,
                "account_id": "103",
                "username": "ask.every.day",
                "asset_sha256": "a" * 64,
                "phase": CheckpointPhase.PREPARED,
                "checkpoint_at": NOW,
                **change,
            }
        )
