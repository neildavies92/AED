# Configurable daily publishing — AED-058

Docker Desktop defaults to **20:00 Europe/London daily**, with a 30-minute publishing
window and 30-day authorisation. The sole schedule source is the private deployment's
`config/schedule.toml`, mounted read-only at `/etc/aed/schedule.toml`. Existing private
files are not changed by a Git merge, image build or installation.

This is one daily slot. Multiple slots, catch-up and automatic retries remain separate
future work. The Mac must be awake, logged in and running Docker Desktop with its
host timezone set to Europe/London.

## Operator commands

After deploying this reviewed version, select the **current authoritative deployment**:

```sh
AED_DEPLOY='/absolute/path/to/current/authoritative/deployment'
make docker-schedule-show DIRECTORY="$AED_DEPLOY"
make docker-schedule-set DIRECTORY="$AED_DEPLOY" TIME=20:00
make docker-schedule-show DIRECTORY="$AED_DEPLOY"
```

These wrappers use the installed interpreter and hash-bound host script. The equivalent
installed command is `desktop_scheduler.py schedule-set 20:00 --directory ...`.
`show` is nonpublishing and does not read credentials. It reports the time, timezone,
window, effective date, application approval, host state, next calendar opportunity
and any admission receipt. Use `diagnose` for full readiness; an opportunity is not
proof that a publication will succeed.

`set` requires a TTY and the exact displayed confirmation. It changes only the daily
time and effective date; timezone, window and consent duration stay fixed. Use two-digit
`HH:MM`, from `00:00` through `23:30` so the window does not cross midnight. The default
effective date is **tomorrow in London**. A disabled/uninstalled trigger stays disabled;
changing configuration does not grant publishing permission.

For an explicitly owner-approved change **today**, before the proposed start:

```sh
make docker-schedule-set DIRECTORY="$AED_DEPLOY" TIME=20:00 TODAY=1
```

This is rejected if today's host admission has already been consumed, any publication
completed/was attempted today, any unresolved checkpoint exists, or the proposed minute
has passed/is nonexistent due to DST. A known failed attempt today also blocks a
future-effective edit; investigate first and try on a later clear day. The receipt is
never deleted or rewritten. A missed old trigger with no admission or attempt can move
to a future slot only through this explicit approval. The command rechecks time and
state before committing. If approval expires first, renew separately.

## Authorisation and consistency

The host admission/configuration locks serialize edits and lifecycle operations. A
short-lived **network-isolated** container then holds the same publication lock used
by manual/scheduled publishing while SQLite is opened read-only. It validates the
current queue, historical checkpoints and approval using the actual container runtime,
renderer and content. No credentials are needed, and no application publishing API is
called. A competing container/publication, changed database identity, script/image,
Compose mount or hand-edited configuration blocks the change.

If existing application approval is valid, the participant changes only its policy
fingerprint for the newly approved time/effective date. Original queue/history bindings,
authorisation date and expiry remain unchanged. Expired, disabled, absent or drifting
approval is never made valid by editing a schedule. The result gives
`authorization_preserved=false` and `reauthorization_reason`; the host is left disabled.
Use the existing interactive `schedule-enable` and host `activate` after reviewing that
reason. An unreadable/corrupt authorisation record blocks the operation for investigation.

The host writes the policy, deployment binding, authority/activation records and plist
with fsync and atomic replacement under a durable change barrier. It reloads an existing
LaunchAgent without `RunAtLoad` or `kickstart`, preserving its previous activation state
only when approval remains valid. The application commits the new approval last. Its
`effective_from` gate prevents direct scheduled invocations from using a tomorrow-only
change today. The installed plist and private policy are checked for drift on admission.

A launchd reconfiguration failure restores the old files/trigger before releasing the
locks. A lost application commit acknowledgement leaves the host disabled and may
require fresh approval. If rollback fails or the host is interrupted, the journal blocks
host dispatch and the config barrier blocks media publication. Inspect the named container;
never kill an uncertain publisher. Once no operation is active, recover explicitly:

```sh
make docker-schedule-recover DIRECTORY="$AED_DEPLOY"
make docker-schedule-show DIRECTORY="$AED_DEPLOY"
```

Recovery restores the previous host/configuration files and **leaves the host disabled**.
It never restores SQLite or removes daily receipts. Inspect `schedule-status`, reauthorise
if the policy no longer matches, and explicitly activate only after checking current
state. Do not hand-edit hashes or remove a journal to regain admission. Structured
`host/host.jsonl` events record validation, rejection, activation, rollback and recovery
using times/dates and fixed reasons, without editorial content or secrets.

DST is evaluated in Europe/London. A nonexistent spring start minute is skipped; only
the first occurrence of a repeated autumn minute is eligible. A late wake, delayed
preflight or second invocation cannot backfill a missed start. Daily history and receipt
guards still apply independently of the chosen time.

## First upgrade and activation at 20:00

An older image/host script does not contain this transaction protocol. **Once**, after
review/merge and green CI, follow the [current-state upgrade procedure](local-operations.md#operator-controlled-upgrade)
using the latest authoritative Docker state, assets, palette, credentials and admission
receipt. Never use the original Mac database or an old export as the source. No live
upgrade or activation is performed by this PR. The new candidate defaults to 20:00.

Inspect `docker-schedule-show` and the ordinary nonpublishing diagnostics. Complete the
existing sole-authority/renderer review, install the disabled trigger, explicitly grant
application approval, and use the [owner activation steps](docker-automation.md#5-install-disabled-authorize-then-activate)
before 20:00. If the deadline is missed, eligibility starts tomorrow. Never use `run`,
`kickstart` or an Instagram post as an installation test.

After this one-time code upgrade, future time-only changes use `docker-schedule-set`
in place: no image build, deployment copy, credential handoff or DB migration. Verify
20:00/Europe/London in `show` and the next opportunity in `diagnose`. Preserve the
current deployment as sole authority. AED-058 operational acceptance remains open until
the owner verifies a genuine unattended completed checkpoint/media ID and correct
queue/palette advancement.
