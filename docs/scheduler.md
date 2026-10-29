# Daily publishing — AED-027

The scheduler is a portable one-shot application workflow. It is **disabled by
default**. Neither installation, setup, generating a LaunchAgent nor starting the
container authorizes a publication. Production manual and scheduled publication
run only inside Docker. Native `make publish-next CONFIRM=1` and `make schedule-run`
fail closed. Docker manual publication retains its exact per-post interactive
approval. See the [shared runtime contract](runtime-contract.md).

## Inspect and explicitly authorize

After merge/main CI, follow the [Docker-only automation runbook](docker-automation.md)
to prepare and verify the sole private deployment. From the reviewed checkout,
select that deployment explicitly for nonpublishing inspection:

```sh
AED_DEPLOY='/absolute/private/deployment'
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" schedule-preview --data-dir /var/lib/aed/data
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" schedule-check-credentials
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" schedule-status --data-dir /var/lib/aed/data
```

The local Docker Desktop policy is **19:00–19:30 Europe/London**, with 30-day
authorization. The generic development `schedule.example.toml` and deferred Linux
deployment examples do not configure this private deployment. The portable engine
validates hours/minutes, 1–120-minute windows that cannot cross midnight, and
1–366-day authorization. Docker Desktop's host adapter additionally requires its
exact 19:00 policy. Late staging/polling fails closed rather than publishing outside
the window; no missed-day bulk backfill is implemented.

`preview` reads local SQL/approved CSV/copy/config and renders a fixed in-memory
specimen to fingerprint the effective font/Cairo output. It does not write a
canonical asset, create authorization, load credentials or contact a provider.
`check-credentials` reads local credential metadata through the selected backend;
it never refreshes the token, contacts Meta, stages media or changes SQL/policy.

Only after completing the runbook's fresh-state cutover, credential/image review
and sole-authority acceptance, the owner can opt in from an interactive terminal:

```sh
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" --interactive schedule-enable --data-dir /var/lib/aed/data
```

It shows the account, policy fingerprint, queue count, time/window, expiry and exact
`AUTHORIZE DAILY @ask.every.day ...` challenge. Type that exact challenge in that
run; `yes`, a flag, piped input or a non-TTY cannot enable it. The configuration,
source inputs and persisted state are rechecked after the prompt. Enabling stores
private durable authorization but does not publish. It authorizes future canonical
images generated from the bound renderer/palette and approved snapshot; it is
broader than approving one manually viewed JPEG. Inspect representative images
before opting in. Renew only by reviewing and repeating this explicit command.

Authorization binds the exact DB Posts to the approved CSV snapshot, all copy and
hashtag inputs, original queue order/positions, baseline publication history,
account, paths, palette/config, cadence, runtime source/templates/bundled fonts and
an actual reference PNG from the effective container renderer/fonts. Normal
ordered queue-prefix consumption and successful palette progression are allowed.
Changes to content, copy, queue generation/reordering, config, renderer, font
output, code or prior history require reapproval. Failed/uncertain new attempts
also block policy continuation; reconcile safely and explicitly review any renewal.
Moving to another Docker deployment changes paths/runtime and requires new approval.

```sh
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" schedule-status --data-dir /var/lib/aed/data
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" schedule-disable --data-dir /var/lib/aed/data
```

The separately approved host trigger invokes `schedule-run` headlessly after
authorization; do not issue a manual run merely to test activation. `status` is
read-only and revalidates active
policy. `disable` explicitly revokes future unattended authorization without a TTY;
it neither edits publication history nor interrupts an already held publishing
lock. If another operation holds that lock, revocation fails visibly; stop/wait for
that operation and retry revocation. Revoked/absent state reports disabled. Corrupt,
expired or drifting state fails closed. The ignored `<database>.schedule.json` is
0600, strictly parsed/bounded and replaced atomically with fsync. Restrict access
to the whole state directory/host: a local administrator who edits files or code
is outside this authorization boundary. Filesystem permissions are not signatures.

## Daily safety and recovery

Under the existing per-database publication lock, each invocation checks active
policy, London execution window, SQL history/daily guard and unresolved attempts.
It prepares the next canonical themed JPEG through the existing renderer, rechecks
the exact hash, obtains a usable credential, verifies account/quota, stages through
the existing Cloudflare adapter and calls the existing checkpointed publisher.
Policy/config/source/window/day checks run again before beginning an attempt and
immediately before each POST. No SQLite write transaction spans network or prompts.
A final fresh SQL connection verifies Published and the media ID after cleanup.

