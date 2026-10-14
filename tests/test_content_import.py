"""Snapshot validation and atomic, non-overwriting local import contracts."""

import csv
import logging
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ask_every_day.content_import import SnapshotError, import_snapshot, load_snapshot
from ask_every_day.domain import AppConfig, QueueEntry
from ask_every_day.repository import (
    ContentDriftError,
    ImportResult,
    SQLiteRepository,
    open_repository,
)

DATA = Path(__file__).parents[1] / "data"


def snapshot(tmp_path: Path) -> tuple[Path, list[dict[str, str]]]:
    with (DATA / "prompts.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    path = tmp_path / "prompts.csv"
    write_rows(path, rows)
    return path, rows


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def config(tmp_path: Path) -> AppConfig:
    return AppConfig(tmp_path / "aed.sqlite3", tmp_path / "assets", "@ask.every.day")


def test_full_round_trip_and_rerun(tmp_path: Path) -> None:
    posts = load_snapshot(DATA / "prompts.csv", DATA / "taxonomy.csv")
    with open_repository(config(tmp_path)) as repo:
        assert import_snapshot(
            repo, DATA / "prompts.csv", DATA / "taxonomy.csv"
        ) == ImportResult(400, 0)
        assert repo.list_posts() == list(posts)
        repo.replace_queue([QueueEntry(posts[0].id, 1)])
    with open_repository(config(tmp_path)) as repo:
        assert import_snapshot(
            repo, DATA / "prompts.csv", DATA / "taxonomy.csv"
        ) == ImportResult(0, 400)
        assert repo.list_queue() == [QueueEntry(posts[0].id, 1)]


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "missing",
        "draft",
        "highlight",
        "taxonomy",
        "template",
        "blank",
        "duplicate_prompt",
        "outside_id",
    ],
)
def test_invalid_snapshot_rejected_before_writes(tmp_path: Path, change: str) -> None:
    path, rows = snapshot(tmp_path)
    if change == "duplicate":
        rows[-1]["id"] = rows[0]["id"]
    elif change == "missing":
        rows.pop()
    elif change == "draft":
        rows[-1]["status"] = "Draft"
    elif change == "highlight":
        rows[-1]["highlight_text"] = "absent phrase"
    elif change == "taxonomy":
        rows[-1]["subcategory"] = "Unrecognized"
    elif change == "template":
        rows[-1]["template"] = "Other"
    elif change == "blank":
        rows[-1]["prompt"] = " "
    elif change == "duplicate_prompt":
        rows[-1]["prompt"] = rows[0]["prompt"]
        rows[-1]["highlight_text"] = ""
    else:
        rows[-1]["id"] = "AED-0401"
    write_rows(path, rows)
    with open_repository(config(tmp_path)) as repo:
        with pytest.raises(SnapshotError):
            import_snapshot(repo, path, DATA / "taxonomy.csv")
        assert repo.list_posts() == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        "id,id\n",
        "id,prompt,category,subcategory,highlight_text,template,status\nAED-0001,short\n",
        'id,prompt,category,subcategory,highlight_text,template,status\n"unterminated',
    ],
)
def test_csv_structure_errors(tmp_path: Path, text: str) -> None:
    source = tmp_path / "broken.csv"
    source.write_text(text)
    with pytest.raises(SnapshotError):
        load_snapshot(source, DATA / "taxonomy.csv")


def test_taxonomy_must_be_complete(tmp_path: Path) -> None:
    taxonomy = tmp_path / "taxonomy.csv"
    taxonomy.write_text("category,subcategory\nGrowth,Change\n")
    with pytest.raises(SnapshotError, match="40 distinct"):
        load_snapshot(DATA / "prompts.csv", taxonomy)


