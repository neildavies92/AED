"""Persistence contracts at the domain/SQLite boundary."""

import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
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
from ask_every_day.repository import (
    Repository,
    SQLiteRepository,
    from_microseconds,
    open_repository,
    to_microseconds,
)


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    return AppConfig(tmp_path / "aed.sqlite", tmp_path / "assets", "@ask.every.day")


@pytest.fixture
def repo(config: AppConfig) -> Iterator[Repository]:
    with open_repository(config) as repository:
        yield repository


@pytest.fixture
def post() -> Post:
    return Post("AED-0001", "Who are you?", "Identity", "Self", "Classic", "you")


def test_post_contract(repo: Repository, post: Post) -> None:
    assert repo.get_post(post.id) is None
    assert not repo.post_exists(post.id)
    with pytest.raises(KeyError):
        repo.update_post(post)
    repo.create_post(replace(post, id="AED-0002", highlight_text=None))
    repo.create_post(post)
    assert repo.get_post(post.id) == post
    assert repo.post_exists(post.id)
    assert [p.id for p in repo.list_posts()] == ["AED-0001", "AED-0002"]
    assert repo.get_post("AED-0002") == replace(
        post, id="AED-0002", highlight_text=None
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_post(post)
    updated = replace(post, prompt="Why now?", highlight_text="now")
    repo.update_post(updated)
    assert repo.get_post(post.id) == updated
    assert repo.delete_post(post.id)
    assert not repo.delete_post(post.id)
    assert repo.get_post("' OR 1=1 --") is None


def test_persistence_and_close(config: AppConfig, post: Post) -> None:
    with open_repository(config) as repository:
        repository.create_post(post)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        repository.list_posts()
    with open_repository(config) as second:
        assert second.get_post(post.id) == post
        with pytest.raises(sqlite3.IntegrityError):
            second.replace_queue([QueueEntry("AED-9999", 1)])
    with sqlite3.connect(config.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    assert not config.assets_path.exists()


def test_close_on_caller_failure(config: AppConfig) -> None:
    repository: SQLiteRepository | None = None
    with pytest.raises(RuntimeError):
        with open_repository(config) as repository:
            raise RuntimeError("Caller failed")
    assert repository is not None
    with pytest.raises(sqlite3.ProgrammingError):
        repository.list_posts()


def test_missing_directory_not_created(config: AppConfig) -> None:
    missing = config.database_path.parent / "missing"
    with pytest.raises(sqlite3.OperationalError):
        with open_repository(replace(config, database_path=missing / "aed.sqlite")):
            pytest.fail("Unexpected open")
    assert not missing.exists()


@pytest.mark.parametrize(
    "value",
    [
        datetime(1960, 1, 1, 0, 0, 0, 123456, UTC),
        datetime(2026, 6, 1, 0, 0, 0, 999999, timezone(timedelta(hours=5, minutes=30))),
        datetime(2026, 10, 25, 1, 30, 0, 123456, ZoneInfo("Europe/London"), fold=0),
        datetime(2026, 10, 25, 1, 30, 0, 123456, ZoneInfo("Europe/London"), fold=1),
        datetime.min.replace(tzinfo=UTC),
        datetime.max.replace(tzinfo=UTC),
    ],
)
def test_timestamp_precision(repo: Repository, post: Post, value: datetime) -> None:
    repo.create_post(post)
    attempt = Publication(post.id, PublicationStatus.PUBLISHING, value)
    record = repo.create_publication(attempt)
    stored = repo.get_publication(record.id)
    assert stored is not None
    assert stored.publication.attempted_at == value.astimezone(UTC)
    assert stored.publication.attempted_at.tzinfo is UTC
    assert from_microseconds(to_microseconds(value)) == value.astimezone(UTC)


def test_timestamp_boundaries() -> None:
    assert to_microseconds(datetime(1969, 12, 31, 23, 59, 59, 999999, UTC)) == -1
    with pytest.raises(ValueError, match="aware"):
        to_microseconds(datetime(2026, 1, 1))
    with pytest.raises(OverflowError):
        from_microseconds(2**63 - 1)


def test_publication_history_and_updates(repo: Repository, post: Post) -> None:
    repo.create_post(post)
    instant = datetime(2026, 1, 1, microsecond=123456, tzinfo=UTC)
    attempt = Publication(post.id, PublicationStatus.PUBLISHING, instant)
    later = repo.create_publication(
        replace(attempt, attempted_at=instant + timedelta(seconds=1))
    )
    first = repo.create_publication(attempt)
    tied = repo.create_publication(attempt)
    assert first.id != later.id
    assert repo.get_publication(first.id) == first
    assert [r.id for r in repo.publication_history(post.id)] == [
        first.id,
        tied.id,
        later.id,
    ]
    result = replace(
        attempt,
        status=PublicationStatus.PUBLISHED,
        published_at=instant + timedelta(microseconds=1),
        external_id="remote",
    )
    repo.update_publication(first.id, result)
    updated = repo.get_publication(first.id)
    assert updated is not None and updated.publication == result
    failed = replace(attempt, status=PublicationStatus.FAILED, error="timeout")
    repo.update_publication(tied.id, failed)
    assert repo.get_publication(tied.id) == replace(tied, publication=failed)
    assert repo.get_publication(999) is None
    assert repo.publication_history("AED-9999") == []
    with pytest.raises(KeyError):
        repo.update_publication(999, attempt)
    with pytest.raises(KeyError):
        repo.update_publication(first.id, replace(attempt, post_id="AED-9999"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_publication(replace(attempt, post_id="AED-9999"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.delete_post(post.id)
    assert repo.get_post(post.id) == post


def test_queue_contract_and_atomic_rollback(repo: Repository, post: Post) -> None:
    repo.create_post(post)
    repo.create_post(replace(post, id="AED-0002"))
    assert repo.next_queue_entry() is None
    entries = [QueueEntry("AED-0002", 10), QueueEntry(post.id, 2)]
    repo.replace_queue(entries)
    assert repo.list_queue() == entries[::-1]
    assert repo.next_queue_entry() == entries[1]
    for invalid in [
        [QueueEntry(post.id, 1), QueueEntry("AED-9999", 2)],
        [QueueEntry(post.id, 1), QueueEntry(post.id, 2)],
        [QueueEntry(post.id, 1), QueueEntry("AED-0002", 1)],
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            repo.replace_queue(invalid)
        assert repo.list_queue() == entries[::-1]
    assert repo.remove_queue_entry(post.id)
    assert not repo.remove_queue_entry(post.id)
    assert repo.next_queue_entry() == entries[0]
    repo.clear_queue()
    assert repo.list_queue() == []
    assert repo.next_queue_entry() is None


def test_logs_exclude_content(
    repo: Repository, post: Post, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        repo.create_post(post)
        with pytest.raises(sqlite3.IntegrityError):
            repo.create_post(post)
    events = [record.__dict__ for record in caplog.records]
    assert events[0]["event"] == "repository_write_committed"
    assert events[1]["event"] == "repository_write_failed"
    assert events[1]["error_type"] == "IntegrityError"
    assert post.prompt not in str(events)
    assert events[0]["post_id"] == post.id


def test_supplied_connection_foreign_keys() -> None:
    with sqlite3.connect(":memory:") as connection:
        repository = SQLiteRepository(connection)
        with pytest.raises(sqlite3.IntegrityError):
            repository.replace_queue([QueueEntry("AED-0001", 1)])


def test_initialization_failure_closes_connection(
    config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA user_version = 99")
    monkeypatch.setattr(sqlite3, "connect", lambda *args: connection)
    with pytest.raises(ValueError, match="Unsupported"):
        with open_repository(config):
            pytest.fail("Unsupported schema was opened")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_autocommit_connection_atomic_queue(post: Post) -> None:
    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        repository = SQLiteRepository(connection)
        repository.create_post(post)
        original = [QueueEntry(post.id, 5)]
        repository.replace_queue(original)
        with pytest.raises(sqlite3.IntegrityError):
            repository.replace_queue(
                [QueueEntry(post.id, 1), QueueEntry("AED-9999", 2)]
            )
        assert repository.list_queue() == original
        connection.execute("BEGIN")
        with pytest.raises(ValueError, match="caller transaction"):
            repository.clear_queue()
        assert connection.in_transaction
        connection.execute("ROLLBACK")
    finally:
        connection.close()
