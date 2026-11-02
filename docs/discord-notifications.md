# Discord operational notifications — AED-061

Notifications are optional and disabled when no private webhook configuration exists.
The Docker manual and scheduled commands use the same observer after the publishing
CLI has released its database connection/transactions and publication lock. Discord
is not a publishing dependency and cannot authorize, retry or undo Instagram work.
The 19:00 Europe/London trigger, 30-minute window and 30-day consent are unchanged.

## Events and authority

- `publication_succeeded`: a separate read-only SQLite snapshot confirms Published,
  a completed checkpoint and a known media ID. The timestamp is the stored publication
  time in Europe/London. An exit code alone is insufficient. A cleanup error after a
  verified completed checkpoint does not turn success into failure.
- `publication_failed`: known preflight failures, local container preflight failures,
  or a durably closed failed attempt. Authentication, configuration, transient provider
  rejection and local infrastructure classifications use existing safe diagnostics.
- `publication_reconciliation_required`: uncertain attempts or unverified results,
  with the known checkpoint/IDs. The message requires inspection and reconciliation,
  never automatic replay. Missing/inaccessible state is handled conservatively.

Messages contain only constrained operational values: account, Post/attempt IDs,
London timestamp, correlation ID, checkpoint, attempt-created certainty, category,
HTTP status and sanitized Meta error code. No captions, provider prose, media URLs,
credential values, headers or private file contents are included. All Discord mentions
are disabled. One embed is bounded to 4 KiB and uses fixed operator instructions.

## Provision the owner's existing webhook

