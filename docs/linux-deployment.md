# Linux production deployment — AED-056

**Prepared, not live.** AED-027 and AED-053 are merged. No paid infrastructure,
production credentials, state migration, cron, or live publication is authorized by
this development ticket. AED-056 remains In Progress until the owner accepts the
cutover and the first genuine unattended 21:00 London post has a completed durable
checkpoint and known media ID. AED-053 also awaits runtime/visual acceptance.
AED-055 is deferred; AED-054 cleanup starts only after verified cutover.

## Host choice and costs

Recommended starting point: Ubuntu **24.04 LTS**, amd64, AWS Lightsail in London,
2 vCPU / **2 GB RAM / 60 GB SSD with IPv4**, currently **US$12/month**. The $7/1 GB
plan may run a prebuilt image, but offers less headroom for Cairo, image decoding,
package updates and Docker. Build/test on CI or a development machine, not the small
production VPS. No Kubernetes, managed database, container orchestrator or registry
is required; `docker save`/SSH/`docker load` is sufficient for this single host.

Budget separately for protected persistent storage and backups. AWS explicitly
provides encryption for **attached disks and their snapshots**; do not assume this
proves root-disk encryption. Before credentials, verify the chosen storage's actual
encryption controls. An encrypted attached disk mounted at `/srv/aed` is a simple
option; at $0.10/GB-month a 20 GB disk adds $2/month. Snapshot storage is $0.05/GB-month
(e.g. 10 GB billed adds $0.50/month). Plan roughly $15–18/month before tax, FX and
traffic overages; exact region/size/backup choices require owner approval. Restrict
AWS IAM, use MFA, retain SSH host-key verification and protect recovery keys. Host
root and Docker administrators can read a running container's plaintext credentials.

