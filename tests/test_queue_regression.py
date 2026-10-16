"""Independent stress invariants and durable queue transaction regressions."""

import csv
import logging
import random
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from itertools import permutations
from pathlib import Path
from time import perf_counter

import pytest

from ask_every_day.domain import AppConfig, Post, QueueEntry
from ask_every_day.publication_service import PublicationService
from ask_every_day.queue_generation import QueueGenerationError, generate_queue
from ask_every_day.queue_rules import QueueRules, validate_sequence
from ask_every_day.queue_service import generate_and_store_queue
from ask_every_day.repository import SQLiteRepository, open_repository


def independent_valid(order: list[Post] | tuple[Post, ...], rules: QueueRules) -> bool:
    for index, post in enumerate(order):
        if any(
            previous.category == post.category
            for previous in order[max(0, index - rules.category_min_gap) : index]
        ):
            return False
        if any(
            (previous.category, previous.subcategory)
            == (post.category, post.subcategory)
            for previous in order[max(0, index - rules.subcategory_min_gap) : index]
        ):
            return False
    return True


def assert_complete(source: list[Post], ordered: list[Post], rules: QueueRules) -> None:
    assert len(ordered) == len(source)
    assert Counter(post.id for post in ordered) == Counter(
        {post.id: 1 for post in source}
    )
    assert {post.id: post for post in ordered} == {post.id: post for post in source}
    assert independent_valid(ordered, rules)
    validate_sequence(ordered, rules)


