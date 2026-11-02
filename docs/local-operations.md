# Local operations and immutable upgrades — AED-060

The existing Docker one-shot engine and 19:00–19:30 Europe/London policy remain
unchanged. The Mac must be awake, logged in, online and running Docker Desktop.
The host accepts delivery only in the 19:00 minute; late wake events skip that day.
There is no retry, catch-up, second credential timer or automatic consent renewal.
A valid 30-day application authorization and separate host activation are required.

On 1 November the owner reported that Meta's restriction was resolved and
`credential-identify` returned HTTP 200 for `ask.every.day`, expiry 27 December
2026. The reported PR #52 image is
`sha256:eeadda9e8b3f73b56afa2e1a33bcb682013dc9c1bedac9680b13ec14a91a3d0a`.
That is owner-supplied evidence, not a new provider check by this change. The
active deployment remains pinned to its previous image until explicit cutover.
This PR's additional diagnostics require an image built from this reviewed commit.
Do not relabel an old image as new source or assume a build upgrades deployment.

Optional [Discord outcome notifications](discord-notifications.md) reuse the private
credential mount and remain independent of publishing authority.

## Nonpublishing health

Use the **installed** script and Python for an existing deployment; newer checkout
code deliberately cannot operate an old script binding. Set the actual private
path, then read only the non-secret Python setting:

```sh
AED_OLD="$HOME/Library/Application Support/AskEveryDay/aed-058-1900-20261029"
AED_OLD_PYTHON="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["python"])' "$AED_OLD/host/settings.json")"
"$AED_OLD_PYTHON" -I "$AED_OLD/host/desktop_scheduler.py" diagnose --directory "$AED_OLD"
```

After upgrading to this revision, `diagnose` additionally reports queue count,
next Post ID/position, last successful publication/checkpoint, failed-attempt
count, the last host admission outcome, actual authorization expiry and (only
with `--check-credentials`) local credential expiry. Historical closed failures
are counted, not silently retried. An unresolved checkpoint still blocks readiness.
No caption or token is included. All diagnostic container queries use network-none.

`next_scheduled_at` is the next calendar opportunity after the current start minute,
first activation date, daily publication guard and persisted host receipt.
`next_eligible_at` is null unless local readiness, credentials, authorization and
queue permit that opportunity. `next_run_requirements_unsatisfied` distinguishes
an otherwise ready host whose next run lacks valid consent, credentials or queue.
Neither timestamp promises remote acceptance. The timestamp
includes the London UTC offset, including across BST/GMT transitions. An opportunity
in the current 19:00 minute is represented by that minute's start, not a catch-up
request. `warnings` includes expiry within seven days, expiry before the next run,
empty queue and a failed/incomplete last host admission. An `admitted` receipt can
mean a host preflight failure or interruption: inspect logs and SQLite, never replay.

`diagnostic=passed`/exit 0 means the diagnostic completed, not that publishing is
armed; read `status`, `warnings` and `next_eligible_at`. `remote_verification` always
says `not_requested_offline_only`. Before upgrade, old scripts expose fewer fields.

For end-of-day detection, reuse the existing read-only database monitor:

```sh
uv run python deployment/desktop-compose.py --directory "$AED_OLD" monitor
```

After 19:30 it exits nonzero unless exactly one completed publication with a known
media ID exists today, and it flags unresolved outcomes at any time. Inspect
`host/host.jsonl` and `state/scheduler.jsonl` together: host receipts are admission
records, not publication proof. This command installs no timer or notification.
Local diagnostics cannot report while the Mac is off; independent alerts and
scheduled backup/restore drills remain open AED-060 work, not claimed here.

## Credential lifecycle

Normal scheduled publication uses `CredentialManager.get_valid_credential()` and
the persistent Linux `FileCredentialStore`. No repeated Keychain handoff is needed.
The current implementation refreshes during the last seven days of validity only
when the token is at least 24 hours old and unexpired, then verifies the same
professional account and atomically replaces the private credential file. Expiry
uses the provider's validated `expires_in` measured from request start; issuance
uses response receipt time, avoiding an overstated age. File and directory fsync,
atomic rename, mode 0600 and directory mode 0700 preserve durability across containers.

