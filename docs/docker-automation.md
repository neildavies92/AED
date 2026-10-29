# Docker-only daily publishing — AED-058

Docker is the exclusive production runtime, including emergency/manual posting.
macOS supplies Docker Desktop and a user LaunchAgent only. The daily policy is
**19:00–19:30 Europe/London**, authorization lasts 30 days, and the existing engine
allows at most one publication attempt per London date. Native publication,
scheduled runs, production staging CLI and media POSTs fail with
`docker_publisher_required`. Native offline rendering/previews, read-only state
inspection, coordinated export/backup, recovery and explicit credential handoff
remain supported.

The target is the first **supervised automatic** post at 19:00 Thursday 29 October
2026. The previously proposed 08:00 manual post is cancelled. This one successful
scheduled post can satisfy AED-057/058/059 when actual Instagram/checkpoint evidence
supports each ticket; no extra manual acceptance post is required. If any gate is
incomplete, leave automation disabled and skip the day.

## Trigger contract

`deployment/desktop_scheduler.py` is a standard-library macOS adapter. It calls the
existing Compose `publisher schedule-run`, which injects FileCredentialStore into
AED-027 and uses AED-059's canonical renderer. It creates no publication database,
queue, token lifecycle or alternative publishing engine. UID/GID 10001 in Docker,
canonical font hashes, persistent SQLite/palette/assets and the common publication
lock remain unchanged.

The dedicated label is `com.ask-every-day.docker-publisher`. Its plist runs an
absolute, validated Python executable with `-I` and an installed script under
private Application Support storage, avoiding the old Desktop shell-wrapper
exit 126 path. It has `Disabled=true`, `RunAtLoad=false`, `KeepAlive=false`, no
periodic restart and no output file that can grow indefinitely. Installing the
plist leaves launchd disabled; activation and application authorization are
separate explicit owner actions.

launchd calendar times use the **host timezone**. The adapter requires the Mac's
system timezone to be Europe/London, then checks aware London dates independently.
`TZ` in a child's environment does not configure launchd's calendar. GMT/BST follow
zoneinfo; a changed host timezone fails closed. Apple's [timed-job documentation](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)
explains that calendar events missed during sleep may arrive on wake. Therefore
this host admits a start only during **19:00:00–19:00:59**, rechecked after preflight.
Late wake/delivery is skipped, even within the application's wider 30-minute window.
Once admitted, the existing application enforces that window before media POSTs.

A private host lock prevents overlapping triggers. A durable invocation receipt
is saved before preflight/dispatch, so failures, interruption, repeated wake events
or host restarts do not retry that date. This receipt records host admission only;
SQLite remains the authority for publication history, manual posts earlier that
day, unresolved outcomes and palette progression. A global named container also
blocks overlapping Docker publishers. There is no backfill or retry loop and no
publication timeout/force-kill. Stalled operations require inspection/reconciliation.

## Owner-only cutover and installation

**Do not run these activation steps until the PR is reviewed/merged and both CI
jobs pass.** Development does not execute them. Keep the Mac awake, logged in,
online and Docker Desktop running. Use FileVault/protected local storage. Use the
same reviewed checkout for every command below; no sudo, cloud host or Linux cron.

### 1. Retire the native trigger and freeze the source

Inspect the legacy agent and active publishing/writing processes. Disable future
launches first; if a publication is active, wait for completion and inspect its
checkpoint before unloading anything. Never kill a publication as a timeout remedy.

```sh
launchctl print "gui/$(id -u)/com.ask-every-day.publisher"
launchctl disable "gui/$(id -u)/com.ask-every-day.publisher"
# Only when idle; a not-found result means it is already unloaded:
launchctl bootout "gui/$(id -u)/com.ask-every-day.publisher"
```

Archive its plist outside `~/Library/LaunchAgents` after unloading, preserving the
file as evidence so it cannot return on login. Keep the persistent launchctl disable
in place. Revoke any old Mac application authorization with `make schedule-disable`
using the actual source config. Stop content/queue/render/recovery writers too.
Do not delete Keychain items or history; AED-054 owns later cleanup.

### 2. Build and prepare a new 19:00 candidate

The old `~/Library/Application Support/AskEveryDay/aed-059-20261029` candidate has a
20:00 policy. Keep it inactive; do not rewrite or activate it. Build the reviewed
source and select a **new** private directory:

```sh
make setup
make check
AED_SOURCE='/Users/neildavies/Desktop/Projects/AED'
AED_DEPLOY="$HOME/Library/Application Support/AskEveryDay/aed-058-1900-20261029"
AED_TAG="ask-every-day:$(git rev-parse --short HEAD)"
DOCKER_CONTEXT=desktop-linux make -f container/Makefile build IMAGE="$AED_TAG" REVISION="$(git rev-parse HEAD)"
AED_IMAGE_ID="$(docker --context desktop-linux image inspect "$AED_TAG" --format '{{.Id}}')"
docker --context desktop-linux image inspect "$AED_IMAGE_ID" --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
uv run python deployment/desktop.py --source-config "$AED_SOURCE/aed.toml" --data-dir "$AED_SOURCE/data" --directory "$AED_DEPLOY" --image "$AED_IMAGE_ID"
```

