# AED-056 hosted publishing runbook

The review candidate uses an Ubuntu 24.04 VPS with the existing Docker publisher
and independent systemd watchdog. It publishes at 20:00 Europe/London, checks
durable completion at 20:35, and verifies an encrypted off-host backup at 20:50. See the [monthly comparison](hosted-costs.md).

## Prepare an inactive host

1. Confirm the owner accepts the actual provider quote, IPv4, storage encryption
   and backup terms. Verify encryption at rest for active storage and provider
   backups before transferring private data. The VPS plan's backup listing alone
   does not establish encryption. If the provider cannot confirm it, keep the
   host synthetic and review encrypted block storage, including reboot unlock and
   cost. Protect console access with MFA and verify the SSH host key independently.
2. Use Ubuntu 24.04 and two working key-authenticated SSH sessions before invoking
   the reviewed `setup-host.sh ADMIN_SSH_CIDR` on the new host. Limit provider and
   UFW inbound IPv4/IPv6 to SSH from that source. Confirm time sync, Docker,
   SSH recovery, available disk and storage mounts. Keep Docker access root-only.
   Allow outbound HTTPS to Meta, R2, Discord and Healthchecks.io. The publisher
   exposes no inbound application port. Reboot the empty host and verify SSH,
   encrypted storage availability, Docker and time synchronisation before transfer.
3. Build the reviewed runtime for the VPS architecture, verify CI and transfer it
   by authenticated SSH. Compare archive SHA256, image ID, architecture and revision
   label after `docker load`. Build from reviewed main after both the hosted and
   Pillow security PRs are integrated. For an x86 host use an amd64 builder or
   `docker buildx build --platform linux/amd64 --target runtime --load` with the
   reviewed `AED_REVISION`. Verify the existing renderer and private R2 origin
   with synthetic assets before introducing production state.
4. Install the reviewed trigger and units without enabling timers.

   ```sh
   sudo install -m 0644 deployment/trigger.py /usr/local/lib/aed/trigger.py
   sudo install -m 0644 deployment/systemd/aed-* /etc/systemd/system/
   sudo systemd-analyze verify /etc/systemd/system/aed-{publish,watchdog,backup}.{service,timer}
   sudo systemctl daemon-reload
   ```

Use the existing [container layout](container-runtime.md) with the private parent
`/srv/aed`. Copy `container/compose.yaml` to `/srv/aed/compose.yaml`, substitute
the verified immutable local image ID and write that ID to `/srv/aed/image-id`.
Keep root-owned Compose and metadata private, `config/` root:10001 mode 0750
with 0640 files, and `state/` and `credentials/` UID/GID 10001 mode 0700
with 0600 private files. `host/` is root 0700. The config and state mount paths
are verified. Use only the reviewed optional fonts overlay if needed.

Copy `deployment/schedule.toml` to `config/schedule.toml`. Preserve production
account, palette and renderer config while setting container paths to
`/var/lib/aed/aed.sqlite3` and `/var/lib/aed/assets`. Copy the non-secret app
identity configuration separately. Use `--data-dir /var/lib/aed/data` in all
operator commands to select the restored approved content.

## Prove private R2 and backups

Create two private R2 Standard buckets, with separate bucket-scoped Object Read &
Write keys. The media bucket serves a signed GET for the approved immutable JPEG.
Keep public development URLs off. Retain objects for uncertain attempts, including
after restart, using the [incident retention procedure](media-incident-2026-11-22.md).
Use hidden prompts and the existing UID 10001 credential store:

```sh
sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -it publisher origin-configure
sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -T publisher origin-probe
```

Require both synthetic HTTPS verification and deletion of the probe. This is
independent of Meta credentials. Include the reviewed fonts overlay on all Compose
commands if installed.

Restic encrypts the application backup before upload. Keep its password in an
offline password manager as well as a root-only 0600 file on the protected host.
Create `/srv/aed/host/backup.env` via a private root editor, mode 0600. Its
systemd EnvironmentFile contains these four settings with actual values:

