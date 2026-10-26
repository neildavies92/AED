# Publish the next queued post

Run from the repository root after `make setup`. Keep `aed.toml` and
`instagram-auth.toml` local and ignored. Import the approved snapshot, prepare an
existing queue and render its canonical JPEGs first. This command never generates
or regenerates a queue, renders assets, or changes editorial CSVs.

## Preview

Run `make render-next` first to prepare the next success-sequenced blue background.
A valid bulk-rendered JPEG is a draft until its colour/sequence and byte hash are
persisted by this preparation step. See [background rotation](background-rotation.md)
for the one-time historical-publication bootstrap and metadata backup contract.

```sh
make publish-next
```

The default is a local preview: Post ID, original queue position, exact account,
prompt, background colour/sequence, JPEG path/validity/SHA256, complete caption and hashtags, and offline
Keychain credential readiness. Quota is explicitly `not_checked`. There is no
Instagram request, token refresh, public staging, or publication attempt. Missing
credentials or an invalid asset appear as not ready; an empty queue exits cleanly.
An unresolved Publishing attempt blocks selection, including an attempt already
removed from the queue.

Inspect the JPEG at the displayed path. Copy uses the existing versioned caption
catalog, per-Post profile and hashtag policy. The original one-based queue position
controls caption cadence; remaining entries are never renumbered. Approved caption
overrides remain exact. Hashtags follow a blank line; the composed caption is
limited to 2,200 characters and 30 total hashtags before any publishing request.

## Approve one real publication

```sh
make publish-next CONFIRM=1
```

Both input and output must be interactive terminals. The command displays the
account, Post, full copy, JPEG path and SHA256 again. Inspect that image and type
the exact displayed `PUBLISH ...` challenge. Its fingerprint binds the account,
Post snapshot, queue position, colour/sequence, asset path/bytes and complete caption. `yes`, a
stale challenge, a redirected stdin, or the flag alone cannot authorize a post.
A second post always requires a new confirmation.

After approval, the existing CredentialManager validates/refreshes the Keychain
credential, then the client verifies the exact `ask.every.day` professional account
and its stored professional ID and reads actual publishing quota. No secrets are
printed or stored in SQLite. A per-database advisory lock serializes confirmed
instances of this CLI on macOS/Linux through completion using an ignored
`.publish.lock` sidecar beside the resolved database path; it does not coordinate
other applications writing SQLite directly.

The existing staging context pins and validates the canonical 1080×1350 RGB JPEG,
checks its approved SHA256 before exposing bytes, and verifies unauthenticated
HTTPS, content type, exact bytes and no redirect. A Quick Tunnel can take roughly
80 seconds to become ready on this connection; existing bounded readiness applies.
Keep the computer online. The capability URL is never routine output.

The plan is checked again after staging. Immediately before the first media POST,
PublicationService atomically commits one Publishing attempt and removes that
Post from the queue. The client creates one container, checks readiness at most
five times at one-minute intervals, then publishes once only after FINISHED.
The tunnel stays open throughout processing and publication and closes on context
exit, including errors and operator interruption. No POST is automatically retried.

The returned media ID is stored as Published with an aware timestamp; container
and media IDs remain distinct. The command reopens SQLite to verify the durable
result before reporting success. Functional output includes known IDs for local
reconciliation; operational logs contain only bounded safe context. Do not commit
local output containing live provider identifiers. A successful post is publicly
visible; stopping the tunnel does not delete it. Deleting a post is a separate
operator action.

## Failures and restart

Preflight/staging failure leaves the Post queued with no attempt. Container ERROR
or EXPIRED records terminal Failed. Unknown status, status timeout, interrupted
processing, or any uncertain create/publish POST leaves Publishing and prevents
another CLI publication until reconciled. Never assume a timeout means nothing
was published, create a replacement container, reset state, or requeue the Post.

On a result-write or cleanup failure, error output preserves known attempt,
container and media IDs. A media ID means Meta reported publication even if local
persistence or cleanup failed. Inspect SQLite and provider evidence before any
further action. A Published record is terminal and cannot be selected again.

**AED-024 remains required for recovery:** the current schema persists attempt
state and the successful media ID, but has no durable container ID or remote
operation checkpoint. IDs printed during this process can be lost on a hard
crash. Blocking unresolved attempts prevents blind restart; it does not provide
full cross-crash idempotency or automatic reconciliation. Do not edit SQLite to
work around that block. Abrupt process death can also require operator cleanup
of surviving staging resources; normal exceptions and Ctrl+C use context cleanup.

This ticket supplies an interactive one-post workflow. Scheduling, retries and
unattended approval policy are separate work and are not enabled here.

For explicit paths use:

```sh
uv run python -m ask_every_day.publish_next_cli \
  --config /path/to/aed.toml --auth-config /path/to/instagram-auth.toml \
  --data-dir data
```

Add `--confirm` only for the interactive approved publication flow. See
[authentication](instagram-auth.md), [staging](media-staging.md), and
[publication state](publication-state-machine.md) for their existing contracts.
