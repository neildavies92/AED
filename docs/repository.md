# Local persistence (AED-005)

Use `Repository` as the business-facing protocol and `open_repository` to manage
one SQLite connection. Configuration loading and filesystem provisioning remain
separate; opening a missing database creates the file, but never its directories.

```python
from ask_every_day.config import load_config
from ask_every_day.repository import Repository, open_repository

with open_repository(load_config()) as repository:
    posts = repository.list_posts()
```

Each connection initializes/checks schema version 1 and enables/verifies foreign
keys using AED-004's initializer. The context closes it on success, caller failure,
or initialization failure. No global connections, pooling or asynchronous I/O.
The lower-level `SQLiteRepository(connection)` initializes a caller-owned
connection; its caller must close it and must not run transactions or SQL alongside
repository operations. Prefer the managed factory.

## Contract

- Posts: create, get, exists, deterministic ID-ordered list, update and delete.
  Creating a duplicate raises `sqlite3.IntegrityError`. Unknown gets return `None`,
  exists returns `False`, updates raise `KeyError`, and deletes return `False`.
  Deletes of referenced posts preserve SQLite's foreign-key error.
- Publications: create returns `PublicationRecord(id, publication)`. Each creation
  is a new attempt; the integer database ID is distinct from the post ID. Get
  returns a record or `None`; history returns records ordered by attempt instant,
  then ID for ties. Updates replace a snapshot for the same attempt/post pair.
  Unknown IDs or a mismatched post raise `KeyError`. No transition policy is
  imposed, and updates do not create attempts. There is intentionally no history
  deletion API.
- Queue: replace a complete queue, list by position, read the next entry without
  consuming it, remove by post ID or clear. Empty next returns `None`; remove
  reports whether a row existed. Removal preserves the other positions, including
  gaps. Uniqueness and foreign-key errors remain `sqlite3.IntegrityError`.

Each standalone mutating method is one committed operation; a session is **not** a
transaction around all its calls. An explicit `repository.transaction()` groups
state reads and writes atomically; methods inside it join the outer transaction.
Nested transactions are rejected, and a failed write marks the group for rollback. Queue replacement deletes/inserts inside one transaction,
so a duplicate or missing post rolls the entire replacement back to the old queue.
A caller exception after a successful operation does not undo its earlier commit.
Other database exceptions retain their SQLite types. Public reads return domain
values, never SQLite rows. Input models provide domain validation.

Aware datetimes are stored as signed integer UTC microseconds. Exact timedelta
arithmetic avoids floating-point rounding, preserves pre-epoch dates and
microseconds, and distinguishes daylight-saving folds. Reads reconstruct UTC-aware
datetimes, not the original offset/zone. Naive inputs are rejected; out-of-range
stored timestamps raise `OverflowError`. No schema/domain redesign is required.

Structured standard-library logs record connection lifetime and committed/failed
application-level writes with operation names, record identifiers or queue counts.
Failure logs contain exception type, not exception text or parameter values. Prompt
bodies, publication errors, external IDs and database path strings are not logged.
Logging configuration remains the caller's responsibility.

`make setup` and `make check` cover repository boundary tests and existing checks.
No new frequent developer command or dependency is needed. Metrics storage remains
in the schema; a typed metrics API is deferred until a metrics consumer defines
its requirements. State-machine policy (AED-020), queue generation, content import
(AED-033), rendering, publishing, scheduling and analytics are out of scope.
