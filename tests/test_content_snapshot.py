"""Integrity checks for the reviewed export, not a Google Sheets importer."""

import csv
from collections import Counter
from pathlib import Path

from ask_every_day.domain import Post

DATA = Path(__file__).resolve().parents[1] / "data"


def _rows(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def test_snapshot_has_400_contiguous_unique_ids() -> None:
    rows = _rows("prompts.csv")
    assert [row["id"] for row in rows] == [f"AED-{n:04}" for n in range(1, 401)]


def test_distribution_matches_40_approved_taxonomy_pairs() -> None:
    taxonomy = [(row["category"], row["subcategory"]) for row in _rows("taxonomy.csv")]
    assert len(taxonomy) == len(set(taxonomy)) == 40
    counts = Counter(
        (row["category"], row["subcategory"]) for row in _rows("prompts.csv")
    )
    assert counts == Counter({pair: 10 for pair in taxonomy})


def test_prompts_are_distinct_and_concise() -> None:
    rows = _rows("prompts.csv")
    normalized = {" ".join(row["prompt"].casefold().split()) for row in rows}
    assert len(normalized) == 400
    assert all(len(row["prompt"]) <= 100 for row in rows)


def test_metadata_and_highlights_are_valid() -> None:
    for row in _rows("prompts.csv"):
        assert row["status"] == "Approved"
        assert row["template"] == "Classic"
        post = Post(
            id=row["id"],
            prompt=row["prompt"],
            category=row["category"],
            subcategory=row["subcategory"],
            highlight_text=row["highlight_text"],
            template=row["template"],
        )
        assert post.highlight_text
        assert len(post.highlight_text) < len(post.prompt) * 0.8
