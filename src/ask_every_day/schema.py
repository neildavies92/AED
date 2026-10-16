"""Initial SQLite schema and explicit, connection-local initialization."""

import logging
import sqlite3

SCHEMA_VERSION = 2
_LOGGER = logging.getLogger(__name__)

# Timestamps are signed integer UTC microseconds since the Unix epoch.
_SCHEMA = (
    """
    CREATE TABLE posts (
        id TEXT PRIMARY KEY NOT NULL
            CHECK (id GLOB 'AED-[0-9][0-9][0-9][0-9]' AND id != 'AED-0000'),
        prompt TEXT NOT NULL CHECK (length(trim(prompt, char(9,10,11,12,13,32))) > 0),
        category TEXT NOT NULL CHECK (length(trim(category, char(9,10,11,12,13,32))) > 0),
        subcategory TEXT NOT NULL
            CHECK (length(trim(subcategory, char(9,10,11,12,13,32))) > 0),
        template TEXT NOT NULL CHECK (length(trim(template, char(9,10,11,12,13,32))) > 0),
        highlight_text TEXT CHECK (
            highlight_text IS NULL OR (
                length(trim(highlight_text, char(9,10,11,12,13,32))) > 0
                AND instr(prompt, highlight_text) > 0
            )
        )
    ) STRICT
    """,
    """
    CREATE TABLE publication_queue (
        post_id TEXT PRIMARY KEY NOT NULL REFERENCES posts(id),
        position INTEGER NOT NULL UNIQUE CHECK (position > 0)
    ) STRICT
    """,
    """
    CREATE TABLE publications (
        id INTEGER PRIMARY KEY,
        post_id TEXT NOT NULL REFERENCES posts(id),
        status TEXT NOT NULL CHECK (status IN ('Publishing', 'Published', 'Failed')),
        attempted_at INTEGER NOT NULL,
        published_at INTEGER,
        external_id TEXT CHECK (
            external_id IS NULL OR length(trim(external_id, char(9,10,11,12,13,32))) > 0
        ),
        error TEXT CHECK (
            error IS NULL OR length(trim(error, char(9,10,11,12,13,32))) > 0
        ),
        CHECK ((status = 'Published' AND published_at IS NOT NULL)
            OR (status != 'Published' AND published_at IS NULL)),
        CHECK (published_at IS NULL OR published_at >= attempted_at),
        CHECK (error IS NULL OR status = 'Failed')
    ) STRICT
    """,
    "CREATE INDEX publications_post_id ON publications(post_id)",
    """
    CREATE TABLE performance_metrics (
        publication_id INTEGER NOT NULL REFERENCES publications(id),
        recorded_at INTEGER NOT NULL,
        reach INTEGER NOT NULL CHECK (reach >= 0),
        likes INTEGER NOT NULL CHECK (likes >= 0),
        comments INTEGER NOT NULL CHECK (comments >= 0),
        saves INTEGER NOT NULL CHECK (saves >= 0),
        shares INTEGER NOT NULL CHECK (shares >= 0),
        PRIMARY KEY (publication_id, recorded_at)
    ) STRICT
    """,
)


_METADATA_COLUMNS = (
    "caption_override TEXT CHECK (caption_override IS NULL OR "
    "length(trim(caption_override, char(9,10,11,12,13,28,29,30,31,32,133,160,5760,"
    "8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288))) > 0)",
    "hashtag_set_id TEXT CHECK (hashtag_set_id IS NULL OR "
    "(hashtag_set_id GLOB 'TAG-[0-9][0-9][0-9]' AND hashtag_set_id != 'TAG-000'))",
)


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Initialize version 2 or atomically migrate version 1; enable foreign keys.

    Call on every connection before starting a transaction. Existing caller
    transactions and unversioned nonempty databases are rejected without changes.
    No connection is opened or closed, and no parent directory is created here.
    """
    if connection.in_transaction:
        raise ValueError(
            "initialize_schema requires a connection without a transaction"
        )
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise RuntimeError("SQLite 3.37 or newer is required for STRICT tables")
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled")

    connection.execute("BEGIN IMMEDIATE")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            connection.execute("COMMIT")
            return
        if version not in (0, 1):
            raise ValueError(f"Unsupported SQLite schema version: {version}")
        if version == 0:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone():
                raise ValueError("Cannot initialize a nonempty, unversioned database")
            for statement in _SCHEMA:
                connection.execute(statement)
        for column in _METADATA_COLUMNS:
            connection.execute(f"ALTER TABLE posts ADD COLUMN {column}")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    _LOGGER.info(
        "SQLite schema initialized",
        extra={"event": "sqlite_schema_initialized", "schema_version": SCHEMA_VERSION},
    )
