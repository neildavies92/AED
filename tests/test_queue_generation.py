"""Deterministic complete queues, bounded search and transactional selection."""

import csv
import logging
import random
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ask_every_day.domain import Post, Publication, PublicationStatus, QueueEntry
from ask_every_day.publication_service import InvalidTransition
from ask_every_day.queue_generation import QueueGenerationError, generate_queue
from ask_every_day.queue_rules import QueueRules, validate_sequence
from ask_every_day.queue_service import ExistingQueueError, generate_and_store_queue
from ask_every_day.repository import SQLiteRepository


def posts_for(pairs: list[tuple[str, str]]) -> list[Post]:
    return [
        Post(f"AED-{i:04}", "Private prompt body?", category, subcategory, "Classic")
        for i, (category, subcategory) in enumerate(pairs, 1)
    ]


@pytest.fixture
def library() -> list[Post]:
    path = Path(__file__).resolve().parents[1] / "data/prompts.csv"
    with path.open(newline="", encoding="utf-8") as stream:
        return [
            Post(
                row["id"],
                row["prompt"],
                row["category"],
                row["subcategory"],
                row["template"],
                row["highlight_text"] or None,
            )
            for row in csv.DictReader(stream)
        ]


@pytest.mark.parametrize("seed", [0, 1, 2, 42, 2026, -17, 99999])
def test_full_library(library: list[Post], seed: int) -> None:
    ordered = generate_queue(library, seed=seed)
    assert len(ordered) == 400
    assert {post.id for post in ordered} == {post.id for post in library}
    validate_sequence(ordered)
    assert ordered == generate_queue(list(reversed(library)), seed=seed)


def test_different_seeds_and_no_global_randomness(library: list[Post]) -> None:
    before = random.getstate()
    assert generate_queue(library, seed=1) != generate_queue(library, seed=2)
    assert random.getstate() == before


def test_empty_single_and_taxonomy_pair() -> None:
    assert generate_queue([], seed=0) == []
    posts = posts_for([("Growth", "Change"), ("Life Audit", "Change")])
    assert generate_queue(posts[:1], seed=0) == posts[:1]
    assert set(generate_queue(posts, seed=0)) == set(posts)


def test_exact_category_boundary() -> None:
    posts = posts_for([("A", "a"), ("A", "b"), ("B", "c")])
    ordered = generate_queue(posts, seed=0, rules=QueueRules(1, 0))
    assert [post.category for post in ordered] == ["A", "B", "A"]
    with pytest.raises(QueueGenerationError, match="impossible"):
        generate_queue(posts, seed=0, rules=QueueRules(2, 0))


def test_exact_pair_boundary() -> None:
    posts = posts_for([("A", "a"), ("A", "a"), ("B", "b"), ("C", "c"), ("D", "d")])
    ordered = generate_queue(posts, seed=4, rules=QueueRules(1, 3))
    positions = [i for i, post in enumerate(ordered) if post.category == "A"]
    assert positions == [0, 4]
    with pytest.raises(QueueGenerationError, match="impossible"):
        generate_queue(posts, seed=4, rules=QueueRules(1, 4))


def test_duplicate_and_impossible_input() -> None:
    posts = posts_for([("A", "a"), ("A", "a")])
    with pytest.raises(QueueGenerationError, match="Duplicate"):
        generate_queue([posts[0], posts[0]], seed=42)
    with pytest.raises(QueueGenerationError) as failure:
        generate_queue(posts, seed=42)
    assert failure.value.total == 2
    assert failure.value.attempts == 0
    assert "Private prompt" not in str(failure.value)


def test_bounded_search_exhaustion() -> None:
    posts = posts_for(
        [("B", "y"), ("A", "x"), ("A", "x"), ("B", "x"), ("C", "y"), ("C", "y")]
    )
    with pytest.raises(QueueGenerationError, match="budget exhausted") as failure:
        generate_queue(posts, seed=0, rules=QueueRules(1, 3), max_attempts=1)
    assert failure.value.attempts == 1
    assert failure.value.remaining == 3
    with pytest.raises(QueueGenerationError) as bounded:
        generate_queue(posts, seed=0, rules=QueueRules(1, 3), max_attempts=3)
    assert bounded.value.attempts == 3


def test_restarts_recover_a_valid_order() -> None:
    posts = posts_for(
        [
            ("B", "y"),
            ("B", "y"),
            ("D", "z"),
            ("B", "y"),
            ("A", "y"),
            ("A", "z"),
            ("B", "x"),
            ("D", "x"),
            ("D", "z"),
            ("A", "z"),
            ("A", "z"),
        ]
    )
    with pytest.raises(QueueGenerationError, match="budget exhausted"):
        generate_queue(posts, seed=0, rules=QueueRules(1, 3), max_attempts=1)
    validate_sequence(
        generate_queue(posts, seed=0, rules=QueueRules(1, 3)), QueueRules(1, 3)
    )


