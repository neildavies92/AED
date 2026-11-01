# AED-058: 31 October unattended preflight incident

The 19:00 London trigger ran deployed revision
`00a008d0582c3afea38491058065019877d82aa3`. At 19:00:08 it dispatched
Docker; at 19:00:11 the application reported HTTP 400 from `identify_account`
and `preflight_failed`, with no publication ID. This was not a missed launchd event.

## Confirmed findings

- Identity uses `GET https://graph.instagram.com/v26.0/me`, fields
  `user_id,username,account_type`, with a Bearer token in the Authorization header.
  The Instagram Login scopes are `instagram_business_basic` and
  `instagram_business_content_publish`. No token is placed in that request URL.
- The local check validates stored metadata, app binding, issuance and expiry.
  It does not contact Meta. `local_valid` cannot establish server-side validity,
  permission availability or account access. Credential acquisition checks identity
  remotely before publication; an eligible refresh is a separate existing operation.
- The old HTTP client discarded the error envelope on non-2xx responses. The
  credential manager also replaced an identity exception with `account_binding`.
  Consequently the precise Meta code/subcode is unavailable in the recorded logs.
  Expiry, revocation, permissions and request configuration remain hypotheses;
  HTTP 400 alone does not establish any of them. No endpoint/token changes are
  justified by the available evidence.
- Scheduler failures produced JSON (`outcome=blocked`), but omitted `local_day`.
  The host required that field for every outcome and replaced the known failure
  with `container_result_unverified_inspect_checkpoint`. It was not evidence of
  malformed stdout or a media POST in this incident.
- `launchctl print-disabled` returned `enabled`. The old boolean-only parser
  defaulted an unrecognized entry to disabled. Idle/non-running between calendar
  executions is normal. The parser now recognizes enabled/disabled/true/false;
  missing/unrecognized entries remain fail-closed.
- `publication-status` opens SQLite using `mode=ro` and `PRAGMA query_only=ON`.
  Its snapshot transaction ends with COMMIT but cannot write. The shared transaction
  wrapper mislabeled that as `repository_write_committed`; it now emits
  `repository_read_completed` (or `repository_read_failed`). Writable operations
  retain their existing events.

## State evidence and guarantees

Read-only inspection found four Published records (AED-0395, AED-0289, AED-0311,
AED-0066). The latter three have completed checkpoints; the first is retained
legacy history without a checkpoint. AED-0081 remains queue position 5 and has
zero publication attempts. The logs record preparation of its sequence-5 draft,
then identity rejection before staging or beginning an attempt. Preparing a draft
reserves stable colour/bytes but does not consume the queue or a successful palette
slot. No history, queue, assets, checkpoints, credentials or host configuration
were changed during this investigation.

The host invocation receipt still prevents another dispatch on that London date.
No automatic retry is added, even for a known preflight failure. Once an attempt
exists, durable checkpoint safeguards and reconciliation remain authoritative.
A failure after beginning an attempt is conservatively reported as requiring
reconciliation; it is never declared safe to replay based on HTTP status alone.

## Result and error contract

Scheduler run results now include `schema_version=1`. Existing successful/daily
no-op fields remain compatible. A nonzero failure has `outcome=blocked`, `local_day`,
fixed `reason`, typed operational IDs, `failure_category`, `publication_state`, and
safe `diagnostics`. Publication state is one of:

- `no_attempt`: publisher failed before creating an attempt; no media POST occurred.
- `reconciliation_required`: an attempt exists; inspect its durable checkpoint.
- `unknown`: failure outside the publisher boundary; do not infer a safe replay.

The host validates the envelope and exit code, preserves these distinctions in
its logs/result/receipt and retains the one-admission-per-day guard. Malformed,
contradictory or legacy incomplete results remain unverified. A successful result
still requires positive attempt ID, valid Post ID, matching date and exit code zero.

Meta diagnostics retain bounded integer HTTP/code/subcode values, an allowlisted
error type, operation, category and retryability. Provider message text, error_data,
trace IDs and arbitrary response fields are discarded. `safe_message` is generated
from the category, never copied from provider prose. Code 190/HTTP401 is classified
as authentication (not specifically expiry); selected permission/parameter codes
as configuration; rate limits/server/transient flags as transient. Unrecognized
rejections remain generic. Retryability is diagnostic only; no retry is executed.
Network uncertainty on a media POST always requires reconciliation.

## Owner-only next diagnostic

After reviewing/building the patched image, an owner can authorize this **one GET**
using the existing stored credential. It does not refresh/save credentials, mount
SQLite, render, stage or publish. Do not run `schedule-run`, delete the invocation
receipt or change the active LaunchAgent to investigate.

```sh
CANDIDATE="$HOME/Library/Application Support/AskEveryDay/aed-058-1900-20261029"
# Set REVIEWED_IMAGE to the immutable sha256 ID of the reviewed patched image.
docker --context desktop-linux run --rm --read-only --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
  --mount "type=bind,src=$CANDIDATE/config,dst=/etc/aed,readonly" \
  --mount "type=bind,src=$CANDIDATE/credentials,dst=/var/lib/aed-credentials,readonly" \
  "$REVIEWED_IMAGE" credential-identify
```

Record only the structured status/code/subcode/type/category. Do not paste the
credential file, token, headers, request URL parameters or raw provider response.
If local metadata is already expired, this command stops without a request; any
reconnection/refresh requires a separate owner decision. A successful GET establishes
identity at that moment, not permission to publish or proof every API scope works.

Deploying changes requires the existing reviewed immutable-image/host-binding and
fresh authorization procedure. Preserve/export the **latest authoritative Docker
state**, never restore the stale Mac source. Do not replace the installed script
or edit its hashes/receipts in place to bypass authorization. No deployment upgrade,
activation, credential operation or live API request was performed for this fix.
AED-058 remains In Progress until verified real unattended acceptance.
