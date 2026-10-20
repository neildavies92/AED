# Public HTTPS media staging

AED-041 makes one existing validated JPEG temporarily fetchable without credentials.
It is separate from `instagram_client.py`; staging never contacts Instagram.
Quick Tunnel is for controlled first-post acceptance, manual early launch and
local development/testing. It has no uptime guarantee and is not the unattended
scheduler's final hosting architecture.

## Current evidence and decision — 18 October 2026

Rechecked official [Cloudflare Quick Tunnel documentation][quick] (updated
30 September 2026): an externally installed `cloudflared` can expose a local HTTP
service at a temporary public `trycloudflare.com` URL without an account/domain.
Anyone with the URL can fetch it; stopping the process ends access. Hostnames
change each session, and the provider offers no uptime guarantee. This supports
the smallest local-first approach: stdlib one-file origin plus Quick Tunnel,
without a Python SDK, account token, infrastructure or object storage.

Rechecked Meta's official [publishing guide][publishing], [media reference][media]
and [Graph changelog][versions]. The reader returned HTTP 429 on 17–18 October;
the Instagram Login-specific publishing URL was also unavailable. No fresh Meta
version, redirect behavior or fetch-lifetime guarantee could be established.
Conservatively retain the repository's [documented contract](instagram-publishing.md)
and [v26.0 client](instagram-client.md): public media URL, compliant JPEG and
availability throughout media creation/processing/verification. HTTPS, no
redirects, exact bytes and `image/jpeg` are this staging boundary's stricter
checks, not newly verified Meta requirements. Do not assume Cloudflare compatibility
with Meta has been demonstrated until the separate controlled live acceptance.

## Operator commands

Prerequisites: configured `aed.toml`, imported Post, rendered canonical
`assets_path/AED-0001.jpg`, and `cloudflared` already installed. See Cloudflare's
[official downloads/install instructions][install]. This application never
installs/downloads a binary or service. Missing binary fails with that guidance.
`make setup` installs only the existing locked Python dependencies in `.venv`.

```sh
make stage-media POST_ID=AED-0001
```

Or use an explicit configuration:

```sh
uv run python -m ask_every_day.media_staging AED-0001 --config aed.toml
```

After validating and verifying a public GET, the CLI deliberately prints the URL
once to stdout. Keep the foreground command running. Ctrl+C closes the tunnel,
origin, sockets and temporary configuration. Keep the machine awake and the
session open throughout a future authorized media create/status/verification
operation; a successful initial GET does not guarantee later availability.
Do not enable email authentication or a browser challenge: the intended client
must fetch without credentials. Never redirect stdout into operational logs or
commit/share the URL in a ticket. Anyone with it can read this one image while live.

## Lifecycle API

```python
from ask_every_day.media_staging import stage_media

with stage_media(repository, config, "AED-0001") as staged:
    public_url = staged.public_url
    # A separately authorized caller may use it during this context.
```

`StagedMedia` contains `post_id` and `public_url`; its representation omits the URL.
The caller owns the context lifetime. No database writes, PNG conversion or final
publication snapshots occur. The CLI opens existing SQLite read-only, so missing
storage is not silently created/migrated. Invalid source/setup failures become
sanitized `StagingError` diagnostics with no original provider exception chain.
Exceptions from the caller propagate after cleanup.

## Source and origin boundary

- Require a canonical ASCII AED-0001–AED-9999 ID and a persisted Post.
- Pin the configured asset directory and canonical filename with descriptor-based
  opens; reject symlink directory/file leaves and nonregular files, including FIFOs.
  Intermediate configured parent directories remain operator-controlled.
- Read at most the authoritative JPEG maximum plus one overflow sentinel and call
  `validate_jpeg()`: complete RGB JPEG, 1080×1350, at most 8,000,000 bytes and the
  existing metadata restrictions. Serve the validated in-memory bytes throughout
  the session, so subsequent source-file replacement cannot change the response.
- Bind only `127.0.0.1` at an ephemeral port. An independent random 256-bit path
  exposes exactly one JPEG. This is an unguessable URL, not authentication.
- Exact GET/HEAD return `image/jpeg`, correct Content-Length and no-store headers.
  Other paths, query variants and traversal fail with empty 404; mutation verbs
  fail with 405 on the route. No directory listing, upload or arbitrary file access.
- Silence default request/error logs. Track accepted sockets and close them on
  exit before joining handlers, including partial-header/slow clients.

## Tunnel and fetch behavior