```text
RESTIC_REPOSITORY=s3:https://ACCOUNT_ID.r2.cloudflarestorage.com/BACKUP_BUCKET/aed-backups
RESTIC_PASSWORD_FILE=/srv/aed/host/restic-password
AWS_ACCESS_KEY_ID=BACKUP_BUCKET_KEY_ID
AWS_SECRET_ACCESS_KEY=BACKUP_BUCKET_SECRET
```

Create a second Healthchecks.io cron check at `50 20 * * *`, Europe/London, grace
25 minutes, with Discord integration. Place its UUID ping URL in
`/srv/aed/host/backup-healthchecks-url` (root 0600). Exercise an intentional failed
or missed check with disposable resources. Successful verification sends a normal
ping, failed verification sends `/fail`, and host disappearance triggers a missed
ping. 
Initialize once on the approved empty repository using the configured environment.

```sh
sudo systemd-run --wait --collect --property=EnvironmentFile=/srv/aed/host/backup.env /usr/bin/restic init
sudo systemctl start aed-backup.service
sudo journalctl -u aed-backup.service -n 30
```

Run the service first against disposable AED data and again after verifying the
coherent production transfer. On the inactive candidate, an empty state mount can
be seeded with the repository smoke fixture:

```sh
AED_IMAGE="$(sudo cat /srv/aed/image-id)"
sudo docker run --rm --network none --read-only -i \
  --mount type=bind,src=/srv/aed/state,dst=/var/lib/aed \
  --entrypoint python "$AED_IMAGE" - initialize < container/smoke_case.py
```

Use the synthetic state only for acceptance, then archive it outside the live mount
before installing the transferred state. Rename the empty or synthetic state on the
same filesystem while all timers are inactive. Retain the source Mac state. Require `backup_verified` and a green external check.
The job exports SQLite, palette, assets, content and application evidence under
the normal application lock and takes the latest host invocation receipt under the
host lock. It uploads through restic, restores that exact snapshot into a temporary
directory, verifies the application manifest and compares its receipt. Credentials
are recovered separately through protected copies or rotated/reissued at disaster
recovery; keep current credential recovery material in protected storage. Keep
manual writers quiescent during backups. An unresolved attempt blocks the normal
migration export and raises a backup alert. Preserve its evidence on the source
and use the separately documented recovery backup if needed.

Retention is owner-controlled. Review storage monthly and apply restic
retention only after a verified fresh snapshot, keeping at least 14 daily and
eight weekly snapshots where available. Do not apply a bucket lifecycle rule to
restic data objects as it can invalidate retained snapshots.

## Configure the off-host watchdog

