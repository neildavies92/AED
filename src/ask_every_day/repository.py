"""Small persistence contract with one SQLite connection per managed session."""

import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ask_every_day.domain import (
    AppConfig,
    Post,
    Publication,
    PublicationStatus,
    QueueEntry,
)
from ask_every_day.recovery_domain import (
    CheckpointPhase,
    PublicationCheckpoint,
    Resolution,
)
from ask_every_day.schema import SCHEMA_VERSION, initialize_schema

_LOGGER = logging.getLogger(__name__)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_microseconds(value: datetime) -> int:
    """Encode an aware instant without floating-point rounding."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    delta = value.astimezone(UTC) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def from_microseconds(value: int) -> datetime:
    """Decode to UTC, rejecting values outside Python's datetime range."""
    return _EPOCH + timedelta(microseconds=value)


@dataclass(frozen=True)
class PublicationRecord:
    """Database attempt identity paired with its domain snapshot."""

    id: int
    publication: Publication


@dataclass(frozen=True)
class ImportResult:
    """Counts for an atomic import that never overwrites existing content."""

    imported_count: int
    unchanged_count: int


class ContentDriftError(ValueError):
    """Stored editorial content differs from the supplied snapshot."""


class Repository(Protocol):
    """Persistence primitives; workflows and transition policy belong to callers."""

    def transaction(self) -> AbstractContextManager[None]: ...
    def create_post(self, post: Post) -> None: ...
    def import_posts(self, posts: Sequence[Post]) -> ImportResult: ...
    def get_post(self, post_id: str) -> Post | None: ...
    def post_exists(self, post_id: str) -> bool: ...
    def list_posts(self) -> list[Post]: ...
    def update_post(self, post: Post) -> None: ...
    def delete_post(self, post_id: str) -> bool: ...
    def create_publication(self, publication: Publication) -> PublicationRecord: ...
    def get_publication(self, publication_id: int) -> PublicationRecord | None: ...
    def publication_history(self, post_id: str) -> list[PublicationRecord]: ...
    def update_publication(
        self, publication_id: int, publication: Publication
    ) -> None: ...
    def list_publications(self) -> list[PublicationRecord]: ...
    def get_checkpoint(self, publication_id: int) -> PublicationCheckpoint | None: ...
    def create_checkpoint(self, checkpoint: PublicationCheckpoint) -> None: ...
    def update_checkpoint(self, checkpoint: PublicationCheckpoint) -> None: ...
    def replace_queue(self, entries: Sequence[QueueEntry]) -> None: ...
    def list_queue(self) -> list[QueueEntry]: ...
    def next_queue_entry(self) -> QueueEntry | None: ...
    def remove_queue_entry(self, post_id: str) -> bool: ...
    def clear_queue(self) -> None: ...


def _post(row: sqlite3.Row) -> Post:
    return Post(**dict(row))


def _publication(row: sqlite3.Row) -> PublicationRecord:
    return PublicationRecord(
        id=row["id"],
        publication=Publication(
            post_id=row["post_id"],
            status=PublicationStatus(row["status"]),
            attempted_at=from_microseconds(row["attempted_at"]),
            published_at=(
                from_microseconds(row["published_at"])
                if row["published_at"] is not None
                else None
            ),
            external_id=row["external_id"],
            error=row["error"],
        ),
    )


def _publication_values(publication: Publication) -> tuple[object, ...]:
    return (
        publication.post_id,
        publication.status.value,
        to_microseconds(publication.attempted_at),
        to_microseconds(publication.published_at)
        if publication.published_at is not None
        else None,
        publication.external_id,
        publication.error,
    )


