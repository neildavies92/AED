"""Editorial copy boundary, true v1 migration and atomic persistence contracts."""

import csv
import logging
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ask_every_day.content_import import (
    SnapshotError,
    import_snapshot,
    load_hashtag_snapshot,
    load_snapshot,
    optional_copy,
    resolve_hashtag_reference,
)
from ask_every_day.copy_policy import Caption, select_caption, select_hashtag_set
from ask_every_day.domain import AppConfig, Post, QueueEntry
from ask_every_day.repository import (
    ContentDriftError,
    SQLiteRepository,
    open_repository,
)
from ask_every_day.schema import initialize_schema

DATA = Path(__file__).resolve().parents[1] / "data"
V1 = Path(__file__).parent / "fixtures/schema_v1.sql"
CAPTION = "  A private caption, exactly.\n第二行 🌱  "


def post(**values: str | None) -> Post:
    return Post("AED-0001", "Who are you?", "Growth", "Change", "Classic", **values)


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def rows(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_domain_exact_values_and_policy() -> None:
    assert post().caption_override is None and post().hashtag_set_id is None
    value = post(caption_override=CAPTION, hashtag_set_id="TAG-003")
    assert value.caption_override == CAPTION
    assert (
        select_caption(
            value.id, [Caption("CAP-001", "Fallback")], override=value.caption_override
        )
        == CAPTION
    )
    selected = select_hashtag_set(
        value.category,
        load_hashtag_snapshot(DATA / "hashtags.csv"),
        explicit_set=value.hashtag_set_id,
    )
    assert selected is not None and selected.id == "TAG-003"


@pytest.mark.parametrize("blank", ["", " \t\n", "\u2003\u00a0\u3000", None])
def test_normalization(blank: str | None) -> None:
    assert optional_copy(blank) is None
    if blank is not None:
        with pytest.raises(ValueError):
            post(caption_override=blank)
    assert optional_copy(CAPTION) == CAPTION


@pytest.mark.parametrize(
    "value",
    ["TAG-000", "Growth", "TAG-12", "TAG-0001", "tag-001", " TAG-001", "TAG-１２３"],
)
def test_invalid_domain_reference(value: str) -> None:
    with pytest.raises(ValueError):
        post(hashtag_set_id=value)


def test_wrong_domain_types() -> None:
    with pytest.raises(TypeError):
        replace(post(), caption_override=123)
    with pytest.raises(TypeError):
        replace(post(), hashtag_set_id=123)


def genuine_v1(connection: sqlite3.Connection) -> None:
    connection.executescript(V1.read_text())
    connection.execute(
        "INSERT INTO posts VALUES ('AED-0001','Who are you?','Growth','Change','Classic',NULL)"
    )
    connection.execute("INSERT INTO publication_queue VALUES ('AED-0001',7)")
    connection.execute(
        "INSERT INTO publications VALUES (12,'AED-0001','Published',10,11,'external',NULL)"
    )
    connection.execute("INSERT INTO performance_metrics VALUES (12,20,1,2,3,4,5)")
    connection.commit()


@pytest.mark.parametrize("migrated", [False, True])
def test_fresh_and_migrated_metadata_constraints(migrated: bool) -> None:
    with sqlite3.connect(":memory:") as connection:
        if migrated:
            genuine_v1(connection)
        initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        if migrated:
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                connection.execute("DELETE FROM posts")
        if not migrated:
            connection.execute(
                "INSERT INTO posts (id,prompt,category,subcategory,template) VALUES ('AED-0001','Who?','Growth','Change','Classic')"
            )
        for column, invalid in [
            ("caption_override", ""),
            ("caption_override", "\u2003\u00a0"),
            ("hashtag_set_id", "TAG-000"),
            ("hashtag_set_id", "Growth"),
            ("hashtag_set_id", "TAG-1234"),
        ]:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(f"UPDATE posts SET {column} = ?", (invalid,))
        connection.execute(
            "UPDATE posts SET caption_override=?, hashtag_set_id=?",
            (CAPTION, "TAG-003"),
        )
        assert connection.execute(
            "SELECT caption_override,hashtag_set_id FROM posts"
        ).fetchone() == (CAPTION, "TAG-003")


def test_real_migration_preserves_state_and_matches_fresh(tmp_path: Path) -> None:
    path = tmp_path / "aed.sqlite"
    connection = sqlite3.connect(path)
    genuine_v1(connection)
    before = {
        table: connection.execute(f"SELECT * FROM {table}").fetchall()
        for table in (
            "posts",
            "publication_queue",
            "publications",
            "performance_metrics",
        )
    }
    connection.close()
    with open_repository(AppConfig(path, tmp_path, "@ask.every.day")) as repository:
        assert repository.get_post("AED-0001") == post()
        assert repository.list_queue() == [QueueEntry("AED-0001", 7)]
    with sqlite3.connect(path) as migrated, sqlite3.connect(":memory:") as fresh:
        initialize_schema(fresh)
        assert (
            migrated.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
            == fresh.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
        )
        assert migrated.execute("SELECT * FROM posts").fetchall() == [
            (*before["posts"][0], None, None)
        ]
        for table in ("publication_queue", "publications", "performance_metrics"):
            assert (
                migrated.execute(f"SELECT * FROM {table}").fetchall() == before[table]
            )
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []


def test_second_alter_failure_rolls_back_schema_and_version() -> None:
    with sqlite3.connect(":memory:") as connection:
        genuine_v1(connection)
        alters = 0

        def authorizer(
            action: int,
            arg1: str | None,
            arg2: str | None,
            database: str | None,
            trigger: str | None,
        ) -> int:
            nonlocal alters
            if action == sqlite3.SQLITE_ALTER_TABLE:
                alters += 1
                if alters == 2:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        before = list(connection.iterdump())
        connection.set_authorizer(authorizer)
        with pytest.raises(sqlite3.DatabaseError):
            initialize_schema(connection)
        connection.set_authorizer(None)
        assert list(connection.iterdump()) == before
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert not connection.in_transaction
        initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)


