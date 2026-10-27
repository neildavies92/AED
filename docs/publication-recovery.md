# Publication recovery — AED-024

## Existing lifecycle and crash-window review

The main baseline `bb1767b` commits Queued → Publishing, creates a container,
checks readiness, publishes once, then commits Published with the returned media
ID. IDs are currently process-local until the last commit. The queue is consumed
at begin; any unresolved Publishing record blocks the next publication. Staging
is held through the operation and normal exception cleanup, and exact interactive
approval binds the Post, account, copy, colour sequence and JPEG hash.

The following design was recorded before implementing recovery changes:

| Window | Required durable evidence and safe restart behaviour |
| --- | --- |
| 1. Queued → Publishing commit | Atomically create the attempt and Prepared context, remove queue once; rollback leaves Queued. |
| 2. Before create request | Commit CreateIntent first; a crash even before dispatch is conservatively uncertain. |
| 3. During create request | CreateIntent survives; never automatically create another container. |
| 4. Container created, response lost | Same uncertain CreateIntent; there is no known container ID to invent or search by copy. |
| 5. Container response received, before checkpoint | Persist ID immediately; if that commit fails, retain CreateIntent and report any known ID without retry. |
| 6. During processing/polling | ContainerKnown survives; read-only status is possible; publishing still requires a separate durable intent. |
| 7. Before media_publish | Commit PublishIntent before dispatch. No write transaction spans the request. |
| 8. During media_publish | PublishIntent survives; no automatic second publish request. |
| 9. Remote success, media response lost | Read known container; PUBLISHED is evidence of publication, not a media-ID mapping. Leave unresolved without conclusive mapping. |
| 10. Media response received, before local success | Save media ID and Published in one transaction with the completion checkpoint; failures retain PublishIntent and report known IDs. |
| 11. Local success committed, before cleanup/output | Published/Completed survive reopening; report cleanup separately and never republish. |

## Persistence design

Use one additive, one-to-one `publication_checkpoints` table in schema v3.
`publications` remains the only public-state and successful-media-ID source.
Store account binding, approved JPEG digest, phase, known container ID, independent
create/publish intent times, checkpoint time, revision and a bounded resolution
classification/evidence digest. No token, capability URL, raw HTTP body or copy is
stored. Existing v1/v2 records migrate without invented checkpoints.

Prepared → CreateIntent → ContainerKnown → PublishIntent → Completed is monotonic.
An explicitly proven pre-publish failure can close the attempt as terminal Failed;
no recovery action requeues, deletes history, resumes a POST or bypasses approval.
All checkpoint mutations own short SQLite transactions; recovery uses exact
snapshot/revision checks and the existing per-database advisory lock.

Read-only inspection is the default. Provider GETs require an explicit remote
inspection option; development and tests use fakes only. Local mutation requires
account-bound evidence and an exact interactive confirmation of one snapshot.
A lost media_publish response cannot be reconstructed by caption/time/image
similarity. Independent captured-response evidence can be attached and verified;
otherwise an outcome remains unresolved.

Palette recovery validates the original assignment/digest and resulting successful
history inside the same SQLite success transaction, without rewriting images or
advancing a failed attempt's colour. SQLite and the palette sidecar must be backed
up/restored together. This prevents automatic duplicate retries, not all possible
remote duplication or provider-enforced exactly-once delivery.

## Operator commands

```sh
make publication-status
make publication-status ATTEMPT_ID=7 SHOW_IDS=1
make reconcile-publication ATTEMPT_ID=7
```

These defaults are offline and read-only, including on schema v1/v2. They never
initialize/migrate the database, load Keychain credentials, stage media, change an
attempt, or retry a request. Unknown/corrupt data produces an error. Status reports
public state, durable phase/revision/time, resolution, known-ID flags and whether
operator action is required. Live IDs appear only with `SHOW_IDS=1`; no credentials
or editorial/provider payloads appear. A legacy Failed attempt is conservatively
reported as potentially uncertain; it stays terminal.

Explicit provider inspection is:

```sh
make reconcile-publication ATTEMPT_ID=7 REMOTE=1
```

This uses the current unexpired local credential without refreshing/writing it,
verifies the exact stored professional account and username, then reads the known
container. A known media ID can also be checked for owner, type and reported media
timestamp. All calls use Instagram Login's versioned `graph.instagram.com` host.
Lookup errors, a missing media object or non-PUBLISHED status never prove that a
publish POST failed. PUBLISHED is reported separately as observed remote success,
while an unbound published-media identity remains unresolved. No GET changes SQL.

