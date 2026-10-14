"""Exact spacing boundaries and a constructive current-library feasibility check."""

import csv
from collections import defaultdict
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from ask_every_day.domain import Post
from ask_every_day.queue_rules import (
    QueueRules,
    QueueValidationError,
    validate_sequence,
)


def post(number: int, category: str, subcategory: str = "One") -> Post:
    return Post(f"AED-{number:04}", "A prompt?", category, subcategory, "Classic")


def spaced(intervening: int, *, same_pair: bool = False) -> list[Post]:
    return [
        post(1, "A"),
        *(post(index + 2, f"Other {index}") for index in range(intervening)),
        post(intervening + 2, "A", "One" if same_pair else "Two"),
    ]


@pytest.mark.parametrize("posts", [[], [post(1, "A")], [post(1, "A"), post(2, "B")]])
def test_short_valid_sequences(posts: list[Post]) -> None:
    assert validate_sequence(posts) is None


def test_duplicate_id_has_priority_and_useful_context() -> None:
    with pytest.raises(QueueValidationError) as caught:
        validate_sequence([post(1, "A"), post(1, "A")])
    error = caught.value
    assert (error.rule, error.post_id) == ("duplicate_id", "AED-0001")
    assert (error.position, error.previous_position) == (2, 1)
    assert error.minimum_gap is None
    assert "position 2" in str(error) and "AED-0001" in str(error)


def test_duplicate_id_is_global_not_windowed() -> None:
    sequence = [post(index, f"Category {index}") for index in range(1, 20)]
    sequence.append(post(1, "Another category"))
    with pytest.raises(QueueValidationError, match="duplicate_id"):
        validate_sequence(sequence)


@pytest.mark.parametrize("gap", [0, 1, 2])
def test_category_repetition_inside_gap_fails(gap: int) -> None:
    with pytest.raises(QueueValidationError) as caught:
        validate_sequence(spaced(gap))
    assert caught.value.rule == "category_gap"
    assert caught.value.minimum_gap == 3
    assert caught.value.actual_gap == gap


def test_category_boundary_is_three_intervening_posts() -> None:
    validate_sequence(spaced(3))


@pytest.mark.parametrize("gap", [3, 4])
def test_subcategory_repetition_inside_gap_fails(gap: int) -> None:
    with pytest.raises(QueueValidationError) as caught:
        validate_sequence(spaced(gap, same_pair=True))
    assert caught.value.rule == "subcategory_gap"
    assert caught.value.minimum_gap == 5
    assert caught.value.actual_gap == gap


def test_subcategory_boundary_is_five_intervening_posts() -> None:
    validate_sequence(spaced(5, same_pair=True))


def test_change_names_under_different_categories_are_distinct() -> None:
    validate_sequence([post(1, "Growth", "Change"), post(2, "Life Audit", "Change")])


def test_gap_uses_most_recent_occurrence() -> None:
    sequence = spaced(3)
    sequence.extend([post(6, "Other"), post(7, "A", "Three")])
    with pytest.raises(QueueValidationError) as caught:
        validate_sequence(sequence)
    assert caught.value.previous_position == 5
    assert caught.value.actual_gap == 1


def test_custom_rules_and_deterministic_failures() -> None:
    rules = QueueRules(category_min_gap=1, subcategory_min_gap=0)
    validate_sequence(spaced(1, same_pair=True), rules)
    messages: list[str] = []
    for _ in range(2):
        with pytest.raises(QueueValidationError) as caught:
            validate_sequence(spaced(1), QueueRules(category_min_gap=2))
        messages.append(str(caught.value))
    assert messages[0] == messages[1]


def test_rules_are_immutable() -> None:
    rules = QueueRules()
    with pytest.raises(FrozenInstanceError):
        setattr(rules, "category_min_gap", 10)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("category_min_gap", 0, ValueError),
        ("category_min_gap", -1, ValueError),
        ("subcategory_min_gap", -1, ValueError),
        ("category_min_gap", True, TypeError),
        ("subcategory_min_gap", False, TypeError),
        ("category_min_gap", 2.5, TypeError),
        ("subcategory_min_gap", "5", TypeError),
    ],
)
def test_invalid_rule_configuration(field: str, value: object, error: type[Exception]):
    with pytest.raises(error):
        if field == "category_min_gap":
            QueueRules(category_min_gap=cast(int, value))
        else:
            QueueRules(subcategory_min_gap=cast(int, value))


def test_current_400_prompt_library_has_a_valid_spacing_certificate() -> None:
    # Test-only construction: cycle all ten categories for each of four pairs,
    # repeat ten times. No queue generator or persisted queue is introduced.
    groups: dict[str, dict[str, list[Post]]] = defaultdict(lambda: defaultdict(list))
    path = Path(__file__).parents[1] / "data/prompts.csv"
    with path.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            assert row["status"] == "Approved"
            groups[row["category"]][row["subcategory"]].append(
                Post(
                    row["id"],
                    row["prompt"],
                    row["category"],
                    row["subcategory"],
                    row["template"],
                    row["highlight_text"] or None,
                )
            )
    assert len(groups) == 10
    assert all(len(pairs) == 4 for pairs in groups.values())
    assert all(
        len(posts) == 10 for pairs in groups.values() for posts in pairs.values()
    )
    sequence = [
        groups[category][sorted(groups[category])[pair_index]][round_index]
        for round_index in range(10)
        for pair_index in range(4)
        for category in sorted(groups)
    ]
    assert len(sequence) == 400
    assert len({item.id for item in sequence}) == 400
    validate_sequence(sequence)
