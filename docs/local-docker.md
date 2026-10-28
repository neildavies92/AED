# Docker Desktop acceptance — AED-057

Local Docker Desktop on macOS is the target. AED-057 remains In Progress until
owner-approved container publication is observed with a completed checkpoint and
known media ID. AED-058 scheduling follows acceptance. No remote deployment is
needed. The local schedule is **20:00–20:30 Europe/London**, authorization 30 days.
This default applies to newly prepared candidates. Existing private configurations
are not rewritten; an operator schedule change requires fresh policy authorization.

Interactive manual publication now uses the [canonical Docker renderer and exact
per-post approval](canonical-manual-publishing.md). It requires the same explicit
fresh-state cutover; it does not enable or bypass scheduler authorization.

## Private candidate

Use Docker Desktop with its normal file-sharing support. From reviewed code:

```sh
make setup
make check
make -f container/Makefile build IMAGE=ask-every-day:REVIEWED_COMMIT
docker image inspect ask-every-day:REVIEWED_COMMIT --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
make -f deployment/Makefile desktop-prepare ARGS='--source-config /absolute/production/aed.toml --data-dir /absolute/production/data --directory /absolute/private-new-directory --image sha256:VERIFIED_IMAGE_ID'
```

The directory must be new, outside **all** Git repositories, on protected local
storage. Recommended parent: `~/Library/Application Support/AskEveryDay`, mode 0700,
with FileVault enabled/owner accepted. A broader development workspace may itself
be Git-managed. Never use permissive modes to solve a mount failure. The helper
uses AED-056's coordinated SQLite export/restore and copies matching palette,
canonical assets and approved CSVs. It never copies a live SQLite file directly,
loads Keychain, copies active authorization, initializes a new queue or publishes.

Stop source writers during export. Mac remains authoritative during validation;
if any Mac publication/content/queue/asset change occurs, prepare a **new candidate**
from a fresh coordinated export. The old candidate must stay disabled. Never mount
the live Mac database into Docker. The private candidate has empty credentials and
no active scheduler authorization. No timer/cron/LaunchAgent is installed.

The generated Compose file pins the immutable local image ID, keeps root readonly,
UID/GID 10001, dropped capabilities, private tmpfs and no public origin ports. Bind
mounts are config/ readonly, state/ and credentials/ writable. Assets live in state/.
Use `/var/lib/aed/data` consistently for scheduler commands.

```sh
docker compose -f /absolute/private-directory/compose.yaml run --rm --no-deps --entrypoint python publisher -m ask_every_day.desktop_validation storage
docker compose -f /absolute/private-directory/compose.yaml run --rm --no-deps publisher preview
docker compose -f /absolute/private-directory/compose.yaml run --rm --no-deps publisher publication-status
docker compose -f /absolute/private-directory/compose.yaml run --rm --no-deps publisher schedule-run --data-dir /var/lib/aed/data --log-file /var/lib/aed/scheduler.jsonl
```

Before authorization the last command must report disabled. Storage validation
requires the container sees owner10001/mode 0700 on both writable directories; it
uses only a dedicated synthetic credential namespace and temporary state probe.
It never reads the real credential item. Docker Desktop maps bind ownership; do
not assume macOS numeric UID 501 must match Linux 10001. Validate on the actual host.
A wrong mapping/mode blocks; review Desktop file-sharing/backend settings rather
than chowning the Mac account or weakening FileCredentialStore validation.

Compare the bundle summary with the container: all publication fingerprints,
checkpoint phases/media-known flags, queue positions, palette sequence/background
and every canonical PNG/JPEG hash must match. A missing next canonical image is
valid before preparation; render it only in the private candidate using existing
`prepare_next_asset` under the publication lock. Never re-render historical images.

## Credentials and nonpublishing networking

Real credential access requires separate explicit owner authorization. On the Mac,
run the existing `python -m ask_every_day.credential_handoff` with production auth
config and a **new** private destination; type its exact export challenge yourself.
It exports only the credential (no App Secret) directly to owner-private files.
Move its file into the candidate's empty credentials/ directory without printing
it, preserving 0700/0600. Re-run the Docker storage check. No token in chat, argv,
environment, shell history or Git. Local files are plaintext: protected storage and
backups are required. Create config/instagram-auth.toml from the existing **non-secret**
App ID and accepted localhost redirect, with no OAuth TLS keys or App Secret.

After credential approval, existing container `credential-status`,
`schedule-check-credentials` and explicit `credential-verify` validate local expiry,
exact professional identity and eligible durable refresh. Recreate the container
and repeat status. Do not force refresh by editing issue/expiry dates. Synthetic
Linux tests/smoke prove refresh persistence without using a real credential.
These commands make no Instagram media POST; verify can make identity/refresh calls.

The dedicated real-network smoke has **no production mounts or credentials**:

```sh
docker run --rm --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 --entrypoint python sha256:VERIFIED_IMAGE_ID -m ask_every_day.desktop_validation staging
```

