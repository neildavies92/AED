# Instagram authentication (AED-049)

AED owns OAuth, credential storage and lifecycle directly against Meta. It never
collects the Instagram password. `InstagramClient` remains responsible for Graph
identity and publishing primitives; no publishing service or middleware is involved.

## Implemented, configured and accepted

`make instagram-login` implements browser authorization, a local HTTPS callback,
code/long-lived exchanges, exact professional identity verification and Keychain
persistence. It never creates media or publishes a post.

On 22 October 2026 the owner confirmed that Meta saved this exact redirect URI:

```text
https://localhost:8765/oauth/instagram/callback
```

Dashboard location: **Instagram → API setup with Instagram login → Set up Instagram
business login → Business login settings → Valid OAuth Redirect URIs**. The saved
scheme, hostname, port, path and absence of a trailing slash must match AED's
configuration byte-for-byte. Only this form is supported; arbitrary HTTPS and all
HTTP redirects are rejected. The earlier HTTP `127.0.0.1` candidate was rejected
with a generic validation error; no general localhost prohibition is inferred.

The owner also confirms professional account setup and an accepted `@ask.every.day`
Instagram tester invitation. Business Login redirect registration is configured.
On 26 October, operator-provisioned TLS passed the native trust preflight and a
fresh login using the AED-050 response-parser fix completed code/long-lived
exchange, verified exact `@ask.every.day` Creator identity and saved the credential
in Keychain. Offline status then reported connected and locally valid. No raw
response, token, code or provider/account ID was recorded in the repository.

AED-049 is owner-confirmed Done. AED-050 remains In Progress pending review/merge
and integrated-main verification. AED-022 still requires controlled image
publication with explicit operator approval; AED-023/024/027/028 remain Backlog.
Webhooks are not required; App Review is deferred for tester acceptance.

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
On 26 October a fresh live diagnostic returned HTTP 200 with the grant at the
root, an integer `user_id` and list-valued `permissions`. Only field types were
recorded; the response and short-lived token were discarded without persistence.
The code-exchange parser accepts both envelopes, converts positive integer IDs
to strings and validates granted scope lists or comma-separated strings. It
requires both publishing scopes; it never substitutes requested scopes for a
missing grant. Ambiguous envelopes, invalid IDs/scopes and error payloads fail
closed without retrying the single-use code or logging response values.
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
publishing an app. The owner confirms that `@ask.every.day` is an added Instagram
tester and has accepted its invitation. This resolves the tester/role setup
blocker; it does not prove that live authorization has succeeded.

App Review is deferred and is not a PR #39 merge gate for this accepted tester in
development mode. Review/Advanced Access is a later concern for accounts outside
the owner/app-role/tester boundary. If Meta actually refuses this tester flow
without review, record that evidence and stop; do not guess or submit a review.

## Meta app setup and local configuration

1. In Meta for Developers, create/select the business app, add the Instagram
   product and use **API setup with Instagram login**. The current setup guide
   describes Other → Business when creating an app.
2. The professional `@ask.every.day` account has been added as an Instagram tester
   and its invitation accepted, as confirmed by the owner. Keep runtime Instagram
   login separate from developer admin login.
3. Instagram Business Login is required. Its exact HTTPS localhost redirect above
   has been saved, as confirmed by the owner. Keep it registered unchanged and
   request only the two scopes above.
4. Obtain the **Instagram App ID** and **Instagram App Secret** from the Instagram
   product's Business Login settings. These are the Instagram-specific credentials;
   do not silently substitute another product's app ID/secret.
5. Review the setup form's deauthorization/data-deletion URL and review requirements
   with the operator. Do not point those at an unimplemented endpoint or subscribe
   to unrelated messaging/comment webhooks merely to fill out the form.

**Configure webhooks** is not required for AED's current authentication/static-image
publishing scope and remains unconfigured. An OAuth redirect URI is not a webhook
callback URL. No event subscriptions, verify token or webhook receiver are needed.

Copy `instagram-auth.example.toml` to ignored `instagram-auth.toml`; replace its
fake App ID and TLS paths. Retain the exact registered redirect URI. Expected
username is fixed to `ask.every.day`. No operator app/account/provider IDs are
committed. Unknown settings, including attempted secret fields, are rejected with
fixed errors. Bootstrap/status/logout can run without TLS paths; login requires
both certificate and private-key paths.

The App Secret is never an argument, environment dump, config value or chat input.
On macOS, `make instagram-auth-bootstrap` prompts for it using `getpass` only from
an interactive terminal and stores it in Keychain. A noninteractive invocation
fails before reading input. The operator has completed this bootstrap locally;
the App Secret was not provided through chat or configuration. Configure trusted
local HTTPS before bootstrapping secrets.

## Local HTTPS certificate

AED uses stdlib `ssl` and operator-provisioned PEM files, not a public callback
service or tunnel. TLS certificate/private-key files are separate from the
Keychain App Secret/token items. Keep them in a private directory outside every
Git checkout, owned by the current user, directory mode `0700`, file mode `0600`.
Symlink files, shared permissions, combined certificate/key files, missing or
oversized files and mismatched keys fail closed. No material belongs in Git.

Apple's [Certificate Assistant][certificates] can provision local certificates.
An already installed OpenSSL is another operator provisioning tool; it is not an
AED runtime dependency. No mkcert, package, CA or trust installation is automated.
For a dedicated localhost leaf, use an RSA key of at least 2048 bits, a short
lifetime, `subjectAltName=DNS:localhost`, `CA:FALSE`, digital-signature/key-encipherment
key usage and `serverAuth` extended key usage. The leaf does not need a local CA.
For example, after reviewing this setup, an operator with OpenSSL can run:

```sh
umask 077
mkdir -p "$HOME/Library/Application Support/Ask Every Day/tls"
chmod 700 "$HOME/Library/Application Support/Ask Every Day/tls"
openssl req -x509 -newkey rsa:2048 -noenc -days 30 \
  -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost' \
  -addext 'basicConstraints=critical,CA:FALSE' \
  -addext 'keyUsage=critical,digitalSignature,keyEncipherment' \
  -addext 'extendedKeyUsage=serverAuth' \
  -keyout "$HOME/Library/Application Support/Ask Every Day/tls/localhost-key.pem" \
  -out "$HOME/Library/Application Support/Ask Every Day/tls/localhost-cert.pem"
chmod 600 "$HOME/Library/Application Support/Ask Every Day/tls/"*.pem
```

Do not overwrite an existing key/certificate without first deciding to rotate it.
Creating this self-signed leaf does **not** make it trusted. Review its identity
and fingerprint before deliberately adding user-local trust. macOS's built-in
`security add-trusted-cert` supports SSL policy and hostname constraints; trust
only this leaf for `localhost`, without administrator/system trust or a new CA:

```sh
security add-trusted-cert -r trustRoot -p ssl -s localhost \
  -k "$HOME/Library/Keychains/login.keychain-db" \
  "$HOME/Library/Application Support/Ask Every Day/tls/localhost-cert.pem"
security verify-cert -p ssl -n localhost -L -R offline -q \
  -c "$HOME/Library/Application Support/Ask Every Day/tls/localhost-cert.pem"
```

This is an explicit operator trust decision, never part of `make setup` or login.
Respect OS approval prompts. No `allowedError`, global verification override,
browser warning bypass or broad CA trust is used. Remove the certificate's trust
in Keychain Access when retiring it; renew/review trust on expiry or rotation.
If this policy cannot be established safely, stop; do not replace it with a
public service or weaken TLS.

Before launching Instagram, AED checks file ownership/permissions, matching TLS
material and macOS SSL server trust for `localhost` using a five-second, local-only
`security verify-cert` invocation. Only the public certificate path is passed to
that command; no private key or OAuth secret is an argument. TLS 1.2 is the minimum.
This uses macOS trust; confirm the selected system browser honors that trust.
If its local callback shows a certificate warning, stop without bypassing it.
The operator's existing certificate passed native preflight and the HTTPS callback
completed during the 26 October live login. AED installed no certificate or trust.

## Callback and login flow

The listener binds only `127.0.0.1:8765`; the browser still uses the registered
`https://localhost` hostname. It accepts GET on `/oauth/instagram/callback` with
the exact local Host header. Other paths return 404 and mutation methods return
405. It serves no directories, webhooks or dynamic provider content. The callback
page is static, sends no-store/no-referrer headers and never echoes query values.

Each receiver permits one attempt, at most five minutes and 32 connections. TLS
handshake/request/response handling shares a two-second deadline per connection,
bounded by the overall deadline. Request lines are limited to 8300 bytes, the
header block to 16 KiB and 32 headers, and individual headers to 8192 bytes.
Ambiguous Host headers, request bodies, transfer encoding and malformed framing
are rejected. The raw query goes directly to `AuthorizationAttempt.consume()`;
only that existing boundary parses OAuth state/code and enforces its 8192-character
query limit. No duplicated state validator exists in the transport.

The synchronous receiver has no handler threads. Success, denial, malformed OAuth,
timeout and Ctrl+C close sockets and consume/cancel the attempt. Duplicate or
pipelined callbacks cannot trigger another exchange. The listening socket closes
before token exchange. Context cleanup also runs if browser startup, token
exchange, identity verification or storage fails.

Login loads local settings and requires the Keychain App Secret before creating
an attempt. It starts the trusted receiver before handing the private authorization
URL to stdlib `webbrowser`. After state validation it exchanges code → short-lived
token → long-lived token, requires exact `ask.every.day` and a supported professional
account type, then saves once to Keychain. Failures never persist an unverified
credential or delete a previous credential. No network operation is retried.

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
identity verification before its first credential write. The HTTPS receiver
consumes its `AuthorizationAttempt` first; this API is not a manual code-paste
workflow. State has 256 bits of entropy, a five-minute lifetime,
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
| `make instagram-login` | Browser authorization through trusted local HTTPS; verify identity and store |
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
uv run pytest tests/test_instagram_auth.py tests/test_instagram_callback.py tests/test_instagram_login.py tests/test_credential_store.py tests/test_instagram_auth_cli.py tests/test_instagram_client.py
make setup
make check
```

Tests inject the browser, receiver, Keychain backend, provider transport, identity
lookup and clocks. Callback integration uses ephemeral loopback test ports and
fresh synthetic TLS files generated in private temporary directories by OpenSSL
(available on the macOS developer host and Ubuntu CI). The test client trusts
only that synthetic certificate; tests never modify OS trust, contact Meta, open
a browser or use real Keychain credentials.

**Safe OAuth/identity acceptance completed on 26 October using the AED-050 fix;
no media container or publish request occurred.** After TLS/config/Keychain setup,
run `make instagram-login`, complete browser authorization as the accepted tester,
then `make instagram-auth-status`. Safe acceptance is authorize → exchange →
verify `@ask.every.day` → store → status. Authorization codes are single-use: a
failed exchange requires a fresh login, never replaying its callback or code.
Do not share the authorization URL, callback query, code, state or secrets in logs
or chat. Media creation/publishing remains behind separate AED-022 authorization.

[versions]: https://developers.facebook.com/docs/graph-api/changelog/
[login]: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login/
[started]: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started/
[setup]: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/create-a-meta-app-with-instagram/
[keychain]: https://developer.apple.com/documentation/security/secitemadd(_:_:)
[certificates]: https://support.apple.com/en-gb/guide/keychain-access/kyca8916/mac