def test_blank_highlight_and_reordered_source_round_trip(tmp_path: Path) -> None:
    source, rows = snapshot(tmp_path)
    rows[0]["highlight_text"] = ""
    rows.reverse()
    write_rows(source, rows)
    with open_repository(config(tmp_path)) as repo:
        assert import_snapshot(repo, source, DATA / "taxonomy.csv") == ImportResult(
            400, 0
        )
        first = repo.get_post("AED-0001")
        assert first is not None and first.highlight_text is None
        assert [post.id for post in repo.list_posts()] == [
            f"AED-{n:04}" for n in range(1, 401)
        ]


@pytest.mark.parametrize("extra", [False, True])
def test_stored_drift_never_overwritten(tmp_path: Path, extra: bool) -> None:
    posts = load_snapshot(DATA / "prompts.csv", DATA / "taxonomy.csv")
    local = (
        replace(posts[-1], id="AED-9999")
        if extra
        else replace(posts[-1], highlight_text=None)
    )
    with open_repository(config(tmp_path)) as repo:
        repo.create_post(local)
        with pytest.raises(ContentDriftError):
            import_snapshot(repo, DATA / "prompts.csv", DATA / "taxonomy.csv")
        assert repo.list_posts() == [local]


def test_missing_subset_is_filled(tmp_path: Path) -> None:
    posts = load_snapshot(DATA / "prompts.csv", DATA / "taxonomy.csv")
    with open_repository(config(tmp_path)) as repo:
        repo.create_post(posts[0])
        assert import_snapshot(
            repo, DATA / "prompts.csv", DATA / "taxonomy.csv"
        ) == ImportResult(399, 1)


def test_database_failure_rolls_back_whole_import() -> None:
    posts = load_snapshot(DATA / "prompts.csv", DATA / "taxonomy.csv")
    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        repo = SQLiteRepository(connection)
        connection.execute(
            "CREATE TEMP TRIGGER fail_insert BEFORE INSERT ON posts WHEN NEW.id = 'AED-0400' BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            repo.import_posts(posts)
        assert repo.list_posts() == []
        assert not connection.in_transaction
    finally:
        connection.close()


def test_repository_rejects_duplicates(tmp_path: Path) -> None:
    post = load_snapshot(DATA / "prompts.csv", DATA / "taxonomy.csv")[0]
    with open_repository(config(tmp_path)) as repo:
        with pytest.raises(ValueError, match="duplicate"):
            repo.import_posts([post, post])
        assert repo.list_posts() == []


def test_logs_have_counts_not_content(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with open_repository(config(tmp_path)) as repo, caplog.at_level(logging.INFO):
        import_snapshot(repo, DATA / "prompts.csv", DATA / "taxonomy.csv")
        events = [record.__dict__ for record in caplog.records]
        event = next(
            event for event in events if event.get("event") == "content_imported"
        )
        assert event["imported_count"] == 400
        assert event["unchanged_count"] == 0
        assert repo.list_posts()[0].prompt not in str(events)


def test_cli_uses_configuration_and_no_network(tmp_path: Path) -> None:
    settings = tmp_path / "aed.toml"
    settings.write_text(
        'database_path = "aed.sqlite3"\nassets_path = "assets"\naccount_handle = "@ask.every.day"\n'
    )
    args = [
        sys.executable,
        "-m",
        "ask_every_day.content_import",
        "--config",
        str(settings),
        "--source",
        str(DATA / "prompts.csv"),
        "--taxonomy",
        str(DATA / "taxonomy.csv"),
    ]
    first = subprocess.run(
        args, cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert "Imported 400; unchanged 0" in first.stdout
    second = subprocess.run(
        args, cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert "Imported 0; unchanged 400" in second.stdout
    with open_repository(config(tmp_path)) as repo:
        assert len(repo.list_posts()) == 400
    assert not (tmp_path / "assets").exists()
    settings.write_text(
        settings.read_text().replace('"aed.sqlite3"', '"invalid.sqlite3"')
    )
    bad = subprocess.run(
        [
            *args[:-4],
            "--source",
            str(tmp_path / "absent.csv"),
            "--taxonomy",
            str(DATA / "taxonomy.csv"),
        ],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1
    assert not (tmp_path / "invalid.sqlite3").exists()
