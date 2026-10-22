# Instagram authentication (AED-049)

AED owns OAuth, credential storage and lifecycle directly against Meta. It never
collects the Instagram password. `InstagramClient` remains responsible for Graph
identity and publishing primitives; no publishing service or middleware is involved.

## Implementation boundary and callback blocker

**Browser login is not implemented or accepted yet.** On 22 October 2026 the
operator tried registering `http://127.0.0.1:8765/oauth/instagram/callback` in
Instagram Business Login setup. Meta returned “Error saving redirect URIs. Verify
your redirect URIs and try again.” This generic message does not prove whether
HTTP, the loopback address or another validation rule caused rejection.

The retrieved official documentation requires an exact registered redirect URI
but did not establish a supported loopback HTTP exception. Do not assume a plain
HTTP callback is accepted. No callback server, browser authorization, tunnel,
permanent callback service, manual code-paste fallback or TLS bypass is added.
`make instagram-login` exits with this blocker before reading secrets or starting
authorization. This target deliberately does not claim a working login flow.

**Next operator/design prerequisite:** establish an accepted redirect URI using
the authenticated dashboard and Meta's applicable redirect requirements, including
scheme, host, path, trailing slash and any reachability/certificate restrictions.
Then agree how that redirect securely reaches AED. If HTTPS is required, choose
the smallest supported HTTPS callback architecture before implementing a receiver;
do not silently substitute a public service or self-signed-certificate bypass.
No live login acceptance is possible until that decision and local app setup.

The reviewed foundations can be tested independently: OAuth URL/state processing,
token exchanges, strict credential metadata, account binding, refresh, Keychain
storage, local status and removal. They are preparation for the remaining callback
and browser work, not completion of AED-049. AED-049 remains Blocked on callback
verification/configuration; AED-022 remains Blocked on AED-049. AED-023/024/028
remain Backlog. Review/merge, integrated-main CI and safe login acceptance are also
required before AED-049 can be Done.

## Provider evidence — 22 October 2026

The automated documentation reader returned login/429 responses. Direct public
HTTPS retrieval succeeded for the same official Meta pages; their embedded
documentation was read without authenticating or supplying credentials.

The [Graph changelog][versions] identifies **v26.0**, released 29 July 2026, as the
latest version. The [Instagram get-started guide][started] also renders v26.0 and
describes professional-account identity. The existing client version is retained.

The [Business Login guide][login] confirms the following operations:

| Operation | Provider request |
| --- | --- |
| Authorize | `https://www.instagram.com/oauth/authorize`, response type `code` |
| Code exchange | POST `https://api.instagram.com/oauth/access_token` |
| Long-lived exchange | GET `https://graph.instagram.com/access_token`, `ig_exchange_token` |
| Refresh | GET `https://graph.instagram.com/refresh_access_token`, `ig_refresh_token` |

Request only `instagram_business_basic` and
`instagram_business_content_publish`. The current code-exchange example uses one
`data` row containing `access_token`, `user_id` and comma-separated `permissions`.
The client requires both permissions and rejects ambiguous/malformed responses.
It sends multipart form fields as shown by Meta. Authorization codes are single-use
and last one hour; short-lived tokens last one hour. The long-lived exchange and
refresh return `token_type` and `expires_in`; persist those actual lifetimes,
bounded to the documented 60 days. There is no separate refresh token.

Meta permits refresh only for an unexpired long-lived token at least 24 hours old
with the basic permission. The auth requests use the documented query parameters
for the two GET operations, including secret-bearing values. These private
request URLs must never be printed, persisted or sent to browser/history/logs.
The publishing client continues to use bearer-header authentication.

The Business Login guide permits Standard Access for owned/managed professional
accounts added to the dashboard; other accounts need Advanced Access. The
[app-setup guide][setup] also describes App Review and business verification for
publishing an app. The owner's actual tester/account assignments, access level,
review eligibility and granted permissions still require dashboard confirmation.
No own-account review exemption is asserted as a live result.

## Meta app setup and local configuration

1. In Meta for Developers, create/select the business app, add the Instagram
   product and use **API setup with Instagram login**. The current setup guide
   describes Other → Business when creating an app.
2. Add the public professional `@ask.every.day` account in the Instagram setup;
   complete its login/role authorization and any tester invitation required by
   the dashboard. Keep runtime Instagram login separate from developer admin login.
3. In **Set up Instagram business login → Business login settings**, establish
   the accepted redirect route described above and request the two scopes.
   Check the exact saved URI, including any trailing slash added by the dashboard.
4. Obtain the **Instagram App ID** and **Instagram App Secret** from the Instagram
   product's Business Login settings. These are the Instagram-specific credentials;
   do not silently substitute another product's app ID/secret.
5. Review the setup form's deauthorization/data-deletion URL and review requirements
   with the operator. Do not point those at an unimplemented endpoint or subscribe
   to unrelated messaging/comment webhooks merely to fill out the form.

`instagram-auth.example.toml` contains non-secret placeholders. After the callback
decision, copy it to ignored `instagram-auth.toml` and fill in the App ID and exact
registered HTTPS redirect URI. The current configuration intentionally rejects
HTTP until a supported exception is established. Expected username is fixed to
`ask.every.day`. No account/app/provider IDs from the operator are committed.
Unknown settings, including attempted secret fields, are rejected with fixed errors.