class SQLiteRepository:
    """Use open_repository to own initialization and connection lifetime.

    Standalone mutations commit independently; transaction() explicitly groups
    reads and writes. No implicit transaction spans a repository session.
    """

    def __init__(
        self, connection: sqlite3.Connection, *, read_only: bool = False
    ) -> None:
        if read_only:
            connection.execute("PRAGMA query_only = ON")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (1, 2, SCHEMA_VERSION):
                raise ValueError("Unsupported read-only SQLite schema")
        else:
            initialize_schema(connection)
            version = SCHEMA_VERSION
        self._schema_version = version
        self._read_only = read_only
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._transaction_active = False
        self._transaction_failed = False

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Serialize a read/modify/write operation and commit all its writes together."""
        if self._transaction_active:
            raise ValueError("Nested repository transactions are not supported")
        with self._write("transaction"):
            self._transaction_active = True
            self._transaction_failed = False
            try:
                yield
                if self._transaction_failed:
                    raise RuntimeError("Repository transaction is marked for rollback")
            finally:
                self._transaction_active = False

    @contextmanager
    def _write(self, operation: str, **context: object) -> Iterator[None]:
        if self._transaction_active:
            try:
                yield
            except BaseException:
                self._transaction_failed = True
                raise
            return
        if self._connection.in_transaction:
            raise ValueError(
                "Repository operations require no active caller transaction"
            )
        self._connection.execute("BEGIN" if self._read_only else "BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except BaseException as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            _LOGGER.warning(
                "Repository operation failed",
                extra={
                    "event": "repository_read_failed"
                    if self._read_only
                    else "repository_write_failed",
                    "operation": operation,
                    "error_type": type(exc).__name__,
                    **context,
                },
            )
            raise
        _LOGGER.info(
            "Repository operation committed",
            extra={
                "event": "repository_read_completed"
                if self._read_only
                else "repository_write_committed",
                "operation": operation,
                **context,
            },
        )

    def create_post(self, post: Post) -> None:
        with self._write("create_post", post_id=post.id):
            self._connection.execute(
                "INSERT INTO posts (id, prompt, category, subcategory, template, "
                "highlight_text, caption_override, hashtag_set_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    post.id,
                    post.prompt,
                    post.category,
                    post.subcategory,
                    post.template,
                    post.highlight_text,
                    post.caption_override,
                    post.hashtag_set_id,
                ),
            )

    def get_post(self, post_id: str) -> Post | None:
        row = self._connection.execute(
            "SELECT * FROM posts WHERE id = ?", (post_id,)
        ).fetchone()
        return _post(row) if row is not None else None

    def import_posts(self, posts: Sequence[Post]) -> ImportResult:
        """Create missing posts atomically; reject conflicts and extra stored IDs."""
        incoming = {post.id: post for post in posts}
        if len(incoming) != len(posts):
            raise ValueError("Import contains duplicate post IDs")
        with self._write("import_posts", row_count=len(posts)):
            stored = {post.id: post for post in self.list_posts()}
            extra = stored.keys() - incoming.keys()
            if extra:
                raise ContentDriftError(f"Unexpected stored post ID: {min(extra)}")
            for post_id, post in incoming.items():
                if post_id in stored and stored[post_id] != post:
                    raise ContentDriftError(
                        f"Editorial conflict for post ID: {post_id}"
                    )
            missing = [incoming[key] for key in sorted(incoming.keys() - stored.keys())]
            self._connection.executemany(
                "INSERT INTO posts (id, prompt, category, subcategory, template, "
                "highlight_text, caption_override, hashtag_set_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        p.id,
                        p.prompt,
                        p.category,
                        p.subcategory,
                        p.template,
                        p.highlight_text,
                        p.caption_override,
                        p.hashtag_set_id,
                    )
                    for p in missing
                ],
            )
        return ImportResult(len(missing), len(stored))

    def post_exists(self, post_id: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM posts WHERE id = ?", (post_id,)
            ).fetchone()
            is not None
        )

    def list_posts(self) -> list[Post]:
        return [
            _post(row)
            for row in self._connection.execute("SELECT * FROM posts ORDER BY id")
        ]

    def update_post(self, post: Post) -> None:
        with self._write("update_post", post_id=post.id):
            cursor = self._connection.execute(
                "UPDATE posts SET prompt = ?, category = ?, subcategory = ?, "
                "template = ?, highlight_text = ?, caption_override = ?, "
                "hashtag_set_id = ? WHERE id = ?",
                (
                    post.prompt,
                    post.category,
                    post.subcategory,
                    post.template,
                    post.highlight_text,
                    post.caption_override,
                    post.hashtag_set_id,
                    post.id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(post.id)

    def delete_post(self, post_id: str) -> bool:
        with self._write("delete_post", post_id=post_id):
            cursor = self._connection.execute(
                "DELETE FROM posts WHERE id = ?", (post_id,)
            )
        return cursor.rowcount != 0

    def create_publication(self, publication: Publication) -> PublicationRecord:
        with self._write("create_publication", post_id=publication.post_id):
            cursor = self._connection.execute(
                "INSERT INTO publications (post_id, status, attempted_at, published_at, "
                "external_id, error) VALUES (?, ?, ?, ?, ?, ?)",
                _publication_values(publication),
            )
            publication_id = cursor.lastrowid
            if publication_id is None:
                raise RuntimeError("SQLite did not return an attempt ID")
        return PublicationRecord(publication_id, publication)

    def get_publication(self, publication_id: int) -> PublicationRecord | None:
        row = self._connection.execute(
            "SELECT * FROM publications WHERE id = ?", (publication_id,)
        ).fetchone()
        return _publication(row) if row is not None else None

    def publication_history(self, post_id: str) -> list[PublicationRecord]:
        return [
            _publication(row)
            for row in self._connection.execute(
                "SELECT * FROM publications WHERE post_id = ? ORDER BY attempted_at, id",
                (post_id,),
            )
        ]

    def update_publication(self, publication_id: int, publication: Publication) -> None:
        with self._write("update_publication", publication_id=publication_id):
            # Attempt identity stays attached to its original post.
            cursor = self._connection.execute(
                "UPDATE publications SET status = ?, attempted_at = ?, published_at = ?, "
                "external_id = ?, error = ? WHERE id = ? AND post_id = ?",
                (
                    *_publication_values(publication)[1:],
                    publication_id,
                    publication.post_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError("Unknown publication ID or mismatched post ID")

    def list_publications(self) -> list[PublicationRecord]:
        return [
            _publication(row)
            for row in self._connection.execute(
                "SELECT * FROM publications ORDER BY id"
            )
        ]

    def get_checkpoint(self, publication_id: int) -> PublicationCheckpoint | None:
        if self._schema_version < 3:
            return None
        row = self._connection.execute(
            "SELECT * FROM publication_checkpoints WHERE publication_id=?",
            (publication_id,),
        ).fetchone()
        if row is None:
            return None
        values = dict(row)
        values["phase"] = CheckpointPhase(values["phase"])
        values["resolution"] = (
            Resolution(values["resolution"]) if values["resolution"] else None
        )
        for name in ("checkpoint_at", "create_intent_at", "publish_intent_at"):
            values[name] = (
                from_microseconds(values[name]) if values[name] is not None else None
            )
        return PublicationCheckpoint(**values)

    @staticmethod
    def _checkpoint_values(checkpoint: PublicationCheckpoint) -> tuple[object, ...]:
        checkpoint.__post_init__()
        return (
            checkpoint.publication_id,
            checkpoint.account_id,
            checkpoint.username,
            checkpoint.asset_sha256,
            checkpoint.phase.value,
            to_microseconds(checkpoint.checkpoint_at),
            checkpoint.revision,
            to_microseconds(checkpoint.create_intent_at)
            if checkpoint.create_intent_at
            else None,
            to_microseconds(checkpoint.publish_intent_at)
            if checkpoint.publish_intent_at
            else None,
            checkpoint.container_id,
            checkpoint.resolution.value if checkpoint.resolution else None,
            checkpoint.evidence_sha256,
        )

    def create_checkpoint(self, checkpoint: PublicationCheckpoint) -> None:
        with self._write("create_checkpoint", publication_id=checkpoint.publication_id):
            self._connection.execute(
                "INSERT INTO publication_checkpoints VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                self._checkpoint_values(checkpoint),
            )

    def update_checkpoint(self, checkpoint: PublicationCheckpoint) -> None:
        values = self._checkpoint_values(checkpoint)
        with self._write("update_checkpoint", publication_id=checkpoint.publication_id):
            cursor = self._connection.execute(
                "UPDATE publication_checkpoints SET account_id=?,username=?,asset_sha256=?,phase=?,checkpoint_at=?,revision=?,create_intent_at=?,publish_intent_at=?,container_id=?,resolution=?,evidence_sha256=? WHERE publication_id=? AND revision=?",
                (*values[1:], checkpoint.publication_id, checkpoint.revision - 1),
            )
            if cursor.rowcount != 1:
                raise ValueError("Stale or missing publication checkpoint")

    def replace_queue(self, entries: Sequence[QueueEntry]) -> None:
        with self._write("replace_queue", row_count=len(entries)):
            self._connection.execute("DELETE FROM publication_queue")
            self._connection.executemany(
                "INSERT INTO publication_queue (post_id, position) VALUES (?, ?)",
                [(entry.post_id, entry.position) for entry in entries],
            )

    def list_queue(self) -> list[QueueEntry]:
        return [
            QueueEntry(**dict(row))
            for row in self._connection.execute(
                "SELECT post_id, position FROM publication_queue ORDER BY position"
            )
        ]

    def next_queue_entry(self) -> QueueEntry | None:
        row = self._connection.execute(
            "SELECT post_id, position FROM publication_queue ORDER BY position LIMIT 1"
        ).fetchone()
        return QueueEntry(**dict(row)) if row is not None else None

    def remove_queue_entry(self, post_id: str) -> bool:
        with self._write("remove_queue_entry", post_id=post_id):
            cursor = self._connection.execute(
                "DELETE FROM publication_queue WHERE post_id = ?", (post_id,)
            )
        return cursor.rowcount != 0

    def clear_queue(self) -> None:
        self.replace_queue(())


@contextmanager
def open_repository(
    config: AppConfig, *, read_only: bool = False
) -> Iterator[SQLiteRepository]:
    """Open the configured database; its parent directory must already exist."""
    connection = (
        sqlite3.connect(config.database_path.absolute().as_uri() + "?mode=ro", uri=True)
        if read_only
        else sqlite3.connect(config.database_path)
    )
    try:
        repository = SQLiteRepository(connection, read_only=read_only)
        _LOGGER.info("Repository opened", extra={"event": "repository_opened"})
        yield repository
    finally:
        connection.close()
        _LOGGER.info("Repository closed", extra={"event": "repository_closed"})
