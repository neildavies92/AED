"""Editorial snapshot integrity and deterministic, override-aware copy selection."""

import csv
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from ask_every_day.copy_policy import (
    Caption,
    HashtagSet,
    select_caption,
    select_hashtag_set,
)

DATA = Path(__file__).parents[1] / "data"


def rows(filename: str) -> list[dict[str, str]]:
    with (DATA / filename).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


@pytest.fixture
def captions() -> list[Caption]:
    return [
        Caption(row["id"], row["caption"], row["active"] == "TRUE")
        for row in rows("captions.csv")
    ]


@pytest.fixture
def sets() -> list[HashtagSet]:
    return [
        HashtagSet(
            row["id"],
            row["name"],
            tuple(row["hashtags"].split()),
            tuple(row["use_for"].split("; ")),
            row["active"] == "TRUE",
        )
        for row in rows("hashtags.csv")
    ]


def test_snapshot_integrity(captions: list[Caption], sets: list[HashtagSet]) -> None:
    assert [item.id for item in captions] == [f"CAP-{i:03}" for i in range(1, 13)]
    assert [item.id for item in sets] == [f"TAG-{i:03}" for i in range(1, 7)]
    assert len({item.caption for item in captions}) == 12
    assert len({item.name for item in sets}) == 6
    for filename in ("captions.csv", "hashtags.csv"):
        assert all(row["active"] in {"TRUE", "FALSE"} for row in rows(filename))
    categories = {row["category"] for row in rows("taxonomy.csv")}
    assert len(categories) == 10
    mapped = [category for item in sets[1:] for category in item.use_for]
    assert len(mapped) == len(set(mapped)) == 10
    assert set(mapped) == categories
    assert all(item.active for item in captions + sets)


@pytest.mark.parametrize(
    "category,expected",
    [
        ("Self-awareness", "TAG-002"),
        ("Past & Healing", "TAG-002"),
        ("Growth", "TAG-003"),
        ("Confidence", "TAG-003"),
        ("Life Audit", "TAG-003"),
        ("Relationships", "TAG-004"),
        ("Future", "TAG-005"),
        ("Purpose & Work", "TAG-005"),
        ("Gratitude & Perspective", "TAG-006"),
        ("Everyday Life", "TAG-006"),
    ],
)
def test_category_mapping(sets: list[HashtagSet], category: str, expected: str) -> None:
    result = select_hashtag_set(category, sets)
    assert result is not None and result.id == expected
    assert select_hashtag_set(category, sets[::-1]) == result


def test_caption_cycle_and_order(captions: list[Caption]) -> None:
    for number, index in [(1, 0), (12, 11), (13, 0), (400, 3)]:
        expected = captions[index].caption
        assert select_caption(f"AED-{number:04}", captions) == expected
        assert select_caption(f"AED-{number:04}", captions[::-1]) == expected


def test_override_preserves_exact_copy_and_precedes_empty_pool() -> None:
    override = "  A personal reflection. 🪞\nTake your time.  "
    assert select_caption("AED-0001", [], override=override) == override


@pytest.mark.parametrize("blank", [None, "", " \n"])
def test_blank_overrides_use_defaults(
    captions: list[Caption], sets: list[HashtagSet], blank: str | None
) -> None:
    assert select_caption("AED-0001", captions, override=blank) == captions[0].caption
    assert select_hashtag_set("Growth", sets, explicit_set=blank) == sets[2]


def test_inactive_caption_is_excluded_and_empty_pool_fails(
    captions: list[Caption],
) -> None:
    captions[0] = replace(captions[0], active=False)
    assert select_caption("AED-0001", captions) == captions[1].caption
    with pytest.raises(ValueError, match="No active"):
        select_caption("AED-0001", [captions[0]])
    with pytest.raises(ValueError, match="Duplicate"):
        select_caption("AED-0001", [captions[1], captions[1]])


def test_explicit_set_id_or_sheet_name_overrides_category(
    sets: list[HashtagSet],
) -> None:
    for value in ("TAG-004", "Relationships", "TAG-001", "General"):
        expected = sets[3] if value in ("TAG-004", "Relationships") else sets[0]
        assert select_hashtag_set("Growth", sets, explicit_set=value) == expected


def test_fallback_none_and_disabled(sets: list[HashtagSet]) -> None:
    assert select_hashtag_set("Unmapped category", sets) == sets[0]
    assert select_hashtag_set("Growth", [sets[0]]) == sets[0]
    assert select_hashtag_set("Growth", []) is None
    assert select_hashtag_set("Growth", sets, enabled=False) is None
    assert (
        select_hashtag_set("Growth", sets, explicit_set="General", enabled=False)
        is None
    )
    sets[2] = replace(sets[2], active=False)
    assert select_hashtag_set("Growth", sets) == sets[0]
    sets[0] = replace(sets[0], active=False)
    assert select_hashtag_set("Growth", sets) is None


@pytest.mark.parametrize("value", ["TAG-999", "Unknown", "growth", " Growth "])
def test_unknown_explicit_set_fails_even_when_disabled(
    sets: list[HashtagSet], value: str
) -> None:
    for enabled in (True, False):
        with pytest.raises(ValueError, match="one active"):
            select_hashtag_set("Growth", sets, explicit_set=value, enabled=enabled)


def test_inactive_and_ambiguous_sets_fail(sets: list[HashtagSet]) -> None:
    sets[2] = replace(sets[2], active=False)
    with pytest.raises(ValueError, match="one active"):
        select_hashtag_set("Growth", sets, explicit_set="Growth")
    ambiguous = replace(sets[3], use_for=("Self-awareness",))
    with pytest.raises(ValueError, match="Ambiguous"):
        select_hashtag_set("Self-awareness", [sets[1], ambiguous])
    with pytest.raises(ValueError, match="unique"):
        select_hashtag_set("Growth", [sets[0], sets[0]])
    with pytest.raises(ValueError, match="Ambiguous"):
        select_hashtag_set(
            "Growth", [sets[0], replace(sets[1], use_for=("All categories",))]
        )


@pytest.mark.parametrize("post_id", ["AED-0000", "AED-1", "BAD-0001", "AED-１２３４"])
def test_invalid_post_ids_fail(post_id: str) -> None:
    with pytest.raises(ValueError):
        select_caption(post_id, [], override="A valid override")


@pytest.mark.parametrize("tags", [("journaling",), ("#a-b",), ("#",), ("#A", "#a"), ()])
def test_malformed_or_duplicate_tags_fail(tags: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        HashtagSet("TAG-001", "General", tags, ("All categories",))


def test_invalid_editorial_values_and_override_types() -> None:
    with pytest.raises(ValueError):
        Caption("CAP-001", " ")
    with pytest.raises(ValueError):
        HashtagSet("TAG-001", "General", ("#a",), ("All categories", "Growth"))
    # Wrong runtime values deliberately reach the public boundaries.
    invalid: object = 7
    with pytest.raises(TypeError):
        select_caption("AED-0001", [], override=cast(str, invalid))
    with pytest.raises(TypeError):
        select_hashtag_set("Growth", [], explicit_set=cast(str, invalid))
    with pytest.raises(TypeError):
        Caption("CAP-001", "Valid", active=cast(bool, "TRUE"))


def test_selection_is_independent_of_process_hash_seed() -> None:
    code = """from ask_every_day.copy_policy import Caption, select_caption
pool = [Caption('CAP-002', 'Second'), Caption('CAP-001', 'First')]
print(select_caption('AED-0013', pool))
"""
    outputs = [
        subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for seed in (0, 1, 12345)
    ]
    assert outputs == ["First\n"] * 3
