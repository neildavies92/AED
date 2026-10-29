# Instagram API client (AED-022)

`ask_every_day.instagram_client.InstagramClient` implements the Instagram Login
single-image primitives. It does not publish automatically. See
[account and publishing prerequisites](instagram-publishing.md) for the setup
research and source links.

## Version and evidence

Rechecked official Meta documentation on **22 October 2026** during AED-049.
The documentation reader returned login/429 responses, but direct public HTTPS
retrieval of the [Graph changelog](https://developers.facebook.com/docs/graph-api/changelog/)
and [Instagram Login get-started guide](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started/)
succeeded. The changelog identifies **v26.0** as current, so the existing version
is retained. The [authentication evidence](instagram-auth.md) records the verified
OAuth contract and remaining callback/app-access uncertainty. Publishing live
acceptance remains separate; no third-party tutorial supplies endpoint assumptions.

The fixed base is `https://graph.instagram.com/v26.0`. Use an Instagram User token
with `instagram_business_basic` and `instagram_business_content_publish`, passed
explicitly as `InstagramClient(access_token, timeout=15.0)`. Construction makes no
request. The token is only sent in the bearer Authorization header; it is absent
from query strings, result values, reprs and diagnostics. This module neither
loads configuration/environment variables nor persists credentials.

The [authentication layer](instagram-auth.md) now provides account-bound credential
metadata, secure local storage and expiry/refresh handling. Its
`CredentialManager.client()` supplies a validated bearer token to this client;
the publishing primitives retain their existing responsibilities. Browser login
is still blocked on a verified callback route, so normal operator connection has
not been accepted and no automatic publishing flow is enabled.

## Independent operations

| Method | Request | Result |
| --- | --- | --- |
| `identify_account()` | GET `/me`, fields `user_id,username,account_type` | Frozen `InstagramIdentity` |
| `publishing_quota(account_id)` | GET `/{id}/content_publishing_limit`, fields `quota_usage,config` | Frozen `PublishingQuota(usage,total,duration)` |
| `create_image_container(account_id,image_url,caption=None,alt_text=None)` | POST `/{id}/media`, URL-encoded form | Frozen `Container(id)` |
| `container_status(container_id)` | GET `/{id}`, fields `status_code,status` | `ContainerStatus` enum |
| `publish_container(account_id,container_id)` | POST `/{id}/media_publish`, `creation_id` form field | Frozen `PublishedMedia(id)` |

IDs are preserved as strings, including leading zeros; only nonempty ASCII digit
strings are accepted. Container and published-media identifiers have different
result types. Known states are IN_PROGRESS, FINISHED, PUBLISHED, ERROR and EXPIRED;
unknown states fail closed. Provider status prose is discarded. Identity supports
a single object or a single-row `data` envelope. Quota requires one `data` row and
the documented `config.quota_total`/`quota_duration` fields. It never substitutes
50 or 100: the older `rate_limit_settings` example discrepancy fails safely until
verified against an actual response.

Optional copy is omitted only for `None`; empty strings are sent deliberately.
The URL must be HTTPS with a host and without embedded credentials, whitespace,
control characters or fragments. This is syntax validation, not proof of public
access, image validity or compliance. The client never fetches that URL, reads
images, uploads bytes, starts a server or creates a tunnel. Supply an externally
verified public unauthenticated HTTPS JPEG URL from a separate hosting step.

## Transport, failures and recovery boundary

The standard-library `urllib` transport has no additional dependencies. Inject a
callable via `transport=` accepting `(method, url, headers, body, timeout)` and
returning `(HTTP_status, response_bytes)` for isolated testing. The production
transport rejects redirects (preventing bearer forwarding), reads at most 1 MiB
plus an overflow sentinel, and closes responses, including HTTP errors.

The explicit timeout defaults to 15 seconds and must be finite, positive and at
most 60 seconds. It is passed to urllib for socket operations; it is not a total
wall-clock deadline across DNS resolution and repeated reads. System DNS timeouts
remain operating-system controlled. Injected transports must honor their timeout
contract. There are no retry loops, polling, sleeps or replacement containers.

Invalid caller arguments raise sanitized `ValueError`. `TransportError` means no
trustworthy response; `APIError` means non-2xx HTTP or an API error envelope;
`ResponseError` means invalid JSON/schema or an unknown state. Each exposes only
operation, HTTP status and `outcome_uncertain`. Every failed POST is conservatively
marked uncertain: **never infer that no side effect happened**. Raw provider data
and original exception chains are discarded because either could contain secrets.
An uncertain create/publish requires later reconciliation (AED-024), never blind
POST replay. This client intentionally has no combined create-and-publish method,
media-list reconciliation, orchestration, retry/idempotency policy or persistence.

Operational logs use the existing structured logger: event, operation, HTTP
status and safe exception class. The sole formatter extension is integer
`http_status`. Tokens, IDs, URLs, captions, alt text and provider bodies are never
logged. The client does not configure handlers.

## Validation and outstanding live acceptance

`tests/test_instagram_client.py` uses only fake transports or fake urllib openers;
a socket guard prohibits network access in these tests. It covers exact requests,
Unicode/form encoding, optional copy, URL/ID/timeout validation, provider quota,
all states, malformed data, HTTP/auth/rate-limit/server failures, network failures,
no retries, response closure, redirects and safe logs/errors. Run the focused file
with `.venv/bin/pytest tests/test_instagram_client.py`, then canonical `make setup`
and `make check`.

**No Instagram request or real post was made.** A green implementation PR does not
complete live acceptance. AED-022 remains **Blocked**, Completed empty; AED-023
stays Backlog. Remaining prerequisites are:

- Confirm a professional/public Instagram account.
- Configure the Meta app and account role.
- Make a token with the two scopes available securely.
- Confirm the intended account identity.
- Provide a public unauthenticated HTTPS JPEG URL.
- Obtain explicit authorization for one controlled real post.

OAuth, secret storage, hosting/tunnels/S3, publish-next, scheduling and Insights
remain outside this client.


Production media POSTs require Docker at both the client and HTTP transport
boundaries. Native callers receive `docker_publisher_required` before a request;
GET-only inspection and owner-approved credential administration remain available.
