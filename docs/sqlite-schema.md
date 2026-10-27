# Local SQLite schema (AED-004)

`ask_every_day.schema.initialize_schema(connection)` initializes an empty SQLite
connection and enables **and verifies** foreign-key enforcement. Call it on every
connection, before any transaction. Use `sqlite3.connect(':memory:')` for an
in-memory database or pass a local filename to `sqlite3.connect`. SQLite 3.37+
is required for STRICT tables; no additional Python dependencies are needed.

Initialization is atomic, records `PRAGMA user_version = 3`, and is repeatable.
Version 1 migrates automatically by adding nullable `caption_override` and
`hashtag_set_id` columns to posts. Existing rows receive NULL; all queue, attempt
and metrics records remain intact. Fresh databases follow the same v1 creation
plus ALTER sequence, so final SQL/constraints match migrated databases exactly.
Both ALTER statements and the version update commit together or roll back.
Version 2 additionally migrates to v3 by adding `publication_checkpoints` and its
transition guards; existing rows and all IDs are unchanged. Fresh/v1 databases
follow the same final checkpoint DDL. No historical checkpoints are invented.
Existing version 3 is trusted. Unknown versions and nonempty unversioned databases
are rejected; schema repair remains future work.

Caption overrides must be NULL or nonblank (including Unicode whitespace);
populated text is preserved exactly. Hashtag IDs must be NULL or TAG-001–TAG-999.
Existence/active status is validated against the editorial snapshot on import,
not a filesystem foreign key.
Active caller transactions are rejected without committing their changes. The
initializer owns only its transaction, never the connection or filesystem paths.
It emits a standard-library log record with `event` and `schema_version` fields
only after creating the schema; logging configuration belongs to the application.

| Table | Purpose and identity |
| --- | --- |
| `posts` | Exact Post fields; immutable-pattern `AED-0001`–`AED-9999` primary key |
| `publication_queue` | Post primary key and unique positive one-based position |
| `publications` | Attempt history; independent integer `id`, many attempts per post |
| `publication_checkpoints` | Optional one-to-one durable account/asset/remote-operation context for new attempts |
| `performance_metrics` | Count snapshots keyed by `(publication_id, recorded_at)` |

Foreign keys use SQLite's default NO ACTION behavior, so referenced content or
attempt history cannot be deleted while dependent rows exist. The publications
post index supports the relationship; queue position and snapshot keys have unique
indexes. No destructive cascades are installed.

All timestamps (`attempted_at`, `published_at`, `recorded_at`) are **signed integer
microseconds since 1970-01-01 00:00:00 UTC**. AED-005 must reject naive datetimes,
convert aware inputs to UTC, and use exact timedelta arithmetic (days, seconds,
microseconds), not floating-point `timestamp()` multiplication. Decode using the
UTC epoch plus a timedelta. Original offsets are not preserved. Integer comparison
therefore compares instants, including daylight-saving transitions; offset-bearing
strings cannot enter these STRICT integer columns. The repository must enforce
Python datetime's representable range during conversion.

Published attempts require `published_at >= attempted_at`. Other statuses forbid
`published_at`; only Failed permits an error, and Failed does not require one,
matching the domain. External IDs are optional. Metrics require all five
nonnegative integer counts; zero means measured zero, not missing data. Multiple
snapshots are allowed, but duplicate publication/time pairs are rejected. No
status restriction is imposed on snapshot insertion before metrics workflows exist.

SQL enforces required text, ASCII whitespace-only rejection and case-sensitive
highlight inclusion. Domain validation remains responsible for Python runtime
types and the complete Unicode whitespace definition; SQLite STRICT may coerce
losslessly convertible values. Category/subcategory taxonomy is not duplicated.

Run `make setup` and `make check`; schema tests use isolated databases. Repository
sessions initialize mutable databases; status commands do not. See the recovery
guide before opening an existing production database with the new schema.

## Durable operations — AED-024

The new table retains the approved JPEG SHA256, account ID/username, monotonic
phase/revision, checkpoint/intent times, known container ID, resolution and optional
operator-evidence digest. The successful media ID remains solely in
`publications.external_id`. Checks/triggers reject malformed combinations, stale
revisions, changed bindings, duplicate account/container pairs, deleted checkpoints
and changes to completed/closed results. Completion updates public state and its
checkpoint together. No token, staging URL, raw response or editorial copy is stored.

Use `make migrate-state` to back up SQLite and the palette together before migration.
Default status/reconciliation opens support v1/v2/v3 read-only without initializing
or migrating them. See [publication recovery](publication-recovery.md) for rollback,
legacy attempts, evidence and crash-window handling.