Use a subprocess argument array with no shell and a new POSIX session/process
group. An isolated temporary HOME, explicit empty config, minimal environment and
`--no-autoupdate` avoid inheriting user tunnel credentials/config or updating the
binary. Explicit `--protocol http2` uses Cloudflare's TLS-protected TCP transport
instead of waiting for automatic QUIC fallback on networks where UDP fails.
Capture and drain provider output into a bounded buffer without logging
it. Accept only a base `https://<single-label>.trycloudflare.com` URL: no userinfo,
port, path, query, fragment or suffix-host tricks. Add only the generated route.

Before Ready, perform a standard-library GET without proxy credentials, auth or
cookies. Reject all redirects. Require status 200, `image/jpeg`, matching advertised
length when present, and exact complete staged bytes, bounded to image length + 1.
Retry readiness GETs within the startup window to tolerate initial tunnel routing
propagation. No downstream Instagram calls or POST retries occur here.

Defaults: startup budget 120 seconds and fetch socket timeout 15 seconds; both must
be finite, positive and at most 120 seconds. Read chunks check an elapsed deadline
between reads, preventing indefinite body trickling. These are not a hard total
wall-clock limit: DNS resolution is OS-controlled, response headers are parsed by
urllib, and an in-flight socket operation may consume its timeout before the next
deadline check. Do not describe the startup budget as a guaranteed total duration.
The origin has a three-second socket inactivity timeout; forced socket closure on
exit provides cleanup independently of that timeout.

On every normal/exceptional context exit, terminate the whole tunnel process group,
wait up to five seconds for its leader, then force-kill if necessary and reap it.
Also signal remaining descendants when the leader already exited. Stop/join the
bounded pipe reader, close its descriptor, stop the origin and join its handlers,
and delete temporary config. Cleanup failures produce a safe diagnostic. A tunnel
can still fail after readiness; a future consumer must treat a fetch failure as a
failure, not proof that an Instagram operation succeeded. This is POSIX tooling
for the current macOS development/Linux CI environments.

Operational events are `media_staging_started`, `media_staging_ready`,
`media_staging_failed`, `media_staging_stopped`, and `media_staging_readiness_retry`.
Retry summaries appear on the first failure and at most once per ten seconds;
the final ready/failed event includes the attempt count, elapsed milliseconds,
tunnel URL discovery milliseconds, and last verification failure if present.
Safe categories distinguish DNS, connect, TLS, timeout, HTTP status, content type,
content length, byte mismatch, read deadline, other OS errors and unknown errors.
Only known exception class names and numeric errno/status are retained, never
exception messages. A timeout names its last failure category. No URL, hostname,
random route, payload, editorial copy, credentials or raw provider output is
logged. The library never configures logging handlers.

## Verification and remaining acceptance

```sh
uv run pytest tests/test_media_staging.py
make setup
make check
```

Focused tests cover real local HTTP routes, headers, partial-client shutdown,
source validation, URL rejection, fake executable startup/exit/timeouts,
process-group descendant cleanup, forced termination fallback, no-auth byte
verification, redirects, bounded reads, safe diagnostics/logs and CLI interruption.
Normal tests forbid external socket connections; CI never needs Cloudflare.

### Readiness investigation — 20 October 2026

On main `7b72311b56d4036a58b2b3130f2576cacb48eff4`, `cloudflared` 2026.10.1
was already installed. `make setup` and `make check` passed (691 tests,
Ruff/Pyright clean); integrated-main CI run 39253619228 was green.
The real `make stage-media POST_ID=AED-0001` still exhausted its former
30-second startup budget. The generated RGB 1080×1350 JPEG (96,129 bytes)
passed authoritative validation, and its local origin stayed healthy.

Temporary diagnostics confirmed that AED captured exactly the emitted provider
URL and hostname. Independently launching the same binary, arguments and isolated
environment reproduced initial system DNS failures (`gaierror`, errno 8).
In two correctly paired fresh sessions, public DNS-over-HTTPS had A records by
5.21/5.18 seconds after URL emission, but system DNS recovered only at
81.77/81.72 seconds. Unauthenticated HTTPS returned HTTP 200 `image/jpeg` and
the exact canonical bytes at 82.03/82.01 seconds; unchanged `_verify()` passed.
URL emission itself took 5.10/4.17 seconds after subprocess launch.
Deferring the first system lookup until public DNS resolved in a separate
diagnostic reached exact-byte HTTPS at 1.53 seconds after emission.