Refresh HTTP errors now retain the same bounded code/subcode/type/classification
as publishing errors. Provider prose, request URLs and tokens remain excluded.
Code 190/HTTP 401 requires account/token investigation; permission/configuration
rejections require owner remediation. A code alone does not prove expiry or
revocation. No failure automatically deletes the credential, retries a request or
falls back to the old token for that invocation. If refresh succeeds remotely but
identity/persistence fails, the old file remains (a post-rename fsync failure can
leave the complete new file); neither partial bytes nor an unverified token are
returned. Inspect local status and perform explicit verification before recovery.

Stored scopes prove what was granted at authorization, not continued permission.
Identity GET verifies account access, not every publishing permission. Browser
re-consent is owner-operated if permissions are removed; developer restrictions
must be resolved in Meta. See [authentication](instagram-auth.md) and Meta's
[Instagram business login reference](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login/).

`credential-status` and `schedule-check-credentials` are local-only.
`credential-identify` explicitly contacts Meta with GET and never refreshes/saves.
`credential-verify` explicitly contacts Meta and may refresh/save an eligible token;
it sends no media POST. They are separate operator actions, not part of offline
health. Routine authorized daily runs provide the existing seven-day refresh margin;
another timer would add a second credential writer and would not fix an offline
Mac. If posting is paused or authorization expires, refresh also stops. Before a
long pause exhausts token validity, the owner may run `credential-verify`; an expired
or revoked token needs reconnection, not repeated automated attempts. Thirty-day
publication consent still requires explicit renewal even when the token is valid.

## Operator-controlled upgrade

These are **owner-only steps after review/merge and green CI**. They have not been
executed by this PR. Stop on any failed command or mismatch. Work sufficiently
before 19:00; if incomplete, skip the day. Keep all private files outside Git and
on protected local storage. Never edit pinned hashes or installed code in place.

1. Select the reviewed source/image and a **new** destination. Inspect immutable
   image ID and revision label. The following example uses a fresh build; to deploy
   the already-built PR #52 image, verify it against that reviewed revision instead.

   ```sh
   umask 077
   AED_NEW="$HOME/Library/Application Support/AskEveryDay/aed-reviewed-upgrade"
   AED_TAG="ask-every-day:$(git rev-parse --short HEAD)"
   DOCKER_CONTEXT=desktop-linux make -f container/Makefile build IMAGE="$AED_TAG" REVISION="$(git rev-parse HEAD)"
   AED_IMAGE="$(docker --context desktop-linux image inspect "$AED_TAG" --format '{{.Id}}')"
   docker --context desktop-linux image inspect "$AED_IMAGE" --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
   ```

2. Disable future old launches using its installed script (variables from above).
   Inspect processes and containers. Wait for any active publisher to finish;
   never kill or force-unload it. If its outcome is uncertain, reconcile before
   continuing. Freeze all manual, queue, render, recovery and credential writers.
   Keep the legacy native Mac publisher disabled/unloaded.

   ```sh
   "$AED_OLD_PYTHON" -I "$AED_OLD/host/desktop_scheduler.py" disable --directory "$AED_OLD"
   docker --context desktop-linux ps -a --format 'table {{.Names}}\t{{.Status}}'
   pgrep -fl 'ask_every_day|publish_next_cli|scheduler_cli|desktop_scheduler' || true
   uv run python deployment/desktop-compose.py --directory "$AED_OLD" publication-status
   # Only after confirming no active/uncertain publisher:
   "$AED_OLD_PYTHON" -I "$AED_OLD/host/desktop_scheduler.py" uninstall --directory "$AED_OLD"
   uv run python deployment/desktop-compose.py --directory "$AED_OLD" schedule-disable
   ```

