"""Additive schema-v3 migration preserves historical data and rolls back as a unit."""

import sqlite3
from pathlib import Path

import pytest

from ask_every_day.schema import SCHEMA_VERSION, initialize_schema

FIXTURES = Path(__file__).parent / "fixtures"


def legacy(connection, version):
    path = FIXTURES / ("sqlite-v2.sql" if version == 2 else "schema_v1.sql")
    connection.executescript(path.read_text())
    columns = "id,prompt,category,subcategory,template"
    for i in range(1, 5):
        connection.execute(
            f"INSERT INTO posts ({columns}) VALUES (?,?,?,?,?)",
            (f"AED-{i:04}", f"Question {i}?", "A", "B", "Classic"),
        )
    for i, status, published, external, error in (
        (1, "Published", 11, "legacy-media", ""),
        (2, "Failed", None, None, "legacy-error"),
        (3, "Publishing", None, None, None),
    ):
        connection.execute(
            "INSERT INTO publications VALUES (?,?,?,?,?,?,?)",
            (i, f"AED-{i:04}", status, 10, published, external, error or None),
        )
    connection.execute("INSERT INTO performance_metrics VALUES (1,12,1,2,3,4,5)")
    connection.execute("INSERT INTO publication_queue VALUES ('AED-0004',9)")
    connection.commit()


def rows(connection):
    return {
        table: connection.execute(f"SELECT * FROM {table}").fetchall()
        for table in (
            "posts",
            "publications",
            "publication_queue",
            "performance_metrics",
        )
    }


@pytest.mark.parametrize("version", [1, 2])
def test_real_legacy_migration_preserves_every_historical_record(version, tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        legacy(connection, version)
        before = rows(connection)
    with sqlite3.connect(path) as migrated, sqlite3.connect(":memory:") as fresh:
        initialize_schema(migrated)
        initialize_schema(fresh)
        after = rows(migrated)
        if version == 1:
            assert after["posts"] == [(*post, None, None) for post in before["posts"]]
        else:
            assert after["posts"] == before["posts"]
        for table in ("publications", "publication_queue", "performance_metrics"):
            assert after[table] == before[table]
        assert (
            migrated.execute("SELECT * FROM publication_checkpoints").fetchall() == []
        )
        assert migrated.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert migrated.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            migrated.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
            == fresh.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
        )
        dump = list(migrated.iterdump())
        initialize_schema(migrated)
        assert list(migrated.iterdump()) == dump


@pytest.mark.parametrize("version", [0, 1, 2])
def test_checkpoint_migration_failure_rolls_back_all_schema_data_and_version(version):
    with sqlite3.connect(":memory:") as connection:
        if version:
            legacy(connection, version)
        before = list(connection.iterdump())

        def authorize(action, name, column, database, trigger):
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_CREATE_TABLE
                and name == "publication_checkpoints"
                else sqlite3.SQLITE_OK
            )

        connection.set_authorizer(authorize)
        with pytest.raises(sqlite3.DatabaseError):
            initialize_schema(connection)
        connection.set_authorizer(None)
        assert list(connection.iterdump()) == before
        assert connection.execute("PRAGMA user_version").fetchone() == (version,)
        assert not connection.in_transaction
        initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)


def test_read_only_status_does_not_migrate_or_modify_v2(tmp_path):
    from ask_every_day.domain import AppConfig
    from ask_every_day.publication_recovery import snapshot
    from ask_every_day.repository import open_repository

    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        legacy(connection, 2)
    before = path.read_bytes()
    config = AppConfig(path, tmp_path, "@ask.every.day")
    with open_repository(config, read_only=True) as repository:
        report = snapshot(repository, 3).report()
        assert report["checkpoint"] == "legacy_uncheckpointed"
        assert report["remote_outcome_uncertain"] and report["operator_action_required"]
        assert repository.get_checkpoint(1) is None
        with pytest.raises(sqlite3.OperationalError):
            repository.clear_queue()
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