The App Secret is never an argument, environment dump, config value or chat input.
On macOS, `make instagram-auth-bootstrap` prompts for it using `getpass` only from
an interactive terminal and stores it in Keychain. A noninteractive invocation
fails before reading input. No real App Secret has been supplied or stored during
implementation. There is no reason to bootstrap secrets before callback setup.

## Secure persistence

`CredentialStore` provides load/save/delete operations; tests inject memory stores.
`KeychainCredentialStore` uses native Security.framework `SecItemCopyMatching`,
`SecItemUpdate`, `SecItemAdd` and `SecItemDelete` through stdlib `ctypes`. Apple's
[Keychain API][keychain] encrypts generic-password item data. It uses the user's
default local Keychain and an app-specific `com.ask-every-day.instagram.oauth.`
service namespace. Credential and App Secret are separate items. Updates replace
an item atomically; a concurrent add collision gets one bounded update attempt.

No secret is passed in a subprocess command line, and no subprocess, shell,
plaintext project credential, SQLite credential row or new dependency is needed.
Unsupported platforms, locked/denied Keychain access, corrupted data and oversized
items fail closed. Normal CI uses fake backends and does not access a real Keychain.
A unique synthetic entry was independently read, written, updated and deleted on
the macOS operator host; it was removed after the check.

This protects secrets at rest; it does not isolate secrets from every process
running as the same OS user or from the Python process holding them in memory.
The local operator owns the Meta app and its confidential secret. Meta documents
long-lived exchange as a server-side secret operation; this private operator
process must not be distributed as a public desktop client embedding an app secret.
OS access prompts/Keychain policy must be respected, never bypassed.

## Lifecycle and integration

`InstagramCredential` hides tokens and account IDs from its representation and
records app ID, authorization-scoped ID, professional identity ID, username,
permissions, bearer type and aware issue/expiry timestamps. Meta describes the
authorization-scoped ID separately from `/me`'s professional ID; they are retained
separately rather than assumed equal. Identity must be `ask.every.day` and have
a documented professional account type before persistence. Refresh must retain
the same professional account ID. A different app cannot reuse the credential.

`CredentialManager.connect()` performs code exchange, long-lived exchange and
identity verification before its first credential write. A future verified
receiver must consume its `AuthorizationAttempt` first; this API is not a manual
code-paste workflow. State has 256 bits of entropy, a five-minute lifetime,
constant-time comparison, bounded query parsing and one-use invalidation on both
success and failure. Unknown/duplicate/denied/missing/mismatched callbacks fail
closed without exposing query values.

`get_valid_credential()` rejects missing, corrupt, future-issued, expired,
wrong-app or wrong-account credentials. Outside the final seven days it verifies
identity and returns the current token. Inside that safety margin it refreshes
only when the 24-hour eligibility condition holds, rechecks identity, then
persists the new lifetime. Failed refresh/identity/storage never falls back to
the old token for that call. A subsequent call uses the refreshed expiry, so it
does not refresh on every call. There is no background scheduler.

`CredentialManager.client()` supplies the validated bearer token to the existing
`InstagramClient`; it does not create media or publish. OAuth HTTP responses are
bounded to 64 KiB plus an overflow sentinel, use a 15-second socket timeout and
disable redirects and environment proxies. There are no automatic network retries;
failed code-exchange POSTs are marked uncertain and require a fresh authorization
flow rather than automatic replay. Socket timeouts do not bound OS DNS resolution.

## Operator commands and acceptance

| Command | Behavior |
| --- | --- |
| `make instagram-login` | Fails with the unresolved callback prerequisite; no authorization starts |
| `make instagram-auth-bootstrap` | Hidden interactive App Secret entry into macOS Keychain |
| `make instagram-auth-status` | Safe local credential metadata; no network or refresh |
| `make instagram-logout` | Removes local credential and App Secret; no provider revocation |

Underlying commands accept `--config /path/to/instagram-auth.toml` through
`uv run python -m ask_every_day.instagram_auth_cli <command>`. Status
`credential_valid` means locally valid metadata/time, not proof of provider validity
or continued permissions; manager access performs the online identity check.
Local logout does not revoke Meta's grant. No unverified revoke endpoint is used.

Structured events contain fixed operation/status/error metadata only. Tokens,
codes, state, App Secret, callback queries, authorization URLs, headers and provider
bodies are excluded from logs, errors and public representations. Audit tests
use unmistakably synthetic fixtures and assert these redaction boundaries.

Focused checks:

```sh
uv run pytest tests/test_instagram_auth.py tests/test_credential_store.py tests/test_instagram_auth_cli.py tests/test_instagram_client.py
make setup
make check
```

**No live OAuth request, identity verification or media publish request occurred.**
After a receiver is implemented against a verified redirect contract, safe live
acceptance is authorize → exchange → verify `@ask.every.day` → store → status.
It must also verify listener cleanup, timeout and duplicate callback handling.
Media container creation/publishing stays behind the separate AED-022 approval.

[versions]: https://developers.facebook.com/docs/graph-api/changelog/
[login]: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login/
[started]: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started/
[setup]: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/create-a-meta-app-with-instagram/
[keychain]: https://developer.apple.com/documentation/security/secitemadd(_:_:)
