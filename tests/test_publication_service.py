"""Local workflow validity, atomicity and history preservation."""

import logging
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ask_every_day.domain import (
    AppConfig,
    Post,
    Publication,
    PublicationStatus,
    QueueEntry,
)
from ask_every_day.publication_service import (
    InvalidTransition,
    PublicationService,
    PublicationState,
    publication_state,
    validate_transition,
)
from ask_every_day.repository import SQLiteRepository, open_repository

NOW = datetime(2026, 10, 14, 12, 0, 0, 123456, UTC)
POST = Post("AED-0001", "Who are you?", "Identity", "Self", "Classic")


@pytest.mark.parametrize("previous,target", list(product(PublicationState, repeat=2)))
def test_complete_transition_matrix(
    previous: PublicationState, target: PublicationState
) -> None:
    allowed = {
        ("Ready", "Queued"),
        ("Queued", "Publishing"),
        ("Publishing", "Published"),
        ("Publishing", "Failed"),
    }
    if (previous.value, target.value) in allowed:
        validate_transition(previous, target)
    else:
        with pytest.raises(InvalidTransition):
            validate_transition(previous, target)


@pytest.fixture
def repo():
    connection = sqlite3.connect(":memory:")
    repository = SQLiteRepository(connection)
    repository.create_post(POST)
    yield repository
    connection.close()


def test_success_path_and_terminal(repo: SQLiteRepository) -> None:
    service = PublicationService(repo)
    assert service.state(POST.id) is PublicationState.READY
    service.enqueue(QueueEntry(POST.id, 3))
    assert service.state(POST.id) is PublicationState.QUEUED
    with pytest.raises(InvalidTransition):
        service.enqueue(QueueEntry(POST.id, 4))
    record = service.begin_publish(POST.id, NOW)
    assert service.state(POST.id) is PublicationState.PUBLISHING
    assert repo.list_queue() == []
    with pytest.raises(InvalidTransition):
        service.begin_publish(POST.id, NOW)
    service.record_success(record.id, NOW + timedelta(microseconds=1), "external")
    assert service.state(POST.id) is PublicationState.PUBLISHED
    for operation in [
        lambda: service.record_success(record.id, NOW),
        lambda: service.record_failure(record.id, "secret"),
        lambda: service.enqueue(QueueEntry(POST.id, 1)),
        lambda: service.begin_publish(POST.id, NOW),
    ]:
        with pytest.raises(InvalidTransition):
            operation()
    assert len(repo.publication_history(POST.id)) == 1


def test_failure_terminal_and_safe_logs(
    repo: SQLiteRepository, caplog: pytest.LogCaptureFixture
) -> None:
    service = PublicationService(repo)
    service.enqueue(QueueEntry(POST.id, 1))
    record = service.begin_publish(POST.id, NOW)
    with caplog.at_level(logging.INFO):
        service.record_failure(record.id, "sensitive provider secret")
        with pytest.raises(InvalidTransition):
            service.begin_publish(POST.id, NOW)
    assert service.state(POST.id) is PublicationState.FAILED
    with pytest.raises(InvalidTransition):
        service.enqueue(QueueEntry(POST.id, 1))
    assert "sensitive provider secret" not in str([r.__dict__ for r in caplog.records])
    assert any(
        r.__dict__.get("event") == "publication_transition_committed"
        for r in caplog.records
    )
    assert len(repo.publication_history(POST.id)) == 1


def test_legacy_multiple_attempts_preserved(repo: SQLiteRepository) -> None:
    old = repo.create_publication(
        Publication(POST.id, PublicationStatus.FAILED, NOW, error="old")
    )
    current = repo.create_publication(
        Publication(POST.id, PublicationStatus.PUBLISHING, NOW + timedelta(seconds=1))
    )
    service = PublicationService(repo)
    service.record_success(current.id, NOW + timedelta(seconds=2))
    assert repo.get_publication(old.id) == old
    assert len(repo.publication_history(POST.id)) == 2
    assert service.state(POST.id) is PublicationState.PUBLISHED


@pytest.mark.parametrize(
    "statuses",
    [
        [PublicationStatus.PUBLISHING, PublicationStatus.FAILED],
        [PublicationStatus.PUBLISHING, PublicationStatus.PUBLISHING],
        [PublicationStatus.PUBLISHED, PublicationStatus.FAILED],
        [PublicationStatus.PUBLISHED, PublicationStatus.PUBLISHING],
    ],
)
def test_ambiguous_history_fails_closed(
    repo: SQLiteRepository, statuses: list[PublicationStatus]
) -> None:
    for status in statuses:
        repo.create_publication(
            Publication(
                POST.id,
                status,
                NOW,
                published_at=NOW if status is PublicationStatus.PUBLISHED else None,
            )
        )
    with pytest.raises(InvalidTransition):
        PublicationService(repo).state(POST.id)


def test_invalid_chronology_and_queue_conflict(repo: SQLiteRepository) -> None:
    old = repo.create_publication(Publication(POST.id, PublicationStatus.FAILED, NOW))
    new = repo.create_publication(
        Publication(POST.id, PublicationStatus.FAILED, NOW + timedelta(seconds=1))
    )
    for history in [[new, old], [old, old]]:
        with pytest.raises(InvalidTransition):
            publication_state(history, queued=False)
    repo.replace_queue([QueueEntry(POST.id, 1)])
    with pytest.raises(InvalidTransition):
        PublicationService(repo).state(POST.id)