Do this only after review/merge and an approved [fresh Docker deployment upgrade](local-operations.md#operator-controlled-upgrade).
Changing application code/image requires the existing fresh policy/host authorization;
this PR does not deploy, activate scheduling or access production secrets.

Use a private ordinary text channel's incoming webhook. The accepted URL is an HTTPS
Discord `discord.com` (also `ptb.discord.com`/`canary.discord.com`) `/api/webhooks/ID/TOKEN`
or `/api/v10/webhooks/ID/TOKEN` URL. Other hosts, redirects, ports, userinfo, query strings,
fragments and extra route segments are rejected. Forum/thread webhooks are not supported.
Do not put the value in shell arguments, environment variables, Compose YAML, `.env`,
source control or chat. No new webhook needs to be created.

```sh
AED_DEPLOY="$HOME/Library/Application Support/AskEveryDay/YOUR-REVIEWED-DEPLOYMENT"
make docker-notification-configure DIRECTORY="$AED_DEPLOY"
```

The existing Compose helper supplies an interactive TTY. Paste the existing URL only
at the **hidden** prompt. Successful provisioning prints `notifications_configured=true`.
It neither contacts Discord nor accesses Instagram credentials/SQLite. It reuses the
FileSecretBackend permission checks and atomic persistence in the existing
`credentials` bind mount: directory mode 0700, files 0600, owner UID 10001 inside Docker.
The secret is stored under a separate hashed service namespace, never the Instagram
credential service. It survives every one-shot container invocation and host restart.
No extra mount, container environment value or LaunchAgent secret is required.

Explicit operator delivery test:

```sh
make docker-notification-test DIRECTORY="$AED_DEPLOY"
# Equivalent:
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" notification-test
```

This sends only a clearly labelled `notification_test`. It does not load application
config, open SQLite, render, load Instagram credentials, contact Meta, consume content
or enable scheduling. Expect sanitized JSON with `delivery_result=delivered` and a
2xx HTTP status, **and verify the message appears in the private channel**. A disabled,
failed or uncertain test exits nonzero. Each explicit test is a new notification.
Development/CI use injected synthetic transports; they do not perform this live test.

To disable notifications while preserving delivery receipts and Instagram credentials:

```sh
make docker-notification-disable DIRECTORY="$AED_DEPLOY"
```

Re-run the hidden configuration command to re-enable or rotate the webhook. Do not
remove receipt files to replay an alert. A locally stored URL is not evidence that
Discord still accepts it or that the intended channel is correct.

## Delivery, failure and deduplication

A separate notification receipt is reserved and fsynced **before** network delivery.
A short nonblocking file lock serializes admission only; no publication lock or SQL
transaction is held during Discord HTTP. The notification receipt never changes
content eligibility, queue, palette, checkpoints or publication status.

Attempt-based keys combine event, durable attempt ID, Post ID and original attempt
time, so recreating a container or moving unchanged state does not resend the same
terminal alert. A later different event (for example reconciliation followed by
confirmed completion) is distinct. Scheduled preflight alerts without an attempt are
limited to one per event/London day; manual invocations use fresh correlation IDs.
If state cannot be read, correlation/day fallback is conservative and cannot provide
full attempt-based deduplication until state is available again.

The adapter requests Discord server confirmation with `wait=true`. Successful 2xx
responses are delivered; ordinary 4xx/redirects are rejected; 429 is rate-limited;
5xx and connection/timeouts are uncertain. Only an explicit 429 with numeric
`Retry-After` from 0 to 1 second permits one further request. Longer/malformed delays
are recorded without waiting/retrying. There is no 5xx/timeout retry loop.
A disposable worker has two-second socket timeouts and a six-second parent deadline
covering DNS/TLS and any permitted delay. Only that worker is terminated at its
notification deadline, never the publisher. The URL travels on stdin, not argv;
worker stderr and raw response bodies are never forwarded. Proxy inheritance and
redirects are disabled. See Discord's [webhook API](https://docs.discord.com/developers/resources/webhook)
and [rate-limit contract](https://docs.discord.com/developers/topics/rate-limits).

Delivery is best-effort, **not exactly once**. A process interruption after reservation
can lose an alert; a timeout can mean Discord received it without confirmation. Such
receipts suppress automatic replay. A process killed before reaching the observer
can also lose its notification. There is no replay queue, broker or background worker.
A notification failure never changes the publishing command's original result or
triggers an Instagram retry. SQLite remains the publication authority; inspect it
before responding to an alert or its absence.

`notification_delivery` JSON events record safe event/correlation/Post/attempt IDs,
delivery result, HTTP status and duration. `duplicate_suppressed`, `configuration_error`,
`rate_limited`, `rejected` and `uncertain` distinguish delivery outcomes.
The existing bounded `state/manual.jsonl` / `state/scheduler.jsonl` remain detailed
operator diagnostics; the host receipt still governs scheduled admission.

During an upgrade, preserve the **latest entire private credential directory**, including
Discord config and receipt files, alongside current Docker state and host receipts.
These private files are intentionally excluded from state bundles and Docker builds.
Follow the stopped-writer/owner-permission rules in the upgrade runbook; never restore
stale tokens or receipts. Each event adds one small private receipt file; no automatic
pruning is performed because deletion would weaken deduplication.

## Boundaries and acceptance

An unavailable Mac, launchd or Docker daemon cannot send an in-container alert.
Failures before the container can start remain in host logs; a hard container/process
kill can prevent reporting. An independent off-host watchdog, alert scheduling for
expiry/inventory, and automated coordinated backups/restores remain AED-060 work.
No remote hosting or new timer is introduced here.

AED-061 stays In Progress until code review and an owner-authorized real webhook test
are confirmed. AED-058 still requires a genuine unattended Instagram publication with
a completed checkpoint/media ID and correct queue/palette advancement. Neither green
CI nor a Discord test satisfies that acceptance.

## Investigated Actions failure

[Container run 41355264358](https://github.com/neildavies92/AED/actions/runs/41355264358)
at merged PR #53 commit `5f3a4ea` failed in its first attempt while downloading pinned
Debian packages: `snapshot.debian.org` returned HTTP 503 / `TooManyRequests` / `No healthy
backends` for fonts-liberation2 and XCB/X11 packages. It did not reach application tests.
The existing second attempt passed without a source change. Dockerfile now allows
three bounded apt acquisition retries and a 30-second HTTPS timeout in both package
installation stages. The snapshot, package/font pins, checksum verification and CI
checks are retained; there is no fallback to unpinned repositories or skipped checks.