### Complete an independently verified success

If the original media_publish response was captured independently (for example,
its safe IDs were retained after a local commit failure), create a private JSON
mapping containing **only** the following fields. These are illustrative synthetic
IDs; use the actual attempt/account/container/media values from correlated request
and response evidence, never from caption/time/image matching:

```json
{
  "attempt_id": 7,
  "account_id": "1001",
  "container_id": "2001",
  "media_id": "3001",
  "source": "captured_publish_response"
}
```

No token, request URL, HTTP headers, raw response body or free-text attachment is
accepted. Files must be bounded regular files without symlinks, duplicate keys or
extra fields. Only their SHA256 digest is persisted, alongside the actual media ID
in `publications.external_id` and `operator_response` resolution in the checkpoint.

```sh
make reconcile-publication ATTEMPT_ID=7 REMOTE=1 \
  RESOLVE=published EVIDENCE=/private/path/response-evidence.json
```

This previews a proposed local mutation without changing state. The attempt must
still be Publishing with a durable PublishIntent and known container. Account,
attempt and container must match the captured mapping; the container must report
PUBLISHED and the media GET must identify the exact ID, expected owner, IMAGE type
and an aware timestamp consistent with the attempt. A media ID already assigned
to another Published attempt is rejected.

Then run with `CONFIRM=1` in an interactive terminal and type the exact challenge
shown **in that run**. The command presents the exact attempt, current revision,
account/container/media IDs, proposed result and reported timestamp. Confirmation
attests that the independently captured response really maps this request to that
result. This is a human evidence trust boundary: the application cannot
cryptographically authenticate an arbitrary local mapping file. A newly typed
media ID or a visual guess is not acceptable proof.

Before committing, the command rechecks provider facts with GETs, rejects changed
facts, and compares the complete persisted snapshot inside the result transaction.
It writes Published, the reported media timestamp/media ID, and Completed checkpoint
atomically. The original palette assignment/hash and resulting success ordering
must validate inside that same transaction; any disagreement rolls back the result.
It then reopens SQLite to verify completion. No recovery path sends a POST.

If a response was lost with no independently conclusive request/result mapping,
leave Publishing unresolved. A public permalink or media listing alone does not
prove which container/attempt created it. Retain any evidence and investigate with
the provider; there is no automatic caption, timestamp or similarity matcher.

### Close a proven pre-publish failure

```sh
make reconcile-publication ATTEMPT_ID=7 REMOTE=1 RESOLVE=failed
make reconcile-publication ATTEMPT_ID=7 REMOTE=1 RESOLVE=failed CONFIRM=1
```

The first command previews only; the second requires a new exact interactive
challenge. Closing is allowed only for a new Prepared checkpoint (no create intent
was ever committed by this application), or a ContainerKnown checkpoint with an
ERROR/EXPIRED container and **no publish intent**. The account is reverified and
facts/snapshot rechecked before atomic Failed/Closed persistence. A CreateIntent
without a known ID or any PublishIntent cannot be closed this way, regardless of
a failed lookup or later ERROR/EXPIRED status. Failed remains terminal: no retry,
requeue, deletion, palette advancement or replacement container is offered. A future
operator-authorized retry policy would need new explicit evidence, history
preservation, approval and durable operations; it is not implemented here.

## Checkpoint and database integrity

New attempts commit Prepared context and queue removal in the same transaction.
CreateIntent and PublishIntent each commit independently before their respective
POST. ContainerKnown commits the returned container ID immediately before polling.
Successful response IDs, Published and Completed commit together. SQLite checks
validate IDs/digests, field combinations and intent chronology; transition triggers
and revisions enforce monotonic progress, immutable account/asset/container binding,
terminal results and retained checkpoint history. An account/container pair cannot
belong to two checkpointed attempts. No uniqueness constraint is retroactively
imposed on historical media IDs. The public state is still derived solely from
`publications` and queue membership, not a second recovery status table.

Any transport/provider/schema error after a POST leaves its durable intent/context
in place. The caller receives safe known attempt/container/media IDs where possible;
never assume an error means no remote side effect happened. If the returned media
ID cannot be committed, it may exist only in captured functional output. If the
process dies between receiving an ID and its immediate commit, that ID can still
be lost. The prior intent prevents blind replay but cannot reconstruct the ID.
Completed results survive cleanup/output failure and cannot be selected again.