3. Export **current authoritative Docker state**, never the initial Mac snapshot
   or old `source-bundle`. `state/aed.toml` is the restore helper's host-relative
   configuration. Check it still represents `config/aed.toml` (same account,
   renderer/palette; paths resolve to this deployment's state/assets) before use.
   If configuration differs, stop and reconcile it explicitly. The coordinated
   export locks and snapshots SQLite, ledger, queue, content and canonical assets;
   restore is private and omits active scheduler authorization.

   ```sh
   uv run python -m ask_every_day.deployment inspect --config "$AED_OLD/state/aed.toml" --data-dir "$AED_OLD/state/data" > "$AED_OLD/upgrade-source-check.json"
   uv run python deployment/desktop.py --source-config "$AED_OLD/state/aed.toml" --data-dir "$AED_OLD/state/data" --directory "$AED_NEW" --image "$AED_IMAGE"
   uv run python -m ask_every_day.deployment verify --bundle "$AED_NEW/source-bundle"
   uv run python -m ask_every_day.deployment inspect --config "$AED_NEW/state/aed.toml" --data-dir "$AED_NEW/state/data" > "$AED_NEW/destination-check.json"
   cmp "$AED_OLD/upgrade-source-check.json" "$AED_NEW/destination-check.json"
   ```

4. With separate owner permission for credential access, copy the **current**
   FileCredentialStore into the empty new private directory; do not export an old
   Keychain token. No command should print credential bytes. Copy non-secret auth
   configuration and any reviewed optional font overlay, preserving its private
   read-only font mount. Do not copy old authority, activation, settings or active
   application authorization. This example preserves the current credential file;
   retain the configured app credential plus any dedicated Discord configuration and
   delivery receipt files. These share the private mount but separate service namespaces.
   Preserve all `.credential` files; never copy only an old Instagram token and discard
   notification deduplication evidence. The transient `discord-notifications.lock` file
   is recreated on first use and contains no delivery state.

   ```sh
   cp -p "$AED_OLD/config/instagram-auth.toml" "$AED_NEW/config/instagram-auth.toml"
   cp -p "$AED_OLD/credentials/"*.credential "$AED_NEW/credentials/"
   uv run python deployment/desktop_scheduler.py prepare --directory "$AED_NEW" --docker "$(command -v docker)"
   # Preserve the daily guard exactly; absence is acceptable only if no admission ever occurred.
   if test -f "$AED_OLD/host/last-invocation.json"; then
       cp -p "$AED_OLD/host/last-invocation.json" "$AED_NEW/host/last-invocation.json"
       cmp "$AED_OLD/host/last-invocation.json" "$AED_NEW/host/last-invocation.json"
   fi
   uv run python deployment/desktop_scheduler.py diagnose --directory "$AED_NEW" --check-credentials
   ```

   Require private UID/GID 10001 bind-mount checks, canonical fonts, local credential
   validity and unchanged history/queue/palette/assets. A diagnostic blocked by a
   permission or unresolved-state error is a stop, not permission to chown/delete
   evidence. Preserve the old directory's logs/receipts and export as inactive
   recovery evidence. Never clear today's receipt to regain an admission.

5. With explicit owner permission, verify current remote identity (GET only), then
   review the exact next JPEG/caption/hash without preparing or replacing history.
   If an unattempted draft must be updated, use the existing reviewed `prepare-next`
   workflow and compare changes; never regenerate a published or unresolved asset.

   ```sh
   uv run python deployment/desktop-compose.py --directory "$AED_NEW" credential-identify
   uv run python deployment/desktop-compose.py --directory "$AED_NEW" manual-preview --data-dir /var/lib/aed/data
   uv run python -m ask_every_day.deployment inspect --config "$AED_OLD/state/aed.toml" --data-dir "$AED_OLD/state/data" > "$AED_NEW/source-recheck.json"
   cmp "$AED_OLD/upgrade-source-check.json" "$AED_NEW/source-recheck.json"
   ```

6. Only after freshness/identity/renderer approval, attest sole authority, install
   disabled, grant fresh 30-day application consent, and separately activate the
   host using the existing [cutover steps 4–5](docker-automation.md#4-generate-diagnose-and-accept-sole-docker-authority)
   with `AED_DEPLOY="$AED_NEW"`. No `run`, `kickstart`, retry or manual publication
   is an upgrade test. Activation at/after 19:00 starts eligibility tomorrow.
   The copied receipt still suppresses a second admission on its recorded date.

After cutover, only the new Docker database/credentials are authoritative. Observe
the next genuine calendar execution: require Published/completed, durable media ID,
one queue removal and one palette advance; compare historical asset hashes. Check
host and application logs plus `monitor`. Until this succeeds, AED-058 and related
operational acceptance remain open, regardless of green CI or credential identity.

## Rollback

Disable the new host with **its installed script**, let any active attempt finish,
and inspect/reconcile uncertainty. Do not reactivate the old directory. Prepare
another fresh candidate from the latest authoritative Docker database, palette,
assets, credentials and host receipt using the same procedure, pinning a compatible
reviewed image. If that image cannot read/verify the current schema or renderer,
remain paused; never downgrade data or reset history. Restore an older snapshot
only after proving there has been no subsequent attempt, credential refresh or
state change; normally export the latest state instead. Recovery grants fresh
sole authority/application/host consent, and never bypasses same-day admission.
