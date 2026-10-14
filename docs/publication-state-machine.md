# Local publication transitions (AED-020)

`PublicationService` owns transition policy. It uses `Repository`, never SQL.
`PublicationState` is a derived view, not a second persisted status column:

- No attempts and no queue entry: **Ready** (locally available, not a new editorial
  approval claim).
- No attempts and a queue entry: **Queued**.
- Attempt history: the latest attempt supplies **Publishing**, **Published** or
  **Failed**. All earlier attempts must be Failed, IDs must be unique, chronological
  order must be valid, and no queue entry may coexist with attempts.

Conflicting legacy data fails closed with `InvalidTransition`; the service never
silently repairs it or discards history. All reads check that the Post exists.

| Allowed transition | API | Persistence |
| --- | --- | --- |
| Ready → Queued | `enqueue(QueueEntry)` | Store explicit position, preserving other entries |
| Queued → Publishing | `begin_publish(post_id, attempted_at)` | Create attempt and remove queue entry atomically |
| Publishing → Published | `record_success(publication_id, published_at, external_id=None)` | Update that active attempt's result |
| Publishing → Failed | `record_failure(publication_id, error=None)` | Update that active attempt's result |

Every other pair, including self-transitions, is rejected. Published is terminal.
Failed is also terminal for now: an error can represent an uncertain remote side
effect, so neither Failed → Queued nor Failed → Publishing is exposed. Operator
reconciliation/retry policy belongs to AED-024. No automatic retry or reset exists.
A pre-existing, coherent sequence of failed attempts followed by one active attempt
can finish; earlier failure records remain untouched. The service does not itself
create multiple attempts for a post until a future safe retry policy exists.

All timestamps are supplied explicitly; no hidden wall clock. Existing domain
validation rejects naive timestamps and success times before the attempt instant.
SQLite preserves exact UTC microseconds, including daylight-saving folds. Failure
has no new timestamp field because the existing attempt model does not define one.
Unknown post/attempt IDs raise `KeyError`; invalid local transitions raise
`InvalidTransition`; domain validation and persistence failures retain their types.

## Atomicity and usage

```python
from datetime import UTC, datetime
from ask_every_day.config import load_config
from ask_every_day.domain import QueueEntry
from ask_every_day.publication_service import PublicationService
from ask_every_day.repository import open_repository

with open_repository(load_config()) as repository:
    service = PublicationService(repository)
    service.enqueue(QueueEntry("AED-0001", 1))
    attempt = service.begin_publish("AED-0001", datetime.now(UTC))
    # No remote call is implemented here. Persist an outcome only when known.
```

The repository gains one explicit `transaction()` context. `BEGIN IMMEDIATE`
serializes state reads and writes across SQLite connections; existing write methods
join only that repository-owned transaction. Standalone writes keep their original
per-method commits. Nested contexts and unrelated caller transactions are rejected.
A caught write failure marks the outer transaction for rollback, preventing partial
commits. Interrupted transactions also roll back. Service methods own their context;
do not wrap them in another repository transaction. Queue position conflicts leave
the original queue intact.

Success logs appear only after commit. Structured transition logs carry post and
attempt IDs where available, previous/new states, and exception type on failure.
They never include provider errors, prompt bodies, tokens, or exception messages.
Joined repository writes do not emit premature commit logs. Standard application
logging configuration remains external to the service.

This is **local state validity**, not protection against duplicate Instagram posts.
A crash after a remote action still requires reconciliation. Network publishing,
idempotency/recovery (AED-024), queue generation, scheduling and rendering are
excluded. No schema changes or dependencies are needed. `make setup` and
`make check` exercise the full state matrix, timestamp/history contracts, atomic
rollback, independent connections and safe logging.