@pytest.mark.parametrize("budget", [0, -1, True])
def test_invalid_budget(budget: int) -> None:
    with pytest.raises(ValueError):
        generate_queue([], seed=0, max_attempts=budget)


@pytest.fixture
def repository() -> Iterator[SQLiteRepository]:
    connection = sqlite3.connect(":memory:")
    yield SQLiteRepository(connection)
    connection.close()


def test_persist_full_order_and_regenerate(
    repository: SQLiteRepository, library: list[Post]
) -> None:
    for post in library:
        repository.create_post(post)
    expected = generate_queue(library, seed=42)
    entries = generate_and_store_queue(repository, seed=42)
    assert [entry.post_id for entry in entries] == [post.id for post in expected]
    assert [entry.position for entry in entries] == list(range(1, 401))
    assert repository.list_queue() == entries
    with pytest.raises(ExistingQueueError):
        generate_and_store_queue(repository, seed=1)
    assert repository.list_queue() == entries
    changed = generate_and_store_queue(repository, seed=1, regenerate=True)
    assert changed != entries
    assert repository.list_queue() == changed


def test_terminal_and_active_posts_excluded(repository: SQLiteRepository) -> None:
    posts = posts_for([("A", "a"), ("B", "b"), ("C", "c"), ("D", "d")])
    now = datetime(2026, 10, 14, tzinfo=UTC)
    for post in posts:
        repository.create_post(post)
    for post, status in zip(posts, PublicationStatus, strict=False):
        repository.create_publication(
            Publication(
                post.id,
                status,
                now,
                published_at=now if status is PublicationStatus.PUBLISHED else None,
            )
        )
    assert generate_and_store_queue(repository, seed=1) == [QueueEntry(posts[-1].id, 1)]


def test_ambiguous_history_fails_closed(repository: SQLiteRepository) -> None:
    post = posts_for([("A", "a")])[0]
    repository.create_post(post)
    attempt = Publication(
        post.id, PublicationStatus.PUBLISHING, datetime(2026, 10, 14, tzinfo=UTC)
    )
    repository.create_publication(attempt)
    repository.create_publication(attempt)
    with pytest.raises(InvalidTransition):
        generate_and_store_queue(repository, seed=0)
    assert repository.list_queue() == []


def test_generation_failure_preserves_old_queue(repository: SQLiteRepository) -> None:
    posts = posts_for([("A", "a"), ("A", "b")])
    for post in posts:
        repository.create_post(post)
    old = [QueueEntry(posts[0].id, 9)]
    repository.replace_queue(old)
    with pytest.raises(QueueGenerationError):
        generate_and_store_queue(repository, seed=0, regenerate=True)
    assert repository.list_queue() == old


def test_partial_persistence_failure_rolls_back(
    repository: SQLiteRepository,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    posts = posts_for([("A", "a"), ("B", "b")])
    for post in posts:
        repository.create_post(post)
    old = [QueueEntry(posts[0].id, 9)]
    repository.replace_queue(old)
    original = repository.replace_queue

    def fail(entries: list[QueueEntry]) -> None:
        original(entries)
        raise sqlite3.OperationalError("sensitive payload")

    monkeypatch.setattr(repository, "replace_queue", fail)
    with caplog.at_level(logging.INFO), pytest.raises(sqlite3.OperationalError):
        generate_and_store_queue(repository, seed=0, regenerate=True)
    assert repository.list_queue() == old
    assert "sensitive payload" not in caplog.text
    assert "Private prompt" not in str([record.__dict__ for record in caplog.records])
    assert not any(
        record.__dict__.get("event") == "queue_generated" for record in caplog.records
    )


def test_empty_service_and_success_logging(
    repository: SQLiteRepository, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        assert generate_and_store_queue(repository, seed=7) == []
    record = next(
        r for r in caplog.records if r.__dict__.get("event") == "queue_generated"
    )
    assert record.__dict__["queue_length"] == 0
    assert record.__dict__["seed"] == 7


def test_queue_history_conflict_preserves_queue(repository: SQLiteRepository) -> None:
    post = posts_for([("A", "a")])[0]
    repository.create_post(post)
    repository.create_publication(
        Publication(
            post.id, PublicationStatus.FAILED, datetime(2026, 10, 14, tzinfo=UTC)
        )
    )
    old = [QueueEntry(post.id, 1)]
    repository.replace_queue(old)
    with pytest.raises(InvalidTransition):
        generate_and_store_queue(repository, seed=0, regenerate=True)
    assert repository.list_queue() == old


def test_unknown_queued_post_fails_closed(
    repository: SQLiteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repository, "list_queue", lambda: [QueueEntry("AED-9999", 1)])
    with pytest.raises(InvalidTransition, match="unknown"):
        generate_and_store_queue(repository, seed=0, regenerate=True)
