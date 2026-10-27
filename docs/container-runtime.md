# Portable Linux runtime — AED-053

This PR is an independent packaging/credential milestone. It does not activate a
scheduler or implement a second publishing engine. AED-027 must merge first, then
this branch must rebase/integrate and repeat full/nonpublishing acceptance before
AED-053 can be complete. The Mac stays the supported fallback until owner-approved
container cutover. AED-054 cleanup remains Backlog.

## Build and verify

```sh
make setup
make check
make -f container/Makefile build
make -f container/Makefile test
make -f container/Makefile smoke
make -f container/Makefile audit
```

The runtime uses digest-pinned official Python 3.13 slim-trixie, uv 0.13.6 and
Cloudflare cloudflared 2026.10.1 multi-architecture images. Debian packages come from
the 27 October 2026 snapshot; apt signatures remain checked, with archival
Valid-Until disabled only for those immutable snapshot URLs. `uv sync --locked
--no-dev --no-editable --no-install-project` installs the existing hashed runtime
lock without changing it or downloading another Python. The application wheel is
built in a separate temporary environment using only hash-pinned
`container/build-requirements.txt` (Hatchling and its complete build dependencies),
then installed without dependency resolution. This explicit builder environment
replaces automatic PEP517 build isolation; it is removed from the final layer.
These are build-only pins, not changes to `pyproject.toml` or `uv.lock`. Cairo, libffi, fontconfig, FreeType, Liberation
fonts and timezone data are included; Pillow uses its locked platform wheel.
The runtime has no pytest/dev tools, cron daemon or separate tunnel service.
The separate `test` target adds dev dependencies/regressions; plain `docker build`
ends at the production target. No registry image is pushed by CI.

The positive `.dockerignore` allowlist admits code/templates, locked metadata,
approved CSVs and explicit synthetic tests/SQL fixtures. Local `aed.toml`, auth
settings, databases/journals, palette, locks, generated assets, TLS files, secrets,
`.env`, `.git` and host `.venv` never enter the context or image layers. Never add
private files to an allowlisted source/test file. No build arguments contain secrets.
Review new pinned image/native-package updates and rerun the same tests; frozen
snapshots do not receive security updates automatically.

The process is UID/GID **10001:10001**. Compose additionally uses a read-only root,
private temporary filesystem, dropped capabilities, no-new-privileges and bounded
Docker logs (3 × 5 MB). There are no exposed origin ports, restart/KeepAlive loop,
startup publication or pre-enabled authorization. Missing operator bind directories
fail rather than silently creating root-owned storage.

## Filesystem and command contract

Copy the deployment YAML to a private operator-owned deployment directory outside
Git; its relative mounts are `config/`, `state/`, and `credentials/`. Provision
ownership for UID/GID 10001 on Linux (or a deliberately reviewed equivalent user
mapping). `state/` must be writable; `credentials/` must be owner 10001, mode 0700.
Do not use permissive chmod to work around mapping failures. The non-root user must
also be able to read the public approved CSVs and operator config.

| Container path | Mount and responsibility |
| --- | --- |
| `/etc/aed` | Read-only `aed.toml`, `instagram-auth.toml`, and after A integration `schedule.toml`; no token/secret in TOML |
| `/var/lib/aed` | One complete writable state directory: SQLite, WAL/SHM/journals, checkpoint rows, palette sidecar, assets, publication lock, authorization/daily scheduler files after A integration, backups |
| `/var/lib/aed-credentials` | Separate persistent private writable credential directory; never an image layer or ordinary environment variable |
| `/tmp` | Ephemeral tunnel HOME/config, origin/tunnel lifecycle files and font cache; not persistent publication state |

`container/aed.toml` is an example with database `/var/lib/aed/aed.sqlite3` and assets
`/var/lib/aed/assets`. Retain the source palette/renderer settings during cutover.
Do not let container creation initialize a new production database or new rotation.
Transfer matching current state; the default preview opens SQLite read-only and
fails when it is absent. Mount the directory, not only the `.sqlite3` file, so
journals, atomic sidecar replacements, locks and authorization state are retained.
Container removal/recreation must retain these mounts. Docker volumes are also
possible if their ownership, backup and secure transfer are explicitly managed.