The image revision must equal the reviewed commit. The helper uses coordinated
export/restore under the existing lock; it never mounts/copies a live SQLite file
into two active environments. It creates a disabled 19:00 policy, empty credential
storage and a matching state/assets/content snapshot. No timer is installed.

Verify the bundle and compare the current source with the restored snapshot:

```sh
uv run python -m ask_every_day.deployment verify --bundle "$AED_DEPLOY/source-bundle"
uv run python -m ask_every_day.deployment inspect --config "$AED_SOURCE/aed.toml" --data-dir "$AED_SOURCE/data" > "$AED_DEPLOY/source-check.json"
uv run python -m ask_every_day.deployment inspect --config "$AED_DEPLOY/state/aed.toml" --data-dir "$AED_DEPLOY/state/data" > "$AED_DEPLOY/destination-check.json"
cmp "$AED_DEPLOY/source-check.json" "$AED_DEPLOY/destination-check.json"
```

Inspect all three historical Published attempts, including AED-0311 attempt 3 and
its completed checkpoint/media-known flag; exact checkpoint/history fingerprints,
queue positions, content, palette and all historical PNG/JPEG hashes must match.
Resolve any discrepancy before proceeding. No reset, requeue or history regeneration.

### 3. Complete credential and image review

The new candidate does not provision an auth config or credential. Create the
non-secret `config/instagram-auth.toml` privately with the existing Instagram App ID
`2092536608049209` and `redirect_uri="https://localhost:8765/oauth/instagram/callback"`.
Use mode 0600. Do not include an App Secret, token or TLS private key.

Only with explicit owner permission, export the current credential into a new
private handoff directory, then move the single `.credential` file into the empty
candidate credential directory without displaying its contents:

```sh
umask 077
uv run python -m ask_every_day.credential_handoff --auth-config "$AED_SOURCE/instagram-auth.toml" --destination "$AED_DEPLOY/credential-handoff"
mv "$AED_DEPLOY/credential-handoff/"*.credential "$AED_DEPLOY/credentials/"
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" credential-status
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" credential-verify
```

The handoff has its own exact TTY export challenge. The final verification uses
identity GET and eligible durable refresh only; require success for the exact
`ask.every.day` professional account and a valid expiry. A generic blocked result
is **not acceptance**: fix missing/non-secret auth config, private modes or credential
handoff before trying again. No media POST is involved. Preserve existing Keychain
and the old candidate as inactive evidence; do not paste secrets into commands/chat.

```sh
make docker-prepare-next DIRECTORY="$AED_DEPLOY"
make docker-manual-preview DIRECTORY="$AED_DEPLOY"
```

Inspect the exact JPEG, full caption and hash shown. Expected next post from the
last owner report is AED-0066, queue position 4, palette sequence 4, Ice Blue `#E2EFF9`; always
use current verified state if it has changed. Preparation may deliberately replace
only that unattempted draft's old Mac raster. Historical JPEGs/checkpoints must
remain unchanged. All eight Liberation Serif/Lato faces must verify.

Re-run source inspection to a new file and compare with `source-check.json` before
cutover. Any source advance invalidates the candidate: prepare a fresh one. Keep
all source writers stopped. Compare destination changes only against the explicitly
reviewed next draft; never excuse a historical hash difference.

### 4. Generate, diagnose and accept sole Docker authority

Resolve the actual installed Docker CLI and Python, then prepare the host adapter:

```sh
uv run python deployment/desktop_scheduler.py prepare --directory "$AED_DEPLOY" --docker "$(command -v docker)"
uv run python deployment/desktop_scheduler.py diagnose --directory "$AED_DEPLOY" --check-credentials
plutil -lint "$AED_DEPLOY/host/com.ask-every-day.docker-publisher.plist"
plutil -p "$AED_DEPLOY/host/com.ask-every-day.docker-publisher.plist"
```

This copies the standalone script outside Desktop/Documents, validates executable
launchability, pins image/revision, configuration, paths and the database inode,
and leaves host/application scheduling disabled. Diagnostics use the same optional
font overlay plus a network-none overlay. They inspect mounts, fonts, checkpoints,
policy, competing publishers and authorization; `--check-credentials` additionally
reads local expiry/credential metadata, never refreshes or contacts Meta. The
identity verification above remains a separate owner gate.

After verifying source freshness, canonical image/copy, complete history and exact
account identity, explicitly attest sole authority:

```sh
uv run python deployment/desktop_scheduler.py accept-authority --directory "$AED_DEPLOY"
```

Type the displayed `ACCEPT SOLE DOCKER AUTHORITY VERIFIED @ask.every.day ...`
challenge only after completing those checks. This is an **owner attestation**,
not a fabricated automatic identity verification. It records the reviewed binding;
it does not enable posting. From this point the Docker database is authoritative.
Keep the old Mac database inactive/protected even after an uncertain Docker result.

