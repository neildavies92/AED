"""Validate and import the versioned approved snapshot without external services."""

import argparse
import csv
import logging
import sqlite3
from collections import Counter
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.copy_policy import HashtagSet, select_hashtag_set
from ask_every_day.domain import Post
from ask_every_day.logging_config import configure_logging
from ask_every_day.repository import (
    ContentDriftError,
    ImportResult,
    Repository,
    open_repository,
)

logger = logging.getLogger(
    "ask_every_day.content_import" if __name__ == "__main__" else __name__
)
_COLUMNS = {
    "id",
    "prompt",
    "category",
    "subcategory",
    "highlight_text",
    "template",
    "status",
    "caption_override",
    "hashtag_set_id",
}
_EXPECTED_IDS = {f"AED-{number:04}" for number in range(1, 401)}


class SnapshotError(ValueError):
    """The snapshot does not satisfy the approved production data contract."""


def _read_rows(path: Path, columns: set[str]) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            header = next(reader, [])
            if len(header) != len(columns) or set(header) != columns:
                raise SnapshotError(f"Unexpected columns in {path}")
            rows = []
            for values in reader:
                if len(values) != len(header):
                    raise SnapshotError(f"Malformed row at {path}:{reader.line_num}")
                rows.append(dict(zip(header, values, strict=True)))
            return rows
    except (csv.Error, UnicodeError) as error:
        raise SnapshotError(f"Cannot decode CSV: {path}") from error


def optional_copy(value: str | None) -> str | None:
    """Normalize absent editorial cells while preserving populated text exactly."""
    if value is not None and not isinstance(value, str):
        raise TypeError("Copy metadata must be a string or None")
    return value if value is not None and value.strip() else None


def load_hashtag_snapshot(path: Path) -> tuple[HashtagSet, ...]:
    """Read editorial values without adding filesystem access to pure copy policy."""
    rows = _read_rows(path, {"id", "name", "hashtags", "active", "use_for"})
    sets = []
    try:
        for row in rows:
            if row["active"] not in ("TRUE", "FALSE"):
                raise ValueError("Invalid active flag")
            sets.append(
                HashtagSet(
                    row["id"],
                    row["name"],
                    tuple(row["hashtags"].split()),
                    tuple(row["use_for"].split("; ")),
                    row["active"] == "TRUE",
                )
            )
        select_hashtag_set("Validation", sets, enabled=False)
    except (TypeError, ValueError) as error:
        raise SnapshotError("Invalid hashtag editorial snapshot") from error
    return tuple(sets)


def resolve_hashtag_reference(
    value: str | None, sets: tuple[HashtagSet, ...]
) -> str | None:
    """Resolve an exact Sheet/export ID or name; never choose a category fallback."""
    value = optional_copy(value)
    if value is None:
        return None
    try:
        selected = select_hashtag_set("Validation", sets, explicit_set=value)
    except (TypeError, ValueError) as error:
        raise SnapshotError("Hashtag reference must identify one active set") from error
    assert selected is not None
    return selected.id


def load_snapshot(source: Path, taxonomy: Path, hashtags: Path) -> tuple[Post, ...]:
    """Validate the whole 400-row snapshot before any database is opened."""
    hashtag_sets = load_hashtag_snapshot(hashtags)
    taxonomy_rows = _read_rows(taxonomy, {"category", "subcategory"})
    pairs = {(row["category"], row["subcategory"]) for row in taxonomy_rows}
    if (
        len(taxonomy_rows) != 40
        or len(pairs) != 40
        or any(
            not category.strip() or not subcategory.strip()
            for category, subcategory in pairs
        )
    ):
        raise SnapshotError("Taxonomy must contain 40 distinct nonblank pairs")
    rows = _read_rows(source, _COLUMNS)
    if len(rows) != 400:
        raise SnapshotError("Snapshot must contain exactly 400 Approved rows")
    posts: list[Post] = []
    for number, row in enumerate(rows, 2):
        if row["status"] != "Approved":
            raise SnapshotError(f"Unapproved row at {source}:{number}")
        if row["template"] != "Classic":
            raise SnapshotError(f"Unsupported template at {source}:{number}")
        try:
            hashtag_id = optional_copy(row["hashtag_set_id"])
            if resolve_hashtag_reference(hashtag_id, hashtag_sets) != hashtag_id:
                raise ValueError("Snapshot requires immutable hashtag IDs")
            posts.append(
                Post(
                    id=row["id"],
                    prompt=row["prompt"],
                    category=row["category"],
                    subcategory=row["subcategory"],
                    template=row["template"],
                    highlight_text=row["highlight_text"] or None,
                    caption_override=optional_copy(row["caption_override"]),
                    hashtag_set_id=hashtag_id,
                )
            )
        except (ValueError, TypeError) as error:
            raise SnapshotError(f"Invalid Post at {source}:{number}") from error
    if (
        len({post.id for post in posts}) != 400
        or {p.id for p in posts} != _EXPECTED_IDS
    ):
        raise SnapshotError("Snapshot IDs must be unique AED-0001 through AED-0400")
    if len({" ".join(post.prompt.casefold().split()) for post in posts}) != 400:
        raise SnapshotError("Snapshot contains duplicate prompts")
    if Counter((post.category, post.subcategory) for post in posts) != Counter(
        {pair: 10 for pair in pairs}
    ):
        raise SnapshotError("Snapshot must contain ten posts per taxonomy pair")
    return tuple(sorted(posts, key=lambda post: post.id))


def import_snapshot(
    repository: Repository, source: Path, taxonomy: Path, hashtags: Path
) -> ImportResult:
    """Validate fully, then use the repository's atomic create-or-compare contract."""
    return _import_posts(repository, load_snapshot(source, taxonomy, hashtags), source)


def _import_posts(
    repository: Repository, posts: tuple[Post, ...], source: Path
) -> ImportResult:
    try:
        result = repository.import_posts(posts)
    except Exception as error:
        logger.warning(
            "content_import_failed",
            extra={
                "event": "content_import_failed",
                "source_path": str(source),
                "error_type": type(error).__name__,
            },
        )
        raise
    logger.info(
        "content_imported",
        extra={
            "event": "content_imported",
            "source_path": str(source),
            "imported_count": result.imported_count,
            "unchanged_count": result.unchanged_count,
            "updated_count": 0,
        },
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the approved content snapshot")
    parser.add_argument("--config", type=Path, default=Path("aed.toml"))
    parser.add_argument("--source", type=Path, default=Path("data/prompts.csv"))
    parser.add_argument("--taxonomy", type=Path, default=Path("data/taxonomy.csv"))
    parser.add_argument("--hashtags", type=Path, default=Path("data/hashtags.csv"))
    args = parser.parse_args()
    configure_logging()
    try:
        # Invalid files must not even create an empty database.
        posts = load_snapshot(args.source, args.taxonomy, args.hashtags)
        config = load_config(args.config)
        with open_repository(config) as repository:
            result = _import_posts(repository, posts, args.source)
        print(f"Imported {result.imported_count}; unchanged {result.unchanged_count}")
    except (OSError, ValueError, sqlite3.Error) as error:
        logger.error(
            "content_import_failed",
            extra={
                "event": "content_import_failed",
                "error_type": type(error).__name__,
            },
        )
        parser.exit(
            1,
            f"Import failed: {error}\n"
            if isinstance(error, (SnapshotError, ContentDriftError))
            else f"Import failed ({type(error).__name__}); inspect files and local state.\n",
        )


if __name__ == "__main__":
    main()