```sh
docker compose -f /private/deployment/compose.yaml run --rm --no-deps publisher preview
docker compose -f /private/deployment/compose.yaml run --rm --no-deps publisher publication-status
```

Default `preview` is offline/read-only: queue selection, canonical JPEG/hash and
palette checks only. It never reads credentials or starts staging/Meta calls.
Output omits editorial text and secrets. `publication-status` uses existing safe
read-only recovery reports. Configuration flags are `--config`, `--auth-config`,
`--schedule-config`, `--data-dir`, `--credential-dir`, `--backup-dir` and
`--log-file` (scheduler commands only).

The agreed AED-027 interface is `ask_every_day.scheduler_cli.main(argv=None, *,
credential_factory)` with `preview/run/status/enable/disable/check-credentials` and
its own authorization, Europe/London window and daily guard. The adapter maps
`schedule-run` and related commands to that exact entrypoint, injecting
`CredentialManager(InstagramAuthClient(auth_config), FileCredentialStore(...))`.
If A is absent it fails closed; no alternate publisher exists. The same application
entrypoint runs on macOS and Linux. Exit 0 is success/safe no-op, 1 is an operational
or policy block, 2 is bad arguments. Safe JSON summaries go to stdout and existing
structured JSON events go to stderr. No remote request is retried by this adapter.

## Private credential backend

The existing `CredentialStore` protocol remains unchanged. `FileCredentialStore`
uses the existing strict credential codec with an explicitly selected POSIX backend;
it never constructs the macOS Keychain adapter. App namespace, exact account,
required scopes, aware issue/expiry times and bearer type retain their existing
validation. Runtime access uses existing `CredentialManager`, including current
expiry, professional identity and refresh checks. Refreshed credentials atomically
replace the persisted item; a read-only Docker secret cannot provide that update.

The operator explicitly provisions the private directory. Every operation pins its
leaf directory descriptor with no-follow checks; leaf directory must be owned by
the effective user with mode 0700. Files must be regular, single-link, owned by the
same user, mode 0600 and bounded to 64 KiB. Symlinks, FIFOs, hardlinks, shared modes,
wrong owners, duplicate/invalid metadata and oversized files fail closed. Writes
use a unique private temporary file, fsync, atomic replacement and directory fsync;
failed replacement retains the prior item. Parent paths, the host, the effective
user and mount configuration remain trusted. Operations do not follow hostile leaf
symlinks or log private filesystem contents/error text.

**Permissions are not encryption.** These are plaintext owner-restricted files at
rest. Host root, Docker administrators, a compromised same-UID process, unsafe
backups or a compromised running container can read them. Require an encrypted,
access-controlled host/volume and protected backups. If that threat model is not
acceptable, use an independently reviewed encrypted/secrets service adapter before
production. Compose secrets are useful for immutable bootstrap inputs but do not
solve writable token-refresh persistence or encrypt arbitrary host bind files.

There is no automatic Keychain export, OAuth transfer or production bootstrap in
this PR. Credential migration needs separate explicit owner approval and a secure
transfer method. Browser OAuth may remain a separately authorized trusted bootstrap
outside the unattended container. Never paste a token into chat, an ordinary env
var, CLI argument, shell history, a Dockerfile, Compose YAML or Git.

For an independently authorized migration, an interactive `-it` container can run
`credential-bootstrap`: it reads a hidden single-line JSON credential record with
the exact existing version-1 codec, requires matching app/account/scopes/unexpired
metadata, and atomically stores it. Input must come from independently authorized
bootstrap evidence; this command does not verify the token remotely or open Keychain.
Do not invent provider identity fields. New authorization still requires provider
identity acceptance before enabling unattended policy. No secret is printed.

```sh
docker compose -f /private/deployment/compose.yaml run --rm --no-deps -it publisher credential-bootstrap
docker compose -f /private/deployment/compose.yaml run --rm --no-deps publisher credential-status
docker compose -f /private/deployment/compose.yaml run --rm --no-deps publisher credential-delete
```

