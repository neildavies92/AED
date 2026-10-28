# Canonical Docker manual publishing — AED-059

Docker Desktop is the canonical publication renderer: Liberation Serif prompts,
Lato supporting text, and the existing Classic layout at 1080×1350. The same
rendering and publication engine serves manual and scheduled operations. Native
Mac rendering remains useful for development; native `publish-next --confirm`
fails with `canonical_docker_renderer_required` before loading Keychain.

## Authority and preparation

The Mac production database remains authoritative **until the owner explicitly
verifies cutover**. This implementation does not perform cutover, access real
credentials, or activate publication. Use [local Docker acceptance](local-docker.md)
to build a reviewed image and create private storage outside Git. Never mount the
live Mac SQLite file into Docker. A copied candidate is validation-only while the
Mac can still change history, content, queue, palette or assets.

Before a real container publication: stop all Mac publishers/writers and revoke
any old scheduling authorization; export the latest state using the existing
coordinated export/restore commands; compare history/checkpoints, unresolved
attempts, queue positions, palette and canonical hashes; complete storage and
credential checks separately with owner authorization. The owner must explicitly
accept that verified candidate as the sole authoritative state. Leave the Mac
copy inactive. Existing credential verification is not permission to publish.

From the reviewed source checkout, select that private deployment explicitly:

```sh
make docker-prepare-next DIRECTORY='/absolute/private/deployment'
make docker-manual-preview DIRECTORY='/absolute/private/deployment'
```

These helpers reuse `deployment/desktop-compose.py`, including its consistent
optional read-only font overlay, with `/var/lib/aed/data` as the copy catalog.
For validation, prepare only an inactive copied candidate. For publication, prepare
only the authoritative state after cutover. No command generates a new queue.

`prepare-next` is an explicit locked write to the next **unattempted** draft. It
verifies all eight regular/bold/italic/bold-italic font faces against the reviewed
`editorial-fonts.json` family/style/version/index/SHA256 metadata, then freshly
rasterizes in memory. It reuses an existing PNG/JPEG only if fresh bytes, persisted
hashes and current files agree. A stale SVG-compatible Mac raster is replaced only
at this explicit step, preserving its success-sequenced palette assignment.
Published or previously attempted assets are never regenerated; any unresolved
attempt blocks selection. Historical files and ledger entries remain intact.

The manifest pins Liberation Serif 2.1.5 (fontconfig integer version 137625) and
Lato 2.015 (132055), including exact file hashes. The immutable Docker package
snapshot supplies these open fonts; no download or font mount is necessary.
A substituting overlay or changed file/version fails closed. Updating the manifest
or rendering code changes the scheduler authorization fingerprint and requires
fresh owner review/authorization. Do not edit the manifest to bypass a failure.

## Read-only exact preview

`manual-preview` opens SQLite read-only, does not load a credential or auth config,
and makes no network request or persistent log. It reports Post ID, original queue
position, complete caption/hashtags, palette colour/sequence, verified font
metadata, exact JPEG path and SHA256. `asset_valid=true` means an in-memory render
matches the ledger and both current PNG/JPEG files. Inspect that exact JPEG:
container `/var/lib/aed/assets/POST_ID.jpg` maps to private deployment
`state/assets/POST_ID.jpg`. The image itself is not rewritten by preview.

If the asset is stale or absent, preview reports `asset_valid=false`, a null hash
and `run_container_prepare_next`. Run the explicit prepare command and inspect
preview again. Preparation changes the asset hash and invalidates any prior
challenge. Preview never silently repairs an image. Generic container `preview`
is only a state summary; use **manual-preview** for approval of canonical bytes.

## Approve exactly one post

Only after the owner-approved cutover and exact JPEG/copy review:

```sh
make docker-manual-publish DIRECTORY='/absolute/private/deployment'
```

This runs Docker with a real interactive TTY and injects the existing persistent
Linux `FileCredentialStore`; no macOS Keychain backend is constructed. It displays
the exact JPEG path/hash, Post ID, colour and complete caption again. Type the
full displayed `PUBLISH ...` challenge. A flag, `yes`, stale challenge, piped stdin
or noninteractive invocation cannot authorize it. Every post needs fresh approval.

Manual approval can be given outside 20:00–20:30 Europe/London. It neither reads nor
grants scheduler authorization. The scheduler policy, 30-minute window and 30-day
authorization remain unchanged. No timer or schedule is created or enabled.

After exact approval, the existing engine validates/refreshes credentials, verifies
account/quota, stages the approved bytes, and uses the existing durable
create/publish checkpoints. Font metadata and fresh raster bytes are checked again
following confirmation and at existing dispatch guards, including before media
POSTs. Queue/palette progression and media-ID persistence use the same transaction
and duplicate-prevention checks. A renderer change during approval fails safely.
Bounded structured logs persist at `/var/lib/aed/manual.jsonl`; keep them private.

No media POST is automatically retried. On an interrupted/uncertain attempt, stop
and use [AED-024 recovery](publication-recovery.md); never reprepare, reset,
requeue or delete the record to try again. Preserve known attempt/container/media
IDs, current SQLite, palette, images and logs. Verify the completed checkpoint,
media ID, exactly one queue removal and palette progression after success.

## Fallback and verification

After cutover the **current container database** is authoritative, including after
an uncertain result. To return state management to the Mac, stop/revoke Docker
operations and export/restore the latest coordinated container state into a new
inactive Mac location. Validate history, checkpoints, assets, queue and palette;
never restore the pre-cutover copy. Native Mac publication remains blocked because
it cannot guarantee these font files. Future publication must again use a freshly
verified sole Docker authority with its canonical renderer and exact approval.

Offline regressions cover substituted files/versions/styles, stale raster repair,
historical/unresolved preservation, read-only preview, credential injection, TTY
refusal, prompt-time drift and a synthetic completed publication outside the
schedule window. Docker smoke uses a disposable volume and `--network none`, checks
actual approved font files and exact preview bytes across container recreation,
and verifies durable synthetic checkpoints/credentials. It does not satisfy live
owner acceptance. See the PR for the complete test and security-audit results.