### 5. Install disabled, authorize, then activate

```sh
uv run python deployment/desktop_scheduler.py install --directory "$AED_DEPLOY"
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" --interactive schedule-enable --data-dir /var/lib/aed/data
uv run python deployment/desktop_scheduler.py activate --directory "$AED_DEPLOY"
uv run python deployment/desktop_scheduler.py diagnose --directory "$AED_DEPLOY" --check-credentials
launchctl print "gui/$(id -u)/com.ask-every-day.docker-publisher"
```

Installation only writes a disabled plist. `schedule-enable` requires AED-027's
existing exact authorization challenge and shows 19:00/Europe/London, the queue,
policy fingerprint and expiry. `activate` has a separate exact host challenge,
validates the binding and authorization again, then enables/bootstrap-loads the
agent without RunAtLoad or kickstart. Activation at/after 19:00 makes the next day
the first eligible date. Install/activate sufficiently before 19:00; do not use
kickstart or `run` as a live test. No command above intentionally starts a publication.

Any image, code, Python executable, deployment config, database replacement or font
change requires fresh review/authorization. Prepare a fresh private deployment and
host binding after controlled migration; do not hand-edit receipts to bypass drift.
The optional font overlay is used consistently by diagnostics and publication.
Both the host trigger and operator Compose helper pin `desktop-linux`, reject a
remote endpoint and exclude shell Docker/Compose overrides and `.env` loading.

## Observe acceptance, status and failures

At 19:00, supervise the **automatic** launch. Inspect:

```sh
make docker-scheduler-status DIRECTORY="$AED_DEPLOY" HOST_ARGS=--check-credentials
launchctl print "gui/$(id -u)/com.ask-every-day.docker-publisher"
docker --context desktop-linux ps -a --filter name=aed-desktop-publisher
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" publication-status
```

`host/host.jsonl` records eligibility/admission, dispatch, result/attempt identifiers
and fixed failure reasons. `state/scheduler.jsonl` retains detailed safe checkpoint
context and known media/container IDs. Both rotate at 1 MiB with three backups, mode 0600;
Docker logging is also bounded. Preserve required evidence before rotation. Neither
raw Docker/provider stderr nor tokens, capability URLs or captions enter host logs.

A status of `ready_for_next_1900` requires current local checks, owner-accepted
binding, valid scheduler authorization and an armed/loaded host agent with its
persistent launchd disable override off. Disabled or
unauthorized scheduler, unchecked credentials, inactive trigger, today's existing
publication and blocked preconditions are reported separately. Fixed errors distinguish
Docker daemon/Compose/image, mount permissions, canonical font, credential config/store,
policy drift and unresolved checkpoint failures. A local credential check cannot prove
current provider availability; the engine checks the exact account again before POSTs.

While a publisher is running, diagnose may report the existing container as a
blocker for another invocation; this is expected. Inspect the active operation,
never start a second one. After success require the intended Instagram media,
Published+completed checkpoint+known media ID, one queue removal and one palette
advance. Record evidence before marking AED-057/058/059 Done. A successful exit alone
is not owner acceptance. Uncertain outcomes require AED-024 reconciliation.

A sleeping/offline/logged-out Mac, unavailable Docker Desktop, missed start minute,
invalid credentials or missing approval skips or blocks safely. There is no wake
alarm, background Docker startup, late catch-up or automatic retry that day. Host
sleep during an in-flight request can leave an uncertain result: retain the container,
receipt, SQLite and logs and inspect before any further action. The adapter never
force-kills an active request. Executable launchability is tested directly; owner
launchd/TCC acceptance on this Mac is still required. Inspect `launchctl print` last
exit status and macOS Console for launchd failures before the target date.

## Disable, uninstall and Docker-only rollback

```sh
uv run python deployment/desktop_scheduler.py disable --directory "$AED_DEPLOY"
# Once any active operation has finished and its checkpoint is understood:
uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" schedule-disable --data-dir /var/lib/aed/data
uv run python deployment/desktop_scheduler.py uninstall --directory "$AED_DEPLOY"
```

Disable clears future host admission and launchctl eligibility but leaves active
work running. Uninstall refuses an active job/container and preserves all state,
credentials, assets, checkpoints, receipts and logs. If a runtime-drift error prevents
the helper from loading, use `launchctl disable gui/UID/LABEL` to stop future triggers;
inspect active work before any bootout. Do not delete the live database or stale-lock
files to make a publication retryable.

Rollback means **pause posting**, repair a reviewed Docker runtime or migrate the
latest coordinated Docker state to another Docker host. Preserve all publication
history, unresolved intent, palette, assets and credential evidence, verify the
restore and grant fresh authorization to exactly one Docker publisher. Never
reactivate native Mac posting or restore the old Mac snapshot. Deferred Linux/VPS
schedules are unchanged; no remote infrastructure is provisioned. AED-054 remains
responsible for post-acceptance decommissioning without premature secret/data deletion.