The per-database advisory lock is shared by preparation, confirmed publication,
backup/migration and confirmed recovery. It coordinates these CLI instances,
including resolved symlink aliases, not independent applications or manual SQL.
SQLite phase/revision checks and result transactions remain effective across fresh
connections; no network, staging startup, polling or operator prompt holds a SQLite
write transaction. Default inspections use a read-only connection/snapshot.

## Backup and migration

Before the first mutable command on the new release, stop other independent
writers and run:

```sh
make backup-state
make migrate-state
```

`backup-state` takes a read-only SQLite backup and an exact copy of the palette
sidecar under the publication lock. A private directory under ignored
`output/state-backups/` contains `state.sqlite3`, its optional
`state.sqlite3.palette.json`, and a manifest with hashes/schema version. SQL
integrity and foreign keys are checked. Absence of a sidecar is recorded, not
silently replaced with a new sequence. Canonical image files must also be retained
or backed up; the command does not regenerate or overwrite them. Credentials stay
in Keychain and are not copied into SQL/backups.

`migrate-state` makes its own coordinated backup first, then runs the existing
schema initializer under the same lock. If backup fails, migration does not start.
Ordinary mutable repository opens also run the transactional initializer, so use
this explicit backup/migration command before normal publishing commands. Merging
or installing the code itself does not open the production database or publish.

Schema v1 → v3 keeps the existing metadata ALTERs and adds the checkpoint table;
v2 → v3 only adds the table/triggers. Existing Posts, queue, publication IDs,
Published/Failed/Publishing rows, external IDs and metrics are unchanged. No
checkpoints are fabricated for older attempts. Migration is atomic and repeatable;
a failed DDL/trigger/version change rolls back the whole migration. Verify
`PRAGMA user_version` (3), `PRAGMA integrity_check` (ok) and
`PRAGMA foreign_key_check` (empty). Unknown schemas fail closed.

Legacy Publishing attempts have no reliable account/dispatch context and remain
unresolved. Their status can be inspected offline, but this recovery command will
not guess an account/container or turn them into Failed/Published. Preserve backups
and independent evidence for a separately reviewed legacy reconciliation procedure.
Legacy Published and Failed history is preserved and remains terminal.

Restore SQL and its matching sidecar together, with all application writers stopped
and the correct configured filenames. Never restore an older pre-publication backup
to forget later remote actions or make a Post eligible again. A lost database or
mismatched backup can lose intent/evidence; recovery still requires independent
remote reconciliation. Palette mismatches block future themed publications and
recovered success; restore the matching metadata rather than resetting the bag or
re-rendering an attempted image.

## Provider evidence and limitations

Official Meta references checked on 26 October 2026 support both Login families;
this implementation uses only the Instagram Login column and v26.0 host/contracts.

- [Instagram Container reference](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-container/): documents id/status/status_code, the five statuses, and no edges. No reliable container-to-published-media-ID lookup is documented there.
- [Content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/): documents container status GETs and the published-media ID returned by media_publish. PUBLISHED reports the container's media was published; it does not return the lost media ID.
- [Instagram Media reference](https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media): documents Instagram Login GET of id, owner, media_type and timestamp. Owner is available for media created by the querying app user; absent/invalid owner fails verification here.

The guarantee is **no blind automatic retry, no new selection while unresolved,
and no local success without known response/evidence**. There is no claimed
provider idempotency key, distributed transaction, universal recovery of lost IDs,
or true exactly-once delivery. No general retry engine, unsafe reset, delete or
requeue command exists. No live provider read or POST is needed to accept the
network-free test suite; this change does not require another Instagram post.

AED-027 development can follow owner review, merge, integrated-main CI and
backup/migration/recovery acceptance. An unattended scheduler still needs its own
stop/notification/approval policy for unresolved outcomes; these safeguards do not
enable scheduling or make retries safe by themselves. AED-027/028/052 stay Backlog.

## Verification

The unchanged main baseline passed `make setup` and `make check` with 1,139 tests.
After implementation, 245 focused recovery/migration/client/publish-next tests
passed. Final `make setup` and `make check` passed with **1,244 tests**, Ruff lint,
Ruff format (110 files) and Pyright (zero errors/warnings); `git diff --check` passed.
Existing rendering, palette, OAuth and publication regression tests are included.
Crash/concurrency tests use committed temporary SQLite databases, fresh connections
and fake provider/staging/credential boundaries. No live Instagram GET/POST,
Keychain access, production migration, history edit, scheduler or publication was
used for acceptance. Added text was reviewed for credentials and live provider IDs.
Production backup/migration/recovery acceptance remains an owner-controlled step
after review and merge; AED-024 stays In Progress until that and main CI complete.