Prices checked 28 October 2026: [Lightsail pricing](https://aws.amazon.com/lightsail/pricing/),
[encrypted attached storage](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-faq-block-storage.html),
[snapshot costs](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-faq-snapshots.html).

## Provision only after approval

1. Owner approves provider, region, cost, SSH administrator/CIDR and encrypted
   storage/backup plan. Create the host/disk manually; never put secrets in user-data.
2. Verify the SSH host fingerprint independently; use a dedicated SSH key and a
   non-root sudo administrator. Confirm a **second key-authenticated session** and
   working provider console recovery before hardening. Keep the first session open.
3. Mount approved encrypted storage at `/srv/aed` with persistent UUID mount and
   restrictive permissions. Ensure boot cannot silently fall back to the root disk
   when it is absent. Confirm `findmnt /srv/aed` and the provider encryption controls.
4. Copy the reviewed `deployment/setup-host.sh` and invoke as root with the explicit
   administrator CIDR. This modifies only the approved new host:

   ```sh
   sudo sh setup-host.sh 203.0.113.10/32
   ```

   Replace the documentation address. It installs signed Docker apt packages,
   Compose, cron, NTP and updates; creates UID/GID 10001 and private directories;
   disables password/root SSH and permits only the chosen SSH source through UFW.
   No AED job/authorization is installed. Read the script first; it targets Ubuntu
   24.04, not arbitrary existing hosts. It can be rerun without resetting AED state
   or credentials. Package upgrades remain deliberate maintenance operations.
5. Restrict Lightsail's IPv4 **and IPv6** ingress firewall to SSH from the approved
   administrator source; remove default HTTP/HTTPS/world SSH rules. No media-origin,
   Docker daemon, database or container port is exposed. Outbound DNS, HTTPS and
   cloudflared HTTP/2 TCP 7844 must work. Docker published ports bypass some UFW rules;
   AED Compose publishes none. Review any later port change separately.
6. Verify `sudo sshd -T` shows password/keyboard auth off, root login off; test the
   second session. Check security updates/reboot requirement, Docker/Compose versions,
   `timedatectl` UTC/NTP sync, `systemctl status cron`, disk space and mounts. No reboot
   or SSH lockout should be hidden by the automation. Enable/review normal Ubuntu
   unattended security updates; schedule Docker/kernel maintenance outside the window.

See [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/)
for supported packages, signatures and firewall limitations. Docker group membership
is root-equivalent; use controlled sudo, not broad group membership.

## Versioned image and deployment layout

From the clean, reviewed commit after merge and green application/container CI:

```sh
git status --porcelain
git rev-parse HEAD
make -f container/Makefile build IMAGE=ask-every-day:REVIEWED_COMMIT
make -f container/Makefile test
make -f container/Makefile smoke IMAGE=ask-every-day:REVIEWED_COMMIT
make -f container/Makefile audit
docker image inspect ask-every-day:REVIEWED_COMMIT --format '{{.Id}} {{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker save -o /private/approved-transfer/aed-image.tar ask-every-day:REVIEWED_COMMIT
```

Replace `REVIEWED_COMMIT` with the full commit, not an unreviewed tag. Build on the
**target architecture**: for an amd64 Lightsail host use the amd64 CI/build machine
or `docker buildx build --platform linux/amd64 --load --target runtime --build-arg
AED_REVISION=COMMIT -t ask-every-day:COMMIT .`, then run the same tests there.
Record the source commit, image ID, architecture and archive SHA256 through a trusted
channel; labels document provenance but are not a cryptographic attestation. Build
only the verified clean checkout. Verify the archive checksum after SSH transfer,
load it on the host and compare **all three** inspect values. No automatic pull/build.

Copy `container/compose.yaml` to `/srv/aed/compose.yaml`; replace its image with the
verified immutable `sha256:...` local image ID. Save exactly that ID plus newline to
`/srv/aed/image-id` (root 0600). The host trigger refuses a different Compose image.
Keep Compose root-owned 0600. Leave its default command `preview` and `pull_policy:
never`. Never run the test target as production.

| Host path | Ownership/mode | Purpose |
| --- | --- | --- |
| `/srv/aed` | root 0700 | Trusted deployment parent on approved encrypted disk |
| `compose.yaml`, `image-id` | root 0600 | Reviewed immutable image and mount configuration |
| `config/` | root:10001 0750, files 0640 | Non-secret app/auth/schedule config, RO mount |
| `state/` | 10001:10001 0700, files 0600 | SQLite, palette, assets, content, scheduler log/approval |
| `credentials/` | 10001:10001 0700, files 0600 | Writable atomic FileCredentialStore |
| `backups/`, `logs/` | root 0700 | Protected transfer bundles and bounded host results |
| `/usr/local/lib/aed/trigger.py` | root 0644 | Reviewed host trigger; invoke with `/usr/bin/python3` |

Use the transferred `aed.toml` palette/renderer/account settings, changing only
`database_path="/var/lib/aed/aed.sqlite3"` and `assets_path="/var/lib/aed/assets"`.
Copy `deployment/schedule.toml`: Europe/London, 21:00, 30-minute window, 30-day grant.
`instagram-auth.toml` contains only the existing non-secret Instagram App ID and
`redirect_uri="https://localhost:8765/oauth/instagram/callback"`; no TLS keys are
needed for headless refresh. Do not copy the source OAuth private key or App Secret.

The bundle includes approved CSVs. The host trigger always passes
`--data-dir /var/lib/aed/data`; use that same path for preview and every operator
scheduler command. This preserves the transferred content rather than silently
selecting a newer image's dataset. Different content requires policy review.

## Fresh coordinated state transfer

The Mac DB remains authoritative until cutover. Do not reset history or requeue
AED-0395/AED-0289. Stop all writers (including manual rendering, content import and
queue regeneration); the publication lock alone cannot coordinate unrelated tools.
After owner approval, revoke Mac unattended authorization and unload its LaunchAgent;
confirm no publishing process/tunnel is active. Preserve the manual fallback and its
Keychain. If preparation misses today's window, stop here and use the Mac fallback;
a later Mac post invalidates any prior transfer snapshot.

On the Mac, from the reviewed code with the **actual production config**:

```sh
make -f deployment/Makefile inspect ARGS='--config /absolute/production/aed.toml --data-dir /absolute/production/data'
make -f deployment/Makefile export ARGS='--config /absolute/production/aed.toml --data-dir /absolute/production/data --target /private/approved-transfer/current-bundle'
make -f deployment/Makefile verify ARGS='--bundle /private/approved-transfer/current-bundle'
```

The target must not exist. Export holds the shared publication lock, uses SQLite's
backup API, checks schema 3/integrity/FKs, unresolved checkpoints, all history/queue
and canonical palette asset hashes, copies all PNG/JPEGs and the five approved CSVs,
and compares before/copied/after summaries. It preserves timestamps, media IDs,
checkpoints, queue positions, palette bag/sequence and canonical bytes. Hashes cover
every file. Enabled schedule authorization is retained **only under `evidence/`**,
never installed alongside the destination DB. No credentials are included. Stop
writers as directed; hashes detect transfer corruption, not a malicious operator
who replaces the manifest. Transfer over authenticated SSH to trusted private storage.

Recovery logs in the DB directory named `scheduler.jsonl*` are included; additionally
retain any Mac log location (typically `output/scheduler.jsonl*`) and independently
captured AED-024 response evidence in the protected handover archive. Do not print
raw provider responses or tokens. Copy these only with writers stopped and preserve
their checksums separately. The bundle is not an automatic arbitrary-file collector.

Verify the manifest SHA256 via an independent trusted comparison after transfer.
On Linux use the reviewed image with **network disabled**, a private staging bind
owned by UID10001, and the explicit deployment module:

```sh
sudo docker run --rm --network none --read-only --user 10001:10001 \
  --mount type=bind,src=/srv/aed/transfer,dst=/transfer \
  --entrypoint python sha256:VERIFIED_IMAGE_ID -m ask_every_day.deployment \
  verify --bundle /transfer/current-bundle
sudo docker run --rm --network none --read-only --user 10001:10001 \
  --mount type=bind,src=/srv/aed/transfer,dst=/transfer \
  --entrypoint python sha256:VERIFIED_IMAGE_ID -m ask_every_day.deployment \
  restore --bundle /transfer/current-bundle --target /transfer/restored
```

Restore refuses an existing target; do not point it at live state. It validates a
complete copy before publishing the new directory. With all containers/triggers
stopped, preserve the old state directory under a timestamped private name and move
the verified restored directory to `/srv/aed/state` on the same filesystem. Fix only
ownership to 10001:10001; never relax private modes. Verify actual source and Linux
summaries match, specifically **AED-0395 and AED-0289 Published**, attempt 2 completed
with media known for AED-0289 (preserve any legitimate legacy checkpoint for 0395),
no unresolved attempt, next Post ID/position/colour/sequence and canonical SHA256.
If source changed since export, discard the candidate and create a **fresh** bundle.
No date or hard-coded Post ID decides next publication.

## Explicit credential handoff and acceptance

Nothing reads Keychain automatically. Two operator-authorized paths are available:

- Existing `credential-bootstrap`: hidden single-line version-1 JSON from an
  independently approved source. Strict codec/app/account/scopes/expiry validation;
  local bootstrap does not prove current provider identity.
- If the only current credential is in Mac Keychain, the new **explicit** handoff
  command asks for exact TTY confirmation **before constructing/accessing Keychain**:

  ```sh
  uv run python -m ask_every_day.credential_handoff \
    --auth-config /absolute/production/instagram-auth.toml \
    --destination /private/encrypted-transfer/instagram-credential
  ```

  The parent must be trusted encrypted storage, destination new. It exports only
  the current validated, unexpired credential to a 0700 directory/0600 file via the
  existing codec; no token/App Secret reaches stdout, argv or environment. It never
  refreshes, publishes, deletes the Mac credential or exports the App Secret. This
  is a **separate owner-approved operation**, not part of setup, tests or cron.

Quiesce credential refresh/rotation during the authorized handoff; reverify the
Linux identity and expiry immediately before cutover. Copy the directory by authenticated `scp -p -r`/SFTP over SSH with no terminal
recording/session logging of secret content. Provision an administrator-owned 0700
incoming directory first; after validation move the credential file into the empty
Linux credential directory and `chown 10001:10001`, mode 0600. Do not `cat`/echo it or
place it in clipboard/chat/history. The destination must remain encrypted at rest.
Keep credentials out of general state bundles. Maintain a separately encrypted,
access-controlled credential backup; refresh means this copy can become stale.

Owner-authorized nonpublishing checks:

```sh
sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -T publisher credential-status
sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -T publisher schedule-check-credentials
sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -T publisher credential-verify
sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -T publisher credential-status
```

`credential-verify` explicitly performs identity GETs and, only when eligible under
existing policy, refreshes/persists the token. It creates no media and sends no
media POST. It must identify the exact existing professional `ask.every.day` account
and bound user ID; missing/expired/malformed/wrong identity fails. Repeat status in a
new container; use synthetic tests to prove refresh durability without forcing a
real refresh or changing timestamps. Only an authorized provider re-login/rotation
replaces an expired credential. Never extend local expiry by editing JSON.
Deleting local files does not revoke Meta's grant or securely erase snapshots.

## Offline acceptance, visual review and staging

Run `make -f deployment/Makefile visual-review` on Mac after container smoke to
regenerate the comparison. Run `make setup`, `make check`, and container `build`, `test`, `smoke`, `audit` on
reviewed code. Native tests include common scheduler authorization, repeated daily
attempts, restarts, DST, unresolved outcomes, credential refresh and new migration
checks. The disposable smoke runs network-none, recreates containers and verifies
SQL/checkpoints/queue/palette/assets/credentials and coordinated restore. It never
mounts production paths. Confirm one-shot exit status, bounded retained logs and
non-root/read-only runtime on the real host with **synthetic data first**.

Review the generated `output/aed-056-review/index.html`: same Post/background,
Mac and Linux JPEGs, 1080×1350. Linux fallback Georgia→DejaVu Serif and
Arial→Liberation Sans differ visibly. **Owner visual approval remains required.**
If parity is necessary, lawfully obtain matching font files/versions, mount them
read-only using `container/fonts.compose.yaml`, check `fc-match` and regenerate the
comparison. Do not add proprietary fonts to Git or layers. The host trigger automatically includes `/srv/aed/fonts.compose.yaml` if present.
Include the same overlay (`-f /srv/aed/fonts.compose.yaml`) in every operator
Compose invocation; changing fonts invalidates scheduler
approval. Transfer canonical approved JPEGs exactly; do not re-render history.

Verify `cloudflared --version` in the reviewed image. Network-isolated staging tests
exercise loopback origin, child teardown and byte validation. On the approved target,
run a synthetic image through the existing staging command as a separate supervised
acceptance step, terminate it, and verify no lingering container/tunnel or open public
origin. This exposes only the synthetic JPEG and makes no Instagram request. Real
Quick Tunnel connectivity, external DNS and host egress cannot be proven offline.

Quick Tunnels have no uptime SLA, random hostnames, occasional DNS/HTTP530 startup
lag and a 200 in-flight request limit. Current bounded HTTP/2 readiness/exact-byte
checks remain; do not shorten safeguards, expose the origin or blind-retry a media
POST. A readiness failure can miss the window; record the event and diagnose DNS,
TCP7844, TLS/443, clocks and teardown. Keep this existing path initially; recurring
production readiness failures belong to AED-052, not a new platform in this ticket.
[Cloudflare limitations](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

## Cron: installation is separate from activation

Install the reviewed trigger root-owned 0644 at `/usr/local/lib/aed/trigger.py`.
Keep `deployment/aed.cron.disabled` **outside `/etc/cron.d`** during preparation.
The template has both jobs commented. It uses UTC host cron, covers 20:00 and 21:00 UTC
(London 21:00 in BST/GMT), and the standard-library wrapper filters to 21:00–21:30 London.
The existing scheduler independently enforces its actual 21:00–21:30 policy.
There is no CRON_TZ assumption, internal container cron, catch-up or new publisher.

Verify **on the actual target** `dpkg-query -W cron`, `timedatectl`,
`readlink -f /etc/localtime`, `systemctl status cron`, and `man 5 crontab`. Ubuntu 24.04's package is [cron 3.0pl1](https://packages.ubuntu.com/noble/cron),
not necessarily Cronie; online manpages can select a different package. After owner
approval to change host cron, first install a temporary harmless one-minute `logger`
probe with UTC timestamp, observe `journalctl -u cron`/`journalctl -t aed-cron-probe`,
then remove it. Do not use `schedule-run` as a test of enabled policy.

Root `flock` spans each container lifetime; the existing per-DB lock remains intact.
A fixed `aed-daily` container name also blocks an orphan after wrapper death. There
is no automatic removal/restart of a stuck container; inspect SQL/checkpoints first.
Do not force-stop a possibly in-flight POST and assume it failed. An uncertain outcome
requires AED-024 reconciliation. Separate database copies are never coordinated by
these locks: **exactly one environment** must be allowed to publish.

After the cutover checklist and explicit owner approval:

```sh
sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -it publisher schedule-enable --data-dir /var/lib/aed/data
```

Review/type the exact TTY challenge; no piped approval. Verify `schedule-status`.
Only then copy the reviewed cron template to `/etc/cron.d/aed` root 0644 and uncomment
its two job lines. No code in this ticket does this automatically. Stop future jobs
by removing `/etc/cron.d/aed`; revoke using `schedule-disable`, which does not interrupt
an already running operation. Inspect/wait for active operations before transferring
or rolling back. Do not delete a lock inode or mutate history to clear a block.

## Daily operation, monitoring, backup and recovery

At21:35 London the second host trigger invokes offline `monitor`. It exits1 for a
missing successful completed checkpoint/media ID after the window, duplicate daily
successes or unresolved attempts. It exits0 for one verified completion. The report
reads SQLite, never Meta. The host log records safe summaries and exit status with
1 MiB × 4 bounded rotation, private 0600; application `state/scheduler.jsonl` preserves
safe returned IDs with the existing 1 MiB × 4 rotation. Docker logs from `--rm` disappear,
so they are **not** the recovery record. Failures also write a fixed local journal
message. Review journal retention on the host (e.g. `SystemMaxUse=100M` / `MaxRetentionSec=14day`).

Until the owner chooses an external alert destination, the minimum monitoring
procedure is an **owner daily21:40 London check**: SSH reachability, fresh dated
host result, `publisher monitor`, `publication-status`, and visual Instagram result.
A host-down or cron-not-running condition cannot alert from inside that host;
missing fresh heartbeat is itself actionable. No email/Slack integration or delivery
claim is made. Configure an independently monitored heartbeat later if unattended
operator notification is required; do not treat local journaling as a delivered alert.

```sh
sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -T publisher monitor
sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -T publisher publication-status
sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -T publisher schedule-status --data-dir /var/lib/aed/data
sudo journalctl -t aed --since today
```

On failure, stop future triggers, preserve logs/IDs before rotation, inspect the exact
attempt. Do not reset/delete/requeue. Linux `publisher reconcile --attempt-id N` uses
existing AED-024 reports; `--remote` explicitly authorizes read-only provider evidence.
`--resolve published --evidence /var/lib/aed/evidence.json --confirm` uses the same exact
interactive challenge and independently captured response mapping; `-it` is required.
Use the [recovery guide](publication-recovery.md) for terminal-failure evidence. The
container adapter injects FileCredentialStore into the **same** reconciliation engine.
No recovery command sends an Instagram media POST. Preserve unresolved state if the
mapping is uncertain; image/caption similarity is insufficient evidence.

After each successful day and before updates, stop triggers/writers and export a new
coordinated bundle using the deployment module under the same lock. Protect/encrypt
and copy it off-host over SSH; retain 7 daily / 4 weekly copies or an owner-reviewed policy.
Copy current private credential/config/response evidence separately to approved
encrypted backup storage. Test restore to a fresh inactive directory, never overwrite
live state. Cloud snapshots alone are crash-consistent, not a substitute for the
coordinated application bundle. Never use a pre-publication backup to forget a later
remote post.

For an update: stop cron, ensure no active operation, revoke authorization, make a
current bundle/credential backup, verify new reviewed image/CI, keep the old image,
change the pinned imageID/Compose, run offline preview/status/monitor/render checks,
then explicitly reauthorize and restore only one cron. No unattended image auto-update.
For rollback: stop the new trigger and finish/reconcile any operation, then migrate
the **latest current** state to the compatible prior runtime or Mac. Do not resurrect
a stale database. Revalidate paths/fonts/config/credentials and grant a new policy
only on the selected host. Preserve the other host disabled until acceptance.

## Cutover gates — in this order

1. Latest reviewed code merged; both main commit CI green; correct immutable image.
2. Owner accepts VPS/network/storage, credentials, Linux typography and staging.
3. Owner authorizes disabling/unloading Mac LaunchAgent and revoking Mac schedule.
4. Confirm no Mac writer/publisher/tunnel is active; retain manual fallback.
5. Create a **fresh** coordinated production backup after the latest real post.
6. Transfer/verify complete state, 0395/0289 history/checkpoints, current next entry, palette and assets.
7. Run Linux nonpublishing preview/status/credential/render/restore checks.
8. Confirm no other authorized active publisher/cron/LaunchAgent exists.
9. Owner types the fresh Linux `schedule-enable` challenge for 21:00–21:30 London.
10. Owner explicitly activates the single reviewed Linux cron.
11. Observe first genuine unattended 21:00 post; completed checkpoint, known media ID,
    correct asset, queue advance, palette and durable daily guard/logs.
12. Preserve current-state rollback until owner accepts; only then close 056/053 and
    consider 054 cleanup. If not ready by 21:00, keep Linux disabled and use Mac manual.

## Development validation — 28 October 2026

Base `9c03d8a1de54e4337b74b477bc8bc45de5ed1069`: both main CI workflows green.
`make setup` passed. Final `make check`: **1,387 tests passed**, Ruff lint and
format passed, Pyright zero errors/warnings. Native Linux suites, network-none
recreation/refresh/disabled-scheduler/log-retention/migration smoke, and image
secret-exclusion audit passed; the arm64 audit inspected **6,154 layer files**.
`sh -n deployment/setup-host.sh` and `git diff --check` passed. The shell was not
executed against a production host; Ubuntu install, SSH/firewall/cron semantics,
storage encryption and real target egress remain operator acceptance.

Two Mac/Linux JPEG pairs were inspected at 1080×1350: readable, unclipped, preserved
colours/highlights/layout, visibly different typography. Owner approval is pending.
No paid resource, real credential read/export/transfer, production DB write, Mac
scheduler change, public staging test, live provider call or scheduled publication
was performed. The PR's CI records the final native test count and amd64 audit.
