"""Database guarantees that apply even when writes bypass domain models."""

import logging
import sqlite3
from collections.abc import Iterator

import pytest

from ask_every_day.schema import initialize_schema


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    initialize_schema(connection)
    connection.execute(
        "INSERT INTO posts (id,prompt,category,subcategory,template,highlight_text) VALUES (?, ?, ?, ?, ?, ?)",
        ("AED-0001", "Who are you?", "Identity", "Self", "Classic", "you"),
    )
    connection.commit()
    yield connection
    connection.close()


def test_initialization(
    db: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    assert db.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert db.execute("PRAGMA user_version").fetchone() == (3,)
    assert {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    } == {
        "posts",
        "publication_queue",
        "publications",
        "performance_metrics",
        "publication_checkpoints",
    }
    db.execute("PRAGMA foreign_keys = OFF")
    initialize_schema(db)
    assert db.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM posts").fetchone() == (1,)
    with sqlite3.connect(":memory:") as fresh:
        with caplog.at_level(logging.INFO):
            initialize_schema(fresh)
    assert caplog.records[-1].__dict__["event"] == "sqlite_schema_initialized"


@pytest.mark.parametrize("version", [4, 99])
def test_unknown_version(version: int) -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute(f"PRAGMA user_version = {version}")
        with pytest.raises(ValueError, match="Unsupported"):
            initialize_schema(connection)
        assert not connection.in_transaction
        assert connection.execute("PRAGMA user_version").fetchone() == (version,)


def test_nonempty_unversioned_database() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE existing (id INTEGER)")
        with pytest.raises(ValueError, match="nonempty"):
            initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == [
            ("existing",)
        ]


def test_caller_transaction_untouched(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO publication_queue VALUES ('AED-0001', 1)")
    with pytest.raises(ValueError, match="transaction"):
        initialize_schema(db)
    assert db.in_transaction
    db.rollback()
    assert db.execute("SELECT count(*) FROM publication_queue").fetchone() == (0,)


@pytest.mark.parametrize(
    "column,value",
    [
        ("id", "AED-0000"),
        ("id", "AED-12345"),
        ("id", "AED-abcd"),
        ("prompt", None),
        ("category", ""),
        ("subcategory", " "),
        ("template", "\t\n"),
        ("highlight_text", "YOU"),
        ("highlight_text", ""),
    ],
)
def test_post_constraints(db: sqlite3.Connection, column: str, value: object) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(f"UPDATE posts SET {column} = ?", (value,))


def test_post_primary_key(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO posts SELECT * FROM posts")


@pytest.mark.parametrize("position", [0, -1, 1.5, None])
def test_queue_positions(db: sqlite3.Connection, position: object) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO publication_queue VALUES ('AED-0001', ?)", (position,))


def test_queue_uniqueness(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO publication_queue VALUES ('AED-0001', 1)")
    db.execute(
        "INSERT INTO posts (id,prompt,category,subcategory,template,highlight_text) VALUES ('AED-0002', 'Why?', 'A', 'B', 'Classic', NULL)"
    )
    for post_id, position in [("AED-0001", 2), ("AED-0002", 1)]:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO publication_queue VALUES (?, ?)", (post_id, position)
            )


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO publication_queue VALUES ('AED-9999', 1)",
        "INSERT INTO publications (post_id, status, attempted_at) "
        "VALUES ('AED-9999', 'Publishing', 0)",
        "INSERT INTO performance_metrics VALUES (999, 0, 0, 0, 0, 0, 0)",
    ],
)
def test_foreign_keys(db: sqlite3.Connection, sql: str) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute(sql)


def test_referenced_post_cannot_be_deleted(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO publication_queue VALUES ('AED-0001', 1)")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("DELETE FROM posts")


@pytest.mark.parametrize(
    "status,published,error",
    [
        ("Invalid", None, None),
        ("Published", None, None),
        ("Published", 10, "bad"),
        ("Published", 9, None),
        ("Publishing", 10, None),
        ("Failed", 10, "bad"),
        ("Publishing", None, "bad"),
        ("Failed", None, " "),
    ],
)
def test_attempt_consistency(
    db: sqlite3.Connection, status: str, published: int | None, error: str | None
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO publications (post_id, status, attempted_at, published_at, error) "
            "VALUES ('AED-0001', ?, 10, ?, ?)",
            (status, published, error),
        )


def test_multiple_attempts_and_snapshots(db: sqlite3.Connection) -> None:
    for status, published, error in [
        ("Publishing", None, None),
        ("Failed", None, "Timeout"),
        ("Failed", None, None),
        ("Published", 10, None),
    ]:
        db.execute(
            "INSERT INTO publications (post_id, status, attempted_at, published_at, error) "
            "VALUES ('AED-0001', ?, 10, ?, ?)",
            (status, published, error),
        )
    assert db.execute("SELECT count(DISTINCT id) FROM publications").fetchone() == (4,)
    db.execute("INSERT INTO performance_metrics VALUES (4, 11, 0, 0, 0, 0, 0)")
    db.execute("INSERT INTO performance_metrics VALUES (4, 12, 10, 2, 1, 0, 1)")
    assert db.execute("SELECT count(*) FROM performance_metrics").fetchone() == (2,)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO performance_metrics VALUES (4, 12, 10, 2, 1, 0, 1)")
    for column in ("reach", "likes", "comments", "saves", "shares"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"UPDATE performance_metrics SET {column} = -1")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("DELETE FROM publications WHERE id = 4")


@pytest.mark.parametrize("timestamp", ["2026-01-01T10:00:00+01:00", 1.5, None])
def test_timestamps_require_integer_microseconds(
    db: sqlite3.Connection, timestamp: object
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO publications (post_id, status, attempted_at) "
            "VALUES ('AED-0001', 'Publishing', ?)",
            (timestamp,),
        )


def test_initialization_failure_rolls_back() -> None:
    def authorize(
        action: int,
        name: str | None,
        column: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_CREATE_TABLE and name == "publications":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    with sqlite3.connect(":memory:") as connection:
        connection.set_authorizer(authorize)
        with pytest.raises(sqlite3.DatabaseError):
            initialize_schema(connection)
        assert not connection.in_transaction
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
        connection.set_authorizer(None)
        initialize_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
