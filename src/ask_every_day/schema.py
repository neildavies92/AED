"""Initial SQLite schema and explicit, connection-local initialization."""

import logging
import sqlite3

SCHEMA_VERSION = 3
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


_CHECKPOINT_SCHEMA = (
    """
    CREATE TABLE publication_checkpoints (
        publication_id INTEGER PRIMARY KEY REFERENCES publications(id),
        account_id TEXT NOT NULL CHECK (length(account_id)>0 AND account_id NOT GLOB '*[^0-9]*'),
        username TEXT NOT NULL CHECK (username='ask.every.day'),
        asset_sha256 TEXT NOT NULL CHECK (length(asset_sha256)=64 AND asset_sha256 NOT GLOB '*[^0-9a-f]*'),
        phase TEXT NOT NULL CHECK (phase IN ('prepared','create_intent','container_known','publish_intent','completed','closed')),
        checkpoint_at INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision>=0),
        create_intent_at INTEGER,
        publish_intent_at INTEGER,
        container_id TEXT CHECK (container_id IS NULL OR (length(container_id)>0 AND container_id NOT GLOB '*[^0-9]*')),
        resolution TEXT CHECK (resolution IS NULL OR resolution IN ('publish_response','operator_response','container_error','container_expired','operator_abort')),
        evidence_sha256 TEXT CHECK (evidence_sha256 IS NULL OR (length(evidence_sha256)=64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*')),
        UNIQUE (account_id,container_id),
        CHECK (phase NOT IN ('completed','closed') OR resolution IS NOT NULL),
        CHECK (create_intent_at IS NULL OR create_intent_at<=checkpoint_at),
        CHECK (publish_intent_at IS NULL OR (create_intent_at IS NOT NULL AND publish_intent_at>=create_intent_at AND publish_intent_at<=checkpoint_at)),
        CHECK (
            (phase='prepared' AND create_intent_at IS NULL AND publish_intent_at IS NULL AND container_id IS NULL)
            OR (phase='create_intent' AND create_intent_at IS NOT NULL AND publish_intent_at IS NULL AND container_id IS NULL)
            OR (phase='container_known' AND create_intent_at IS NOT NULL AND publish_intent_at IS NULL AND container_id IS NOT NULL)
            OR (phase IN ('publish_intent','completed') AND create_intent_at IS NOT NULL AND publish_intent_at IS NOT NULL AND container_id IS NOT NULL)
            OR (phase='closed' AND publish_intent_at IS NULL AND (
                (resolution='operator_abort' AND create_intent_at IS NULL AND container_id IS NULL)
                OR (resolution IN ('container_error','container_expired') AND create_intent_at IS NOT NULL AND container_id IS NOT NULL)
            ))
        ),
        CHECK (
            (phase NOT IN ('completed','closed') AND resolution IS NULL AND evidence_sha256 IS NULL)
            OR (phase='completed' AND ((resolution='publish_response' AND evidence_sha256 IS NULL) OR (resolution='operator_response' AND evidence_sha256 IS NOT NULL)))
            OR (phase='closed' AND resolution IN ('container_error','container_expired','operator_abort') AND evidence_sha256 IS NULL)
        )
    ) STRICT
    """,
    """
    CREATE TRIGGER checkpoint_insert_guard BEFORE INSERT ON publication_checkpoints
    WHEN NEW.phase!='prepared' OR NEW.revision!=0 OR NOT EXISTS (
        SELECT 1 FROM publications WHERE id=NEW.publication_id
        AND status='Publishing' AND NEW.checkpoint_at>=attempted_at
    ) BEGIN SELECT RAISE(ABORT,'Invalid initial publication checkpoint'); END
    """,
    """
    CREATE TRIGGER checkpoint_advance_guard BEFORE UPDATE ON publication_checkpoints
    WHEN NEW.publication_id!=OLD.publication_id OR NEW.account_id!=OLD.account_id
      OR NEW.username!=OLD.username OR NEW.asset_sha256!=OLD.asset_sha256
      OR NEW.revision!=OLD.revision+1 OR NEW.checkpoint_at<OLD.checkpoint_at
      OR (OLD.create_intent_at IS NOT NULL AND NEW.create_intent_at IS NOT OLD.create_intent_at)
      OR (OLD.publish_intent_at IS NOT NULL AND NEW.publish_intent_at IS NOT OLD.publish_intent_at)
      OR (OLD.container_id IS NOT NULL AND NEW.container_id IS NOT OLD.container_id)
      OR NOT (
        (OLD.phase='prepared' AND NEW.phase IN ('create_intent','closed'))
        OR (OLD.phase='create_intent' AND NEW.phase='container_known')
        OR (OLD.phase='container_known' AND NEW.phase IN ('publish_intent','closed'))
        OR (OLD.phase='publish_intent' AND NEW.phase='completed')
      )
      OR (NEW.phase NOT IN ('completed','closed') AND NOT EXISTS (
        SELECT 1 FROM publications WHERE id=NEW.publication_id AND status='Publishing'
      ))
      OR (NEW.phase='completed' AND NOT EXISTS (
        SELECT 1 FROM publications WHERE id=NEW.publication_id AND status='Published'
          AND length(external_id)>0 AND external_id NOT GLOB '*[^0-9]*'
      ))
      OR (NEW.phase='closed' AND NOT EXISTS (
        SELECT 1 FROM publications WHERE id=NEW.publication_id AND status='Failed'
      ))
    BEGIN SELECT RAISE(ABORT,'Invalid publication checkpoint transition'); END
    """,
    """
    CREATE TRIGGER checkpoint_delete_guard BEFORE DELETE ON publication_checkpoints
    BEGIN SELECT RAISE(ABORT,'Publication checkpoints preserve history'); END
    """,
    """
    CREATE TRIGGER completed_publication_guard BEFORE UPDATE ON publications
    WHEN EXISTS (SELECT 1 FROM publication_checkpoints WHERE publication_id=OLD.id AND phase IN ('completed','closed'))
    BEGIN SELECT RAISE(ABORT,'Checkpointed result is terminal'); END
    """,
)


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Initialize version 3 or atomically migrate versions 1/2; enable foreign keys.

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
        if version not in (0, 1, 2):
            raise ValueError(f"Unsupported SQLite schema version: {version}")
        if version == 0:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone():
                raise ValueError("Cannot initialize a nonempty, unversioned database")
            for statement in _SCHEMA:
                connection.execute(statement)
        if version < 2:
            for column in _METADATA_COLUMNS:
                connection.execute(f"ALTER TABLE posts ADD COLUMN {column}")
        for statement in _CHECKPOINT_SCHEMA:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    _LOGGER.info(
        "SQLite schema initialized",
        extra={"event": "sqlite_schema_initialized", "schema_version": SCHEMA_VERSION},
    )