def test_unknown_post_and_attempt(repo: SQLiteRepository) -> None:
    service = PublicationService(repo)
    for operation in [
        lambda: service.state("AED-9999"),
        lambda: service.enqueue(QueueEntry("AED-9999", 1)),
        lambda: service.begin_publish("AED-9999", NOW),
        lambda: service.record_success(999, NOW),
        lambda: service.record_failure(999),
    ]:
        with pytest.raises(KeyError):
            operation()
    assert repo.publication_history(POST.id) == []
    with pytest.raises(InvalidTransition):
        service.begin_publish(POST.id, NOW)


def test_timestamp_validation_and_dst(repo: SQLiteRepository) -> None:
    service = PublicationService(repo)
    service.enqueue(QueueEntry(POST.id, 1))
    with pytest.raises(ValueError):
        service.begin_publish(POST.id, datetime(2026, 1, 1))
    assert service.state(POST.id) is PublicationState.QUEUED
    london = ZoneInfo("Europe/London")
    start = datetime(2026, 10, 25, 1, 50, tzinfo=london, fold=0)
    end = datetime(2026, 10, 25, 1, 10, tzinfo=london, fold=1)
    record = service.begin_publish(POST.id, start)
    with pytest.raises(ValueError):
        service.record_success(record.id, start - timedelta(seconds=1))
    assert service.state(POST.id) is PublicationState.PUBLISHING
    service.record_success(record.id, end)
    stored = repo.get_publication(record.id)
    assert stored is not None and stored.publication.published_at == end.astimezone(UTC)


def test_atomic_begin_failure(
    repo: SQLiteRepository,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = PublicationService(repo)
    service.enqueue(QueueEntry(POST.id, 1))

    def fail(post_id: str) -> bool:
        raise sqlite3.OperationalError("private provider payload")

    monkeypatch.setattr(repo, "remove_queue_entry", fail)
    with caplog.at_level(logging.INFO), pytest.raises(sqlite3.OperationalError):
        service.begin_publish(POST.id, NOW)
    assert repo.publication_history(POST.id) == []
    assert service.state(POST.id) is PublicationState.QUEUED
    assert "private provider payload" not in caplog.text
    assert not any(
        r.__dict__.get("event") == "publication_transition_committed"
        for r in caplog.records
    )


def test_transaction_caught_failure_rolls_back(repo: SQLiteRepository) -> None:
    with pytest.raises(RuntimeError, match="rollback"):
        with repo.transaction():
            repo.create_post(replace(POST, id="AED-0002"))
            with pytest.raises(sqlite3.IntegrityError):
                repo.create_post(POST)
    assert not repo.post_exists("AED-0002")
    with repo.transaction():
        with pytest.raises(ValueError, match="Nested"):
            with repo.transaction():
                pass


def test_independent_connections_observe_terminal_state(tmp_path: Path) -> None:
    config = AppConfig(tmp_path / "aed.sqlite", tmp_path, "@ask.every.day")
    with open_repository(config) as first, open_repository(config) as second:
        first.create_post(POST)
        one, two = PublicationService(first), PublicationService(second)
        one.enqueue(QueueEntry(POST.id, 1))
        record = one.begin_publish(POST.id, NOW)
        with pytest.raises(InvalidTransition):
            two.begin_publish(POST.id, NOW)
        one.record_success(record.id, NOW)
        with pytest.raises(InvalidTransition):
            two.record_failure(record.id)
        assert len(second.publication_history(POST.id)) == 1


def test_enqueue_collision_and_result_failure(
    repo: SQLiteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.create_post(replace(POST, id="AED-0002"))
    service = PublicationService(repo)
    service.enqueue(QueueEntry("AED-0002", 1))
    with pytest.raises(sqlite3.IntegrityError):
        service.enqueue(QueueEntry(POST.id, 1))
    assert service.state(POST.id) is PublicationState.READY
    assert repo.list_queue() == [QueueEntry("AED-0002", 1)]
    service.enqueue(QueueEntry(POST.id, 2))
    record = service.begin_publish(POST.id, NOW)

    def fail(publication_id: int, publication: Publication) -> None:
        raise sqlite3.OperationalError("sensitive")

    monkeypatch.setattr(repo, "update_publication", fail)
    with pytest.raises(sqlite3.OperationalError):
        service.record_success(record.id, NOW)
    assert repo.get_publication(record.id) == record


def test_failed_attempt_cannot_receive_another_result(repo: SQLiteRepository) -> None:
    service = PublicationService(repo)
    service.enqueue(QueueEntry(POST.id, 1))
    record = service.begin_publish(POST.id, NOW)
    service.record_failure(record.id)
    for operation in [
        lambda: service.record_success(record.id, NOW),
        lambda: service.record_failure(record.id),
    ]:
        with pytest.raises(InvalidTransition):
            operation()
    assert len(repo.publication_history(POST.id)) == 1