A prior manual Published record on the same London date counts. Both publication
completion date and other attempts' start dates are checked conservatively. Once
an attempt exists on a local day, another new attempt that day is blocked even if
that attempt closed Failed. Unresolved Publishing blocks all future days. SQL
history is the durable publication authority; there is no process-memory cursor.
The host's separate invocation receipt only prevents repeated trigger admission,
including preflight failures; it never substitutes for SQLite history. Repeated
triggers, restarts and DST fall-back folds cannot
bypass the guard. Future-dated history/clock reversal fails closed.

Spring's skipped local times have no execution window; no late catch-up occurs.
An asleep/offline host can miss its window. A host trigger later on another date
can publish at most that day's next approved Post; it does not make up missed days.
If staging/polling crosses the window or midnight, the next dispatch is refused.
After a durable intent/container exists the attempt stays unresolved for AED-024
reconciliation; it is never blindly resumed. Stop and inspect failures before
renewing authorization or trying any operator recovery.

Known numeric attempt/container/media IDs are retained in safe failure output and
bounded audit logs, including when a returned media ID cannot commit to SQLite.
They can support independently correlated recovery evidence but do not prove a
mapping by themselves. Preserve evidence before log rotation. Uncertain POSTs
are never automatically retried; a lookup error is not proof of remote absence.
Use [publication recovery](publication-recovery.md), not a reset or requeue.
There is no provider-enforced exactly-once guarantee or coordination across separate
hosts/databases. A remote POST that crosses midnight after its last clock check
cannot be controlled atomically with Meta; persistent unresolved state and the
next-day history guard remain the safe boundary.

## macOS host trigger

The native Mac publishing LaunchAgent is retired. Do not generate, install or
reactivate `com.ask-every-day.publisher`. Native `schedule-run` and direct media
POST entrypoints return `docker_publisher_required` before credentials or staging.
Offline preview/status, disabling authorization and state administration remain
available. The shared engine operates exclusively inside Docker.

Use the [Docker-only automation runbook](docker-automation.md) for the dedicated
`com.ask-every-day.docker-publisher` LaunchAgent, pinned image, explicit cutover,
owner installation/activation and 19:00–19:30 Europe/London policy. This host trigger
invokes this existing engine once; it does not supply publication authorization.

## Backups, acceptance and container integration

Retain SQLite, its matching palette sidecar, canonical assets, schedule config and
authorization together with all writers stopped/coordinated. Existing `backup-state`
copies SQL/palette only; additionally retain assets/schedule files securely. Never
restore an old database to forget remote actions. On restore/cutover keep
unattended authorization disabled, validate history/checkpoints/daily dates and
palette continuity, then review/renew on the sole selected host. Do not copy a
live enabled authorization to a second independent environment. Disable/unload the
old trigger and stop its processes before transferring state; rollback likewise
uses current matching state and exactly one authorized publisher.

Development acceptance uses temporary SQLite, fake credentials/provider/staging,
injected clocks, real commits and fresh-process checks, with no real Meta POST.
The owner production read-only publish-next preview after AED-024 migration passed
on 27 October 2026 without changing DB/palette state. A detached non-TTY process
using this adapter's controlled PATH/absolute Cairo wrapper/venv also completed
`check-credentials` successfully with local Keychain metadata and no refresh/network.
Actual installed LaunchAgent execution, operator-approved activation and first
scheduled live operation remain owner acceptance after merge and green main CI.

AED-053 injects its Linux CredentialStore into this same command after AED-027
merges; cron only triggers it (e.g. every minute, application owns the window).
No cron runs inside the image and no alternate publishing engine is allowed.
Container cutover requires both accepted PRs, credential/bootstrap/storage/rendering
acceptance, coordinated backups, one active environment and explicit live approval.
AED-054 cleanup remains Backlog until that cutover succeeds; preserve Mac historical evidence
and credentials until then. AED-052 staging DNS/uptime work remains separate.

Final validation: `make setup` and `make check` passed with **1,320 tests**, Ruff
lint/format (118 files), Pyright zero errors/warnings and `git diff --check` clean.
The focused scheduler/manual-publisher/logging suite passed 150 tests. A generated
inactive LaunchAgent passed native `plutil -lint`; it was not installed or loaded.
Added text was audited for credentials, secret URLs and live provider identifiers.
