# AED-066 · Instagram media download timeout

**Production remains blocked pending evidence for attempt 13.** The staging change protects future attempts. The incident evidence below comes from the owner's report on 22 November 2026. Development used disposable state.

## What failed

| UTC | Evidence |
| --- | --- |
| 20:00:08 | Scheduled execution selected AED-0148, palette sequence 13, Ice Blue. JPEG rendering, identity and quota GETs succeeded. |
| 20:00:15–20:01:25 | Cloudflared obtained a temporary hostname and registered a connector. Public GETs from the publisher repeatedly failed in DNS with `gaierror`, errno −2. |
| 20:01:29 | After about 79.6 seconds and approximately 602 readiness checks, one GET passed. AED began the attempt and committed CreateIntent revision 1. |
| 20:01:40 | The create-image-container POST returned HTTP 400, OAuthException, subcode 2207003. The publisher received no usable container ID. |
| 20:01:41 | Discord delivered the reconciliation-required notice. The context manager closed the tunnel and its HTTP loopback origin. |

Meta defines 2207003 as a timeout downloading the media. Its publishing service fetches the supplied URL independently. The error subtype and the successful identity/quota calls give no reason to rotate the Instagram credential for this incident. [Meta error codes](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/error-codes) · [Publishing guide](https://developers.facebook.com/documentation/instagram-platform/content-publishing)

On main `70d4c76`, the local origin pinned and validated the JPEG, the tunnel exposed HTTPS, and AED fetched and compared the exact bytes. Connector registration was observed for diagnostics. A failed public GET slept for at most 100 ms. Reopening the URL used the system resolver each time, with no AED DNS cache. The first successful local GET passed immediately. A deterministic test produced 822 requests before delayed DNS recovery and 1,151 on permanent DNS failure. Fast cached negative DNS answers are consistent with the reported frequency. No evidence identifies the cache, DNS authority or resolver responsible. Connector registration proves a transport connection to Cloudflare. A successful GET from the publisher proves one route worked at that instant. Neither identifies whether Meta reached DNS, TLS, the edge, or the origin. Teardown followed the 400 and cannot explain the earlier timeout.

## Origin decision and expected monthly cost

| Option | Incremental cost at one 100–200 KB JPEG/day | Operational consequence |
| --- | --- | --- |
| Harden Quick Tunnel | £0 | Spaced exact GETs reduce false readiness. The hostname changes every run and the origin disappears on publisher exit. Cloudflare reserves Quick Tunnels for development. |
| Private Cloudflare R2 Standard | Expected £0 within unused account allowances | Stable provider DNS and HTTPS independent of Docker uptime. One conditional upload, a few GETs and one normal cleanup per post. Owner provisions one bucket and a scoped credential. |
| Named tunnel with a persistent hostname | Potentially £0 incremental with an existing domain and connector | Removes hostname churn. Serving after a crash still requires a live origin, domain administration and a separate lifecycle. |

R2 Standard includes 10 GB-months, one million Class A and ten million Class B operations per month with free egress. Even retaining every image for a month adds only about 6 MB. Account-wide usage and future prices can affect the bill. Desktop and a future Linux publisher can use the same container and outbound HTTPS. [R2 pricing](https://developers.cloudflare.com/r2/pricing/) · [Quick Tunnel limitations](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

Both manual and scheduled publication use private R2 in the reviewed image. Missing configuration blocks before queue removal or CreateIntent. The one-file tunnel remains available for diagnostics. AED validates the canonical JPEG and approved SHA256, then uses conditional creation at `approved/<Post ID>/<sha256>.jpg`. An existing object (412) must pass the same full verification. The SDK signs a GET-only capability for 25 hours, covering the normal 24-hour container lifetime and preflight. URLs stay in memory. Keep the bucket private, disable public development URLs, and omit custom domains. A signed GET needs no cookies or headers from Meta. Actual Meta retrieval still requires owner acceptance. [R2 conditional operations](https://developers.cloudflare.com/r2/api/s3/api/) · [Presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/) · [Container status and expiry](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-container)

Two HTTPS 200 GETs on separate connections must each return image/jpeg, the exact Content-Length and every approved byte. Three seconds between successes samples more than one instant. A network error resets the sequence. Retries back off from one to five seconds within a 120-second budget. Wrong content, redirects and permanent HTTP errors fail early. A separate process bounds even a stalled system resolver. Only the media-origin worker handles SDK traffic. Staging completes before the existing admission and checkpoint sequence. A post-intent failure retains the private object across process exit and restarts. Only normal completion with a matching completed checkpoint/hash permits a best-effort single object deletion. Cleanup failure retains the object and completed publication.

**Retention:** Do not apply an age-only deletion rule to `approved/`. Review leftover objects after the 25-hour capability has expired, with writers idle and the authoritative history, checkpoints and canonical hashes available. Preserve all objects for unresolved or contradictory evidence. For an explicitly verified terminal outcome, preserve the audit evidence and canonical JPEG before owner-approved deletion of its exact private object. Preflight-only orphan cleanup requires confirmation that no attempt references that Post/hash and no staging process is active. Checking age or an empty Instagram grid alone is insufficient. A short lifecycle rule can be limited to `probe/`, which contains only synthetic assets.

## Attempt 13 · owner read-only procedure

1. Identify the currently installed and loaded publisher. Compare the loaded job's arguments, installed plist and the deployment's recorded image ID. If they disagree, multiple candidates are active, or the installed job is missing, stop and establish the authoritative state from the cutover records. The incident path ending in `aed-058-2000-20261116-70d4c76` is historical evidence.

   ```sh
   AED_PLIST="$HOME/Library/LaunchAgents/com.ask-every-day.docker-publisher.plist"
   launchctl print "gui/$(id -u)/com.ask-every-day.docker-publisher"
   plutil -p "$AED_PLIST"
   AED_DEPLOY="$(plutil -extract ProgramArguments.5 raw -o - "$AED_PLIST")"
   cat "$AED_DEPLOY/image-id"
   docker --context desktop-linux ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
   ```

2. From the reviewed checkout, read the authoritative deployment using its pinned image. These commands inspect SQLite through the existing offline recovery adapter. They require neither credential access nor a provider request.

   ```sh
   uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" publication-status
   uv run python deployment/desktop-compose.py --directory "$AED_DEPLOY" reconcile --attempt-id 13
   ```

   Expect attempts 11/AED-0087 and 12/AED-0376 to be completed, and attempt 13/AED-0148 to be Publishing/create_intent revision 1, with unknown container/media IDs and no PublishIntent. If current evidence differs, use the actual snapshot and the [checkpoint recovery contract](publication-recovery.md). Preserve private host admission receipts, application and host logs, alert timing, original JPEG/hash and palette sidecar. Keep writers quiescent for any coordinated evidence backup.

3. **Provider escalation is the blocker.** The local sequence shows AED reached the create request and never reached a publish intent for this attempt. The HTTP 400 establishes the provider-reported fetch timeout. It does not supply a durable request-to-container mapping. With owner approval, `reconcile --attempt-id 13 --remote` can verify the account with GET. Without an ID it cannot read any container status. Meta's documented status GET requires a known container ID. Account media listings, absence from a feed, caption, timing, image similarity and elapsed time cannot identify this request. Ask Meta support to correlate the account/app, UTC timestamp, operation, HTTP status, subcode and any **independently retained** request trace ID, and supply a definitive server-side outcome. Keep support evidence private. Do not send tokens or signed URLs.

4. If the snapshot still has CreateIntent with no ID, leave it unresolved. Existing recovery only closes a pristine Prepared checkpoint or an independently verified ERROR/EXPIRED **durably known** container with no PublishIntent. Even a later support response requires separate evidence review, design approval, snapshot/revision guards, and owner consent before any exceptional local mutation. There is no ordinary failed-resolution command for this incident. Do not repeat creation or publish, requeue the Post, reset history/palette/receipts, regenerate attempted assets or activate a second publisher. No staging fix clears this gate.

## Owner deployment and acceptance

The locked runtime dependency audit on 22 November reported 42 advisories against the existing Pillow 12.3.0 pin and recommended 13.0.0. Review exposure and test that renderer upgrade separately against canonical image fixtures before releasing. The new storage dependencies had no findings in this scan.

After PR review, assess the audit and green CI. Reconciliation and cutover are separate decisions. An unarmed, disposable candidate can test a synthetic object with an owner-approved R2 bucket before production recovery. Never copy authorisation or point a candidate at live SQLite. After approving storage, create a dedicated private R2 **Standard** bucket and a restricted Object Read & Write token scoped to that bucket only. Keep account administration credentials outside AED. The application uses object PUT/GET/DELETE and requires no bucket administration. [R2 API tokens](https://developers.cloudflare.com/r2/api/tokens/)

In the candidate only, with its reviewed image and private UID 10001 credential mount:

```sh
uv run python deployment/desktop-compose.py --directory "$AED_NEW" --interactive origin-configure
uv run python deployment/desktop-compose.py --directory "$AED_NEW" origin-probe
```

The first command stores the R2 keys through hidden prompts and the existing private file backend. The second uploads a generated 1080×1350 JPEG under a random probe key, performs both signed GETs and deletes that key. It uses no publication record or Instagram credential. Confirm probe success and bucket privacy. Keep logs free of capability URLs.

After conclusive resolution and explicit owner approval, follow the [immutable Docker-to-Docker upgrade](local-operations.md#operator-controlled-upgrade). Freeze writers and preserve the latest authoritative SQLite, checkpoints, canonical assets, content, palette, current credentials and host receipts. Verify export/restore and matching source/destination reports, attest sole authority, and approve the new image, application consent and host activation separately. Observe one genuine eligible 20:00 Europe/London run. Require one durable Completed checkpoint/media ID, the expected queue and palette movement, and a matching terminal alert. If uncertain again, retain the object and stop. Restore or roll back only from the latest authoritative state. Keep the incident open until production evidence supports closure.