@pytest.mark.parametrize(
    "reference,expected",
    [("Growth", "TAG-003"), ("TAG-003", "TAG-003"), (" \u2003", None), (None, None)],
)
def test_export_resolution(reference: str | None, expected: str | None) -> None:
    assert (
        resolve_hashtag_reference(
            reference, load_hashtag_snapshot(DATA / "hashtags.csv")
        )
        == expected
    )


@pytest.mark.parametrize(
    "change",
    ["inactive", "duplicate_id", "duplicate_name", "flag", "bad_id", "ambiguous"],
)
def test_invalid_hashtag_snapshot_or_reference(tmp_path: Path, change: str) -> None:
    values = rows("hashtags.csv")
    if change == "inactive":
        values[2]["active"] = "FALSE"
    elif change == "duplicate_id":
        values[1]["id"] = values[0]["id"]
    elif change == "duplicate_name":
        values[1]["name"] = values[0]["name"]
    elif change == "flag":
        values[0]["active"] = "yes"
    elif change == "bad_id":
        values[0]["id"] = "TAG-000"
    else:
        values[0]["name"] = "TAG-003"
    path = tmp_path / "tags.csv"
    write_rows(path, values)
    with pytest.raises(SnapshotError):
        resolve_hashtag_reference("TAG-003", load_hashtag_snapshot(path))


@pytest.mark.parametrize("reference", ["Unknown", "TAG-999", " Growth "])
def test_unknown_export_reference(reference: str) -> None:
    with pytest.raises(SnapshotError):
        resolve_hashtag_reference(
            reference, load_hashtag_snapshot(DATA / "hashtags.csv")
        )


def test_populated_import_reopen_update_drift_and_safe_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    values = rows("prompts.csv")
    values[0].update(caption_override=CAPTION, hashtag_set_id="TAG-003")
    values[1].update(caption_override="\u2003\n", hashtag_set_id="\u00a0")
    source = tmp_path / "prompts.csv"
    write_rows(source, values)
    config = AppConfig(tmp_path / "aed.sqlite", tmp_path, "@ask.every.day")
    with open_repository(config) as repository, caplog.at_level(logging.INFO):
        assert (
            import_snapshot(
                repository, source, DATA / "taxonomy.csv", DATA / "hashtags.csv"
            ).imported_count
            == 400
        )
        repository.replace_queue([QueueEntry("AED-0001", 1)])
    with open_repository(config) as repository, caplog.at_level(logging.INFO):
        assert (
            import_snapshot(
                repository, source, DATA / "taxonomy.csv", DATA / "hashtags.csv"
            ).unchanged_count
            == 400
        )
        first = repository.list_posts()[0]
        assert first.caption_override == CAPTION and first.hashtag_set_id == "TAG-003"
        assert repository.list_posts()[1].caption_override is None
        assert repository.list_posts()[1].hashtag_set_id is None
        repository.update_post(
            replace(
                first, caption_override="Changed private copy", hashtag_set_id="TAG-001"
            )
        )
        with pytest.raises(ContentDriftError):
            import_snapshot(
                repository, source, DATA / "taxonomy.csv", DATA / "hashtags.csv"
            )
        assert repository.list_queue() == [QueueEntry("AED-0001", 1)]
    assert CAPTION not in str([r.__dict__ for r in caplog.records])
    assert "Changed private copy" not in caplog.text