These observations support an initial negative DNS answer remaining cached beyond
the old startup budget; the particular cache layer was not identified. AED-045
raises the default to the existing 120-second maximum to cover the observed
86–87-second total startup with headroom. It retains bounded retries, the
15-second fetch timeout and all verification/security checks. DNS-over-HTTPS
was diagnostic only; production still uses standard system resolution.
Tunnel processes, origin listeners and temporary configuration were cleaned up.
No account/login/token/domain setup, sensitive logging or Instagram request
was involved. Earlier malformed-hostname and stale-tunnel timing measurements
are excluded; a stopped manual origin's 502 was not a staging defect.

### AED-046 follow-up — 20 October 2026

AED-045 merged into `e98fe1c298ce86b2b6b14aa45ef5a33d8511f0f1`, with green
integrated CI run 39433518348 and 693 passing tests. The real command still
exhausted 120 seconds. Tested diagnostic-only instrumentation reproduced this:
788 attempts, URL discovery at 4.366 seconds, initial `gaierror` errno 8, then
HTTP 530 through the final failure at 120.011 seconds. The original suppressed
exceptions concealed the change from DNS failure to an unsuccessful HTTP status.

A second production-path run with temporary observers counted 645 DNS failures,
then 140 HTTP 530 responses, transitioning at 79.831 seconds. For that same fresh
hostname, system `dig`, direct `dig @1.1.1.1` and Cloudflare DNS-over-HTTPS had A
records by the 26.928-second observation; Python and curl still failed resolution
until roughly 80 seconds. Both clients then received HTTP 530. This places the
resolution lag in the OS resolver path rather than Python alone; no cache daemon
was conclusively identified. DNS recovered within 120 seconds and was not the
remaining blocker. No resolver override, cache flush or system setting change
was made.

A further reproduction confirmed the captured URL matched the provider banner.
The origin thread remained alive and its exact route returned HTTP 200 with the
canonical bytes throughout. The tunnel process remained alive, but provider
output inspected only in memory showed six QUIC dial timeouts and zero registered
connections before the deadline. Process liveness did not establish tunnel
connectivity. Automatic transport selection had not produced a working connection
within the startup budget; increasing the DNS allowance could not fix that.

A controlled diagnostic changed only the subprocess transport to `http2`.
It registered one connection with no QUIC dial failures. After 641 initial DNS
failures, unchanged `_verify()` accepted HTTP 200, `image/jpeg`, advertised length
and exact bytes at 78.805 seconds. Origin/tunnel cleanup completed. This supports
explicit HTTP/2 as the narrow correction for the observed QUIC transport failure;
it does not identify which network device caused the UDP timeouts. Cloudflare
documents [transport selection][transport] and [connection troubleshooting][troubleshooting].
HTTP/2 retains TLS but does not support QUIC's post-quantum key agreement.

The fix preserves the 120-second startup budget, 15-second fetch timeout, retry
cadence, standard system DNS, no proxies/auth/cookies/redirects, loopback-only
origin, secret exact route, JPEG validation, bounded reads, exact-byte verification,
isolated subprocess environment and process-group cleanup. Deterministic tests
pin the transport argument and reproduce DNS recovery followed by persistent 530
without accepting an unverified tunnel. Classifier and log tests cover unsafe
exception text and bounded summary output. No Instagram request occurred.

**Full real-command live acceptance remains pending.** Diagnostic success does
not complete AED-041. After AED-046 review/merge and green integrated-main CI,
rerun the production command, independently fetch its HTTPS URL without credentials,
compare exact bytes, close staging, and confirm the old URL no longer serves the
asset. Do not leave a background process or record its URL in logs/PRs/Kanban.

[transport]: https://developers.cloudflare.com/tunnel/reference/run-parameters/#protocol
[troubleshooting]: https://developers.cloudflare.com/tunnel/troubleshooting/

AED-041 stays Blocked, Completed blank, pending AED-046 and full live acceptance.
Any subsequent evidence PR must also be merged with green integrated-main CI
before completion. AED-022 remains Blocked on public staging and owner
account/app/scoped-token/identity/explicit-post authorization. AED-023 remains
Backlog. No OAuth, posting, scheduler, retry/reconciliation, permanent hosting,
secret storage or downstream ticket is implemented here.

[quick]: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/
[install]: https://developers.cloudflare.com/tunnel/downloads/
[publishing]: https://developers.facebook.com/docs/instagram-platform/content-publishing/
[media]: https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media/
[versions]: https://developers.facebook.com/docs/graph-api/changelog/