It generates a flat 1080×1350 JPEG, invokes the existing cloudflared/loopback origin,
verifies exact bytes over public HTTPS and exits/tears down. URLs are not printed.
No Instagram client is constructed. DNS/530 failures remain visible and bounded;
do not disable validation or expose origin ports. Repeat only this synthetic
smoke after a network correction; never blindly retry a media publication.

## Visual and one-post acceptance

Use the Mac/Linux comparison generated with `deployment/visual_review.py` after
container smoke. `--post-id AED-0311` can compare the actual next candidate image
with the Mac renderer using the same background. Both dimensions 1080×1350; fallback
fonts differ. Owner must accept Linux appearance or lawfully mount matching fonts,
then repeat review. All future commands must use the accepted font overlay.

Only after review/merge/green CI and explicit owner consent:

1. Disable/revoke the old Mac schedule and unload its LaunchAgent. Confirm no Mac
   writer/publishing/tunnel process remains. Preserve the manual fallback.
2. Re-export **current** Mac state to a new private candidate; compare history,
   unresolved state, next entry/palette/assets. Repeat mount/credential/visual checks.
3. Record source and destination summaries; owner confirms this candidate becomes
   authoritative at cutover. Keep the Mac copy inactive from this point onward.
4. Inspect `schedule-preview --data-dir /var/lib/aed/data`; confirm 20:00–20:30 London,
   approved queue and exact professional account. With `-it`, owner runs existing
   `schedule-enable --data-dir /var/lib/aed/data` and types its exact TTY challenge.
   This grants future publication permission; it does not itself post.
5. During that configured window, owner explicitly runs **one** `schedule-run`
   with persistent `/var/lib/aed/scheduler.jsonl`. No timer/loop. Outside the window
   it skips safely; never change the clock/window to force acceptance.
6. Inspect `publication-status`, logs and the actual Instagram post. Confirm completed
   checkpoint, media known, correct canonical JPEG, exactly one queue removal,
   palette progression and daily guard. Preserve response/ID evidence before rotation.
7. Revoke using `schedule-disable`; verify disabled. Keep current container state
   authoritative even if cleanup/final recording fails after a provider response.
   Uncertain outcomes require AED-024 reconciliation, never rerun/reset/requeue.

AED-057 is Done only after owner verifies that real container-originated post.
Record exact attempt, media evidence and fingerprints privately. Do not activate
AED-058 scheduling during this ticket.

## Current-state manual fallback

Stop/revoke Docker authorization; verify no container operation is active. Export
the **latest authoritative container state**, not source-bundle or the old Mac DB,
using the existing deployment module and common lock. Keep logs/credentials/recovery
evidence protected. Verify/restore to a new inactive Mac directory and remap config
paths only; preserve palette/renderer/content/history/assets. Confirm the latest
container post and completed checkpoint appear, queue/next colour match and no
unresolved attempt exists. Validate the Mac credential; use owner-approved OAuth
again if it is stale, never edit token metadata. The Mac may resume state management, but native publication is blocked by AED-059.
Use a fresh verified Docker cutover for any subsequent manual publication with
canonical typography and its exact per-post approval. Do not restore stale state to make a post retryable.

## Development evidence

See the PR for exact tests and Docker Desktop runtime results. Credential access,
owner visual approval and a genuine publication are separate acceptance gates;
synthetic smoke cannot satisfy them. Current candidate copies are validation-only
and must be refreshed immediately before an approved cutover.

### Local validation on 28 October 2026

Merged base f1f0c07 has green application and container CI. Docker Desktop Engine
29.9.2 passed actual private bind mount UID/GID 10001, mode 0700, writable atomic
state/private backend tests without host chown. FileVault reports On. A private
candidate was created outside Git under Application Support; no credentials or
active authorization were copied. Mac remains authoritative.

Production inspection and the copied database agree: AED-0395 Published (valid
legacy checkpoint), AED-0289 attempt 2 Published/completed/media known, no unresolved
attempt; next AED-0311 position 3, palette sequence 3, Mist Blue #EDF4FB. The source
summary still matched its original coordinated snapshot after Docker validation.
Existing canonical images were preserved; only AED-0311 was prepared in the copy.
Candidate JPEG SHA256:
`99e496e12726c2d20d45fd47598d86a8cf10aac4dbfbe0581bc80c0ffe7482b0`.

Real Docker networking/synthetic staging succeeded with existing DNS readiness
retries, exact public HTTPS byte verification and tunnel teardown. No production
mounts or credentials were present in that test. Container schedule-run reported
disabled, and its structured log survived removal. Mac/Linux 0311 comparison was
inspected at 1080×1350; readable/unclipped with differing typography, owner approval
pending. Actual credentials, identity/expiry/refresh acceptance and the first real
container publication remain gated. No real Instagram call was made.

Canonical setup/check, native Linux tests, smoke and security audit results are
recorded in the PR. The macOS-only skip is the actual UID10001 test, which runs and
passes inside Linux. Do not treat these development results as live acceptance.