@pytest.fixture(scope="module")
def library() -> list[Post]:
    with (Path(__file__).resolve().parents[1] / "data/prompts.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 400 and all(row["status"] == "Approved" for row in rows)
    return [
        Post(
            row["id"],
            row["prompt"],
            row["category"],
            row["subcategory"],
            row["template"],
            row["highlight_text"] or None,
        )
        for row in rows
    ]


@pytest.fixture(scope="module")
def stress_orders(library: list[Post]) -> dict[int, list[Post]]:
    started = perf_counter()
    orders = {seed: generate_queue(library, seed=seed) for seed in range(100)}
    distinct = len({tuple(post.id for post in order) for order in orders.values()})
    print(
        f"\nQueue stress: 100 generations, {distinct} distinct orders, {perf_counter() - started:.2f}s"
    )
    return orders


def test_seed_range_independently(
    library: list[Post], stress_orders: dict[int, list[Post]]
) -> None:
    pairs = Counter((post.category, post.subcategory) for post in library)
    assert pairs[("Growth", "Change")] == pairs[("Life Audit", "Change")] == 10
    for seed, ordered in stress_orders.items():
        try:
            assert_complete(library, ordered, QueueRules())
        except AssertionError as exc:
            raise AssertionError(f"Queue invariant failed for seed={seed}") from exc
    assert (
        len({tuple(post.id for post in order) for order in stress_orders.values()}) > 1
    )


@pytest.mark.parametrize("seed", [0, 49, 99])
def test_order_invariance(
    library: list[Post], stress_orders: dict[int, list[Post]], seed: int
) -> None:
    shuffled = library.copy()
    random.Random(2718).shuffle(shuffled)
    for inputs in [list(reversed(library)), shuffled]:
        assert generate_queue(inputs, seed=seed) == stress_orders[seed]


def synthetic(pairs: list[tuple[str, str]]) -> list[Post]:
    return [
        Post(f"AED-{index:04}", "A private question?", category, subcategory, "Classic")
        for index, (category, subcategory) in enumerate(pairs, start=1)
    ]


@pytest.mark.parametrize(
    "pairs,rules,feasible",
    [
        ([], QueueRules(), True),
        ([("A", "x")], QueueRules(20, 30), True),
        (
            [("A", "x")] * 4 + [("B", "y"), ("B", "z"), ("C", "z")],
            QueueRules(1, 0),
            True,
        ),
        ([("A", "x")] * 4 + [("B", "y"), ("C", "z")], QueueRules(1, 0), False),
        (
            [("A", "x")] * 3 + [("B", "y"), ("C", "z"), ("D", "z"), ("E", "z")],
            QueueRules(1, 3),
            False,
        ),
        (
            [("A", "x")] * 2 + [("B", "y")] * 2 + [("C", "z")] * 2,
            QueueRules(2, 2),
            True,
        ),
        (
            [("A", "x")] * 2 + [("B", "y")] * 2 + [("C", "z"), ("D", "z")],
            QueueRules(1, 3),
            True,
        ),
        ([("Growth", "Change"), ("Life Audit", "Change")], QueueRules(1, 100), True),
        (
            [("A", "x")] * 2 + [("B", "y")] * 2 + [("C", "z")] * 2,
            QueueRules(3, 0),
            False,
        ),
    ],
)
def test_small_cases_exhaustive_oracle(
    pairs: list[tuple[str, str]], rules: QueueRules, feasible: bool
) -> None:
    posts = synthetic(pairs)
    assert (
        any(independent_valid(order, rules) for order in permutations(posts))
        is feasible
    )
    if feasible:
        for seed in [3, 17]:
            assert_complete(posts, generate_queue(posts, seed=seed, rules=rules), rules)
    else:
        with pytest.raises(QueueGenerationError) as failure:
            generate_queue(posts, seed=3, rules=rules, max_attempts=2)
        assert failure.value.attempts <= 2
        assert "A private question" not in str(failure.value)


def test_duplicate_id_conflicting_values() -> None:
    first = synthetic([("A", "x")])[0]
    duplicate = Post(first.id, "Different wording?", "B", "y", "Classic")
    with pytest.raises(QueueGenerationError, match="Duplicate") as failure:
        generate_queue([first, duplicate], seed=8)
    assert failure.value.attempts == 0


def test_regeneration_after_publication_survives_reopen(tmp_path: Path) -> None:
    config = AppConfig(tmp_path / "queue.sqlite", tmp_path, "@ask.every.day")
    posts = synthetic([(str(index), "same name") for index in range(6)])
    with open_repository(config) as repository:
        for post in reversed(posts):
            repository.create_post(post)
        original = generate_and_store_queue(repository, seed=19)
        service = PublicationService(repository)
        record = service.begin_publish(
            original[0].post_id, datetime(2026, 10, 15, tzinfo=UTC)
        )
        service.record_success(record.id, datetime(2026, 10, 15, tzinfo=UTC))
        history = repository.publication_history(original[0].post_id)
        regenerated = generate_and_store_queue(repository, seed=19, regenerate=True)
    with open_repository(config) as reopened:
        assert reopened.list_queue() == regenerated
        assert len(regenerated) == 5
        assert [entry.position for entry in regenerated] == list(range(1, 6))
        assert original[0].post_id not in {entry.post_id for entry in regenerated}
        assert reopened.publication_history(original[0].post_id) == history
        assert (
            generate_and_store_queue(reopened, seed=19, regenerate=True) == regenerated
        )


def test_commit_failure_preserves_durable_queue(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "queue.sqlite"
    connection = sqlite3.connect(path)
    try:
        repository = SQLiteRepository(connection)
        for post in synthetic([("A", "x"), ("B", "y")]):
            repository.create_post(post)
        original = [QueueEntry("AED-0002", 7)]
        repository.replace_queue(original)

        def deny_commit(
            action: int,
            value: str | None,
            column: str | None,
            database: str | None,
            trigger: str | None,
        ) -> int:
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_TRANSACTION and value == "COMMIT"
                else sqlite3.SQLITE_OK
            )

        connection.set_authorizer(deny_commit)
        with caplog.at_level(logging.INFO), pytest.raises(sqlite3.DatabaseError):
            generate_and_store_queue(repository, seed=2, regenerate=True)
        assert not connection.in_transaction
        assert not any(
            record.__dict__.get("event") == "queue_generated"
            for record in caplog.records
        )
    finally:
        connection.close()
    with open_repository(AppConfig(path, tmp_path, "@ask.every.day")) as reopened:
        assert reopened.list_queue() == original