Status checks local metadata/expiry only. Delete removes the app's local credential
and optional App Secret but does not revoke Meta's grant; independently revoke it
through the provider's account/app controls when appropriate. Filesystem deletion
is not a claim of secure erasure on snapshots/SSD/backups. Keep the Mac credential
and disabled fallback until the target deployment is proven; never silently retire
or duplicate a live production credential store.

## Rendering and staging

The existing Classic SVG, Python wrapping, highlights, dimensions, layout code,
JPEG validation, palette assignment and approved canonical assets are unchanged.
The Linux image's open Liberation/system fallbacks differ from proprietary Georgia
and Arial available on some Macs. Cross-OS pixel/JPEG byte equality is therefore
**not claimed**. Exact transferred approved JPEGs retain their hashes and are not
re-rendered. Regression tests verify the same SVG/wrapping/highlight coordinates,
1080×1350 RGB JPEG validity and repeat-render equality within the pinned Linux image.
Review the representative Linux JPEGs before production. Current host visual QA
confirms readable unclipped output and preserved highlights, but the Linux body is
visibly thinner/wider than the Mac Georgia render and the header weight differs.
**Exact Classic typography parity is an unresolved cutover blocker**, not accepted
by structural/layout tests. If exact original fonts
are required, an operator must lawfully provision reviewed matching font files via
an explicit read-only font mount and repeat rendering/approval; do not copy private
licensed fonts into Git/image layers. Font changes require policy/asset revalidation. `container/fonts.compose.yaml` is an
optional read-only overlay; after lawful provisioning of `fonts/` beside the
operator deployment YAML, inspect family resolution and rerun smoke/visual review:

```sh
docker compose -f /private/deployment/compose.yaml -f /private/deployment/fonts.compose.yaml run --rm --no-deps --entrypoint fc-match publisher Georgia
docker compose -f /private/deployment/compose.yaml -f /private/deployment/fonts.compose.yaml run --rm --no-deps --entrypoint fc-match publisher Arial
```

The smoke report records resolved Georgia/Arial/Times New Roman families. The
current fallback image resolves them to DejaVu Serif, Liberation Sans and Liberation
Serif respectively. Matching
family names alone are not sufficient: operator visual acceptance or reviewed
matching font files/versions and native rendering acceptance remain required.

The bundled cloudflared runs as the existing owned subprocess with its origin on
127.0.0.1 inside the same container namespace. HTTP/2, isolated temporary HOME,
no-autoupdate, exact-byte unauthenticated HTTPS verification, bounded startup and
process-group teardown are unchanged. No public origin port or long-running tunnel
sidecar is needed. Normal/exception/interrupt cleanup remains tested without network.
A SIGKILL/host outage cannot run Python cleanup; a short-lived container/init boundary
reaps/stops its remaining processes. Docker's default 10-second stop window should
be increased if a future deployment needs more cleanup time.

Quick Tunnels are a development/testing service with no production uptime guarantee;
fresh hostname DNS lag remains a known constraint (AED-052). Packaging cloudflared
does not fix DNS propagation or provide stable hosting. No S3/AWS change is included.

## Triggering, cutover and rollback

Do not install cron in the image. After AED-027 integration and owner activation,
host cron can trigger the one-shot Compose command, for example **disabled until
explicit approval**:

```cron
# Only on a host cron implementation supporting CRON_TZ; otherwise use UTC frequent
# triggers and rely on AED's Europe/London window, never the host cron zone alone.
CRON_TZ=Europe/London
# * * * * * cd /private/deployment && /usr/bin/docker compose -f compose.yaml run --rm --no-deps publisher schedule-run --log-file /var/lib/aed/scheduler.jsonl
```