Create a Healthchecks.io cron check named **AED hosted watchdog missing or failed
— inspect VPS and publication-status**. Use `35 20 * * *`, timezone Europe/London
and 10 minutes of grace. Connect its Discord integration and test delivery
separately from AED's webhook. An email integration can cover Discord outages.
Install its UUID ping URL through a private editor into
`/srv/aed/host/healthchecks-url`, root 0600. Treat the URL as a secret.
Configure the existing private AED Discord webhook and verify it using the
[notification-test command](discord-notifications.md). Test a missing check with
a disposable check before switching on production. [Healthchecks documentation](https://healthchecks.io/docs/)

The watchdog timer is separate from the publisher and reads SQLite checkpoints
and Discord receipts. When it finds a problem it sends the existing Discord
channel a classification, safe attempt context and recovery instructions.
A normal off-host ping means completion was verified or the watchdog delivered
an actionable alert. Its body records the classification. When evidence or
direct delivery is unavailable it sends `/fail` with the safe classification.
The external check then alerts independently through its integration. Read the
safe classification in the check's ping timeline. When the
VPS, network or watchdog disappears, the missing ping triggers the off-host
alert. A missing ping alone cannot identify which component is down.

| Watchdog result | Operator action |
| --- | --- |
| `scheduler_never_triggered` | Check timer status, boot history, clock and host journal. |
| `infrastructure_unavailable` | Inspect Docker, disk and container start. Check the external ping timeline if the host is unreachable. |
| `preflight_failed` or `publication_missing` | Inspect approval, queue, network readiness and scheduler JSONL. |
| `publishing_failed` | Read the definitive closed checkpoint and correct its cause. |
| `outcome_uncertain` | Stop and use the [reconciliation runbook](publication-recovery.md). |
| `notification_failed` | Publication is durably completed. Repair only notification delivery. |

## Transfer and activate with the owner

1. Confirm which Docker deployment is authoritative using the **installed** host
   script's `diagnose` and the [attempt 13 evidence procedure](media-incident-2026-11-22.md#attempt-13--owner-read-only-procedure).
   Use the installed job and pinned image to identify the source, then run:

   ```sh
   uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" publication-status
   uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" reconcile --attempt-id 13
   ```

   Last known evidence is AED-0148 in `Publishing/create_intent`, revision 1,
   container and media ID unknown. HTTP 400 / 2207003 establishes a download
   timeout. Escalate the original timestamp, app/account, operation, code and
   any independently captured trace to Meta. Without authoritative correlated
   evidence and a supported resolution, keep cutover paused. Never replay,
   requeue or close the attempt based on the HTTP code or the public feed.
   Verify the actual latest completed attempt and queue head after reconciliation.
2. Follow the [coordinated export/restore and freshness checks](local-operations.md#operator-controlled-upgrade)
   using that Docker deployment's current `state/aed.toml` and `state/data`.
   The owner disables/unloads the old LaunchAgent, waits for all writers to finish
   and reconciles any uncertainty before export. Retain the old configuration,
   host logs, latest admission receipt and original approval as evidence.
   Transfer the verified bundle by SSH and restore to an empty private directory
   with the reviewed deployment module, running offline. On the VPS, with an existing
   private transfer directory accessible to UID 10001 and a new restore target:

   ```sh
   AED_IMAGE="$(sudo cat /srv/aed/image-id)"
   sudo docker run --rm --network none --read-only \
     --mount type=bind,src=/srv/aed/transfer,dst=/transfer \
     --entrypoint python "$AED_IMAGE" -m ask_every_day.deployment \
     verify --bundle /transfer/current-bundle
   sudo docker run --rm --network none --read-only \
     --mount type=bind,src=/srv/aed/transfer,dst=/transfer \
     --entrypoint python "$AED_IMAGE" -m ask_every_day.deployment \
     restore --bundle /transfer/current-bundle --target /transfer/restored
   ```

   Compare the manifest's canonical state with a fresh source inspection before
   promotion. Equality includes digests binding media IDs and checkpoint state,
   queue, palette and every canonical file. From the Mac, with SSH identity and
   source path already verified:

   ```sh
   uv run python -m ask_every_day.deployment inspect --config "$AED_DEPLOY/state/aed.toml" --data-dir "$AED_DEPLOY/state/data" > source-recheck.json
   ssh "$AED_VPS" 'sudo cat /srv/aed/transfer/current-bundle/manifest.json' > target-manifest.json
   uv run python -c 'import json; assert json.load(open("source-recheck.json")) == json.load(open("target-manifest.json"))["state"]'
   ```

   Keep these reports private. The target bundle must have passed its local
   verification and restoration. Move the verified restored directory into the
   empty state location with UID/GID 10001 before any writer starts.
3. With owner approval, transfer the **current Docker** FileCredentialStore and
   Discord receipts over authenticated SSH to the protected host, outside the
   general state bundle. Retain the private files and set UID/GID 10001. Copy
   the source `host/last-invocation.json` to the target host directory unchanged
   and verify its checksum. Carry the old approval in protected evidence.
   The new runtime and deployment binding require fresh application approval.
4. Verify the offline previews, canonical fonts, exact next asset and credentials.
   The owner explicitly performs the existing identity GET check before cutover.
   Verify that no Mac LaunchAgent, old Linux cron or other host can publish.
5. Require a verified VPS backup and R2 probe. On the VPS before the target minute, review both authorisations,
   then enable the timers. The `arm` challenge attests sole authority and pins
   the reviewed configuration and SQLite identity. It does not start a container.

   ```sh
   sudo docker compose -f /srv/aed/compose.yaml run --rm --no-deps -it publisher schedule-enable --data-dir /var/lib/aed/data
   sudo python3 -I /usr/local/lib/aed/trigger.py arm
   sudo python3 -I /usr/local/lib/aed/trigger.py show
   sudo systemctl enable --now aed-publish.timer aed-watchdog.timer aed-backup.timer
   systemctl list-timers 'aed-*'
   ```

   Include the reviewed fonts overlay on operator Compose calls if installed.
   At or after the slot, arming sets first eligibility tomorrow. A copied same-day
   admission receipt blocks a second launch even after fresh authorisation.
   Once enabled, the VPS owns the authoritative state. Verify the first unattended
   post with a completed checkpoint, known media ID, queue and palette advance,
   and off-host watchdog ping. Repeat for seven consecutive unattended days. Use `journalctl -u aed-publish.service -u aed-watchdog.service`
   and the persistent scheduler JSONL to compare evidence. Bound journald disk usage
   and preserve incident logs before rotation.

## Change time, renew consent and recover

Stop only the publisher timer and wait for any active attempt. Edit private
`config/schedule.toml` hour/minute with its existing permissions. Update the
off-host cron check to 35 minutes after the new London start (wrap at midnight).
Review application consent with `schedule-enable`, rerun `arm` and inspect
`show` before restarting the timer. Plan such changes outside the publishing and
watchdog windows. The minute gate uses London time and skips spring gaps and
the second occurrence of autumn's repeated minute. Prefer an evening slot to
avoid ambiguous watchdog deadlines around clock changes. Keep the 30-minute
publishing window and 30-day consent. Time changes require no image rebuild.
Application approval expires after 30 days and can also become invalid after a
queue, content or renderer change. Renew with the existing TTY challenge.
The smallest follow-up is an expiry warning seven days before renewal. Longer
consent requires a separate owner decision.

The host records one admission before Docker preflight and rejects late or
repeated dispatch. The existing DB lock and checkpoints cover the media flow.
No automatic recovery reopens a consumed day. A confirmed preflight failure
can be repaired for the next slot. An operator can separately assess manual
recovery using current state and evidence, without clearing receipts or
replaying an uncertain POST.

For maintenance or rollback, stop future timer activations, wait for current
work and use explicit reconciliation if needed. Back up current state using
the coordinated export and the verified encrypted off-host repository. Separately protect
refreshable credentials, host receipts and approval evidence. Test restoration
into a fresh inactive directory. Standard VPS backups run daily, so their
restore point can predate a remote publication. After storage loss keep
publishing paused until the latest remote outcome and receipts are reconciled.
Any rollback to Docker Desktop must restore the **latest authoritative state**,
then explicitly grant fresh approval and activate only one host.

### Go/no-go before enabling the host

- Owner approved the actual quote, at-rest protection and the sole VPS cutover.
- Attempt 13 and any other uncertain outcomes have conclusive, reviewed resolution.
- One reviewed image includes the hosted work, R2 integration and Pillow security fix.
- SSH, clock, encrypted mounts and Docker survived a reboot on the inactive host.
- R2 synthetic probe and both independent Discord alert paths passed.
- Latest source and restored state reports match, and the exact admission receipt is preserved.
- The latest backup restored and verified successfully. The Mac and other publishers are inactive.
- Fresh time-limited application approval and host attestation are in place. Keep the incident and hosted ticket open until all seven production checks succeed.