def test_late_populated_import_failure() -> None:
    posts = list(
        load_snapshot(
            DATA / "prompts.csv", DATA / "taxonomy.csv", DATA / "hashtags.csv"
        )
    )
    posts[0] = replace(posts[0], caption_override=CAPTION, hashtag_set_id="TAG-003")
    with sqlite3.connect(":memory:") as connection:
        repository = SQLiteRepository(connection)
        connection.execute(
            "CREATE TEMP TRIGGER reject_last BEFORE INSERT ON posts WHEN NEW.id='AED-0400' BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            repository.import_posts(posts)
        assert repository.list_posts() == []


@pytest.mark.parametrize("reference", ["Growth", "TAG-999", "TAG-000"])
def test_cli_invalid_metadata_never_opens_database(
    tmp_path: Path, reference: str
) -> None:
    values = rows("prompts.csv")
    values[-1].update(caption_override=CAPTION, hashtag_set_id=reference)
    source = tmp_path / "custom.csv"
    write_rows(source, values)
    config = tmp_path / "aed.toml"
    config.write_text(
        'database_path="never.sqlite"\nassets_path="assets"\naccount_handle="@ask.every.day"\n'
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ask_every_day.content_import",
            "--config",
            str(config),
            "--source",
            str(source),
            "--taxonomy",
            str(DATA / "taxonomy.csv"),
            "--hashtags",
            str(DATA / "hashtags.csv"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert not (tmp_path / "never.sqlite").exists()
    assert CAPTION not in result.stderr


def test_create_update_and_reopen_metadata(tmp_path: Path) -> None:
    config = AppConfig(tmp_path / "aed.sqlite", tmp_path, "@ask.every.day")
    original = post(caption_override=CAPTION, hashtag_set_id="TAG-003")
    updated = replace(
        original, caption_override="  Another private line\n ", hashtag_set_id="TAG-006"
    )
    with open_repository(config) as repository:
        repository.create_post(original)
        assert repository.get_post(original.id) == original
        assert repository.list_posts() == [original]
        repository.update_post(updated)
        assert repository.get_post(original.id) == updated
    with open_repository(config) as repository:
        assert repository.get_post(original.id) == updated
        repository.update_post(
            replace(updated, caption_override=None, hashtag_set_id=None)
        )
        assert repository.get_post(original.id) == post()


@pytest.mark.parametrize(
    "field,value", [("caption_override", CAPTION), ("hashtag_set_id", "TAG-003")]
)
def test_each_metadata_field_is_drift(tmp_path: Path, field: str, value: str) -> None:
    with open_repository(
        AppConfig(tmp_path / "aed.sqlite", tmp_path, "@ask.every.day")
    ) as repository:
        repository.create_post(post())
        incoming = replace(post(), **{field: value})
        with pytest.raises(ContentDriftError):
            repository.import_posts([incoming])
        assert repository.list_posts() == [post()]


@pytest.mark.parametrize("invalid_tags", [False, True])
def test_cli_explicit_tag_snapshot_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_tags: bool
) -> None:
    import socket

    from ask_every_day.content_import import main

    def reject_network(*args: object, **kwargs: object) -> None:
        pytest.fail("Content import attempted network access")

    application = logging.getLogger("ask_every_day")
    monkeypatch.setattr(application, "handlers", [])
    monkeypatch.setattr(application, "level", application.level)
    monkeypatch.setattr(application, "propagate", application.propagate)
    monkeypatch.setattr(socket, "socket", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    tags = tmp_path / "custom-tags.csv"
    values = rows("hashtags.csv")
    if invalid_tags:
        values[0]["active"] = "not-a-flag"
    write_rows(tags, values)
    config = tmp_path / "aed.toml"
    config.write_text(
        'database_path="aed.sqlite"\nassets_path="assets"\naccount_handle="@ask.every.day"\n'
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "content_import",
            "--config",
            str(config),
            "--source",
            str(DATA / "prompts.csv"),
            "--taxonomy",
            str(DATA / "taxonomy.csv"),
            "--hashtags",
            str(tags),
        ],
    )
    if invalid_tags:
        with pytest.raises(SystemExit) as failure:
            main()
        assert failure.value.code == 1
        assert not (tmp_path / "aed.sqlite").exists()
    else:
        main()
        main()
        with open_repository(
            AppConfig(tmp_path / "aed.sqlite", tmp_path, "@ask.every.day")
        ) as repository:
            assert len(repository.list_posts()) == 400