This is a disabled once-per-minute trigger example, not an installed cron job.
It covers A's minimum one-minute window; wider trigger intervals must not exceed
the configured window or they can miss an otherwise valid run. DST, repeated/missed triggers,
one post per local day (including manual successes), account/policy approval and
unresolved checkpoints are enforced by A's application workflow. There is no missed
run bulk backfill. Inspect cron's actual timezone support and absolute Docker path
on the chosen host. Cron alone supplies no idempotency. Docker's bounded json-file logs disappear
when `run --rm` removes the container. Pass A's `--log-file` through the adapter to
`/var/lib/aed/scheduler.jsonl` on persistent state: it provides bounded private
0600 structured logs, including safe returned container/media IDs needed when a
later SQLite commit fails. Preserve/export that exact operational evidence through
an authorized private channel before rotation; do not lose a known response mapping
or treat a missing log as proof of remote failure. No tokens, raw responses or
staging URLs belong in these logs. Default offline preview writes no log file.

Before container-first activation: accept/merge A and B with green integrated CI;
verify target credentials, native rendering and staging without publishing; disable
the old authorization/scheduler and stop all publishing processes; make coordinated
SQLite/palette/assets/authorization backups; securely transfer the **current** state;
verify queue/checkpoint/Published history, asset hashes, palette and daily guard;
confirm exactly one active environment; run nonpublishing smoke; obtain explicit
live owner approval; only then enable one scheduler and monitor the first run.
Do not reuse an enabled Mac authorization on Linux: A binds its effective native
render fingerprint, so the destination requires fresh explicit policy approval.
Preserve scheduler history in backups, then renew destination authorization after
native/font acceptance. Never run divergent production database copies as active publishers: a per-database
lock cannot coordinate independent hosts/databases.

`backup` reuses the coordinated SQLite/palette backup under the publication lock.
Canonical assets and A's adjacent authorization/daily files must additionally be
retained/backed up with writers stopped; do not omit them from production restore.
The disposable smoke validates SQL/palette/assets restore as a matching unit.
Never restore old state to forget later remote operations. Rollback stops/disables
the new publisher first and transfers the latest state back before reauthorizing
one Mac fallback; unresolved attempts require AED-024 reconciliation. AED-054 must
wait for accepted cutover and never remove a working fallback prematurely.

## Evidence and sources

Local acceptance uses disposable named volumes, synthetic credentials/provider IDs,
no production mounts and `--network none`. It exercises recreation persistence,
Completed checkpoint/media IDs, queue/palette sequence, canonical bytes, strict
credential restart, coordinated backup/restore and repeated Linux rendering. Review
images are ignored under `output/aed-053-review/`. Container CI builds dependencies
from public registries/snapshots, then runs network-disabled tests/smoke; it never
calls Meta, cloudflared's remote network or Keychain, and does not activate schedules.
The test image's private `/tmp` is explicitly executable for synthetic fake tunnel
fixtures; the deployed Compose tmpfs retains its default noexec protection.

Primary sources: [uv Docker integration](https://docs.astral.sh/uv/guides/integration/docker/),
[Debian snapshot apt usage](https://snapshot.debian.org/),
[Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/),
[Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).

## Verified development results — 27 October 2026

Baseline `cfb33fa590afb70f6eeb3549b8309fe206c1b1eb` passed setup/check with 1,244
existing tests. Final `make setup` / `make check` passed **1,273 tests**, Ruff lint,
Ruff format (118 files) and Pyright (zero errors/warnings). The 29 focused backend/
adapter tests passed. Actual pinned Linux arm64 runtime/test builds passed;
**141 native tests** passed with networking disabled, including rendering fixtures
and staging process cleanup. The disposable smoke passed recreation, checkpoint/
queue/palette/credential continuity, exact canonical bytes, repeat rendering and
coordinated SQL/palette/assets restore. The final excluded-secret probe scanned
**6,136 regular layer files**, history/config and build logs successfully. Compose
and optional font overlay configuration validated; `git diff --check` passed.

Images use Python 3.13.18/CairoSVG 2.9.1/cloudflared 2026.10.1, UID 10001. Root visual
review found readable correct dimensions/highlights with no clipping, while noting
the font differences described above. No real credentials, production mounts,
Meta/Cloudflare remote calls, scheduler activation or production migration occurred.
Remaining acceptance: AED-027 merge and shared-entrypoint integration/rebase, green
integrated CI, typography/font acceptance, authorized credential migration, target
staging/nonpublishing acceptance and explicit single-environment owner cutover.
AED-053 stays In Progress; this independent PR is not an activated publisher.
