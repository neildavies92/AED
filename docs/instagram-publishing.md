# Instagram publishing prerequisites (AED-021)

Researched **13 October 2026** against official Meta documentation. **VERIFIED**
means documented by Meta, not tested against `@ask.every.day`; **DECISION** is our
proposed approach; **OPEN** needs confirmation. No account setup or publishing
request was performed.

**DECISION:** use **Instagram API with Instagram Login** and versioned base
`https://graph.instagram.com/v26.0`. Meta's changelog identifies v26.0, released
29 July 2026, as current. Recheck before implementing and record the version used
in the test. Authentication endpoints below are deliberately unversioned.
[Graph API changelog][versions]

## Account and app requirements

**VERIFIED:** Instagram Login supports professional **Business or Creator**
accounts without a linked Facebook Page. Personal accounts are unsuitable.
Facebook Login is a separate path requiring a Page: do not mix its host, tokens
or scope names with Instagram Login. A business portfolio is not required by
the Instagram login flow itself. [Platform overview][overview]

**DECISION:** start with an owned/managed professional account using **Standard
Access**. Meta says this suffices for an account you own/manage and have added to
the app dashboard. App-role users are the development/test audience. Serving
other accounts requires **Advanced Access**, App Review and Business Verification.
[Access levels][overview]

**VERIFIED setup:** register as a Meta developer; create a **Business** app (the
published creation flow uses Other → Business); add the **Instagram** product
and **API setup with Instagram login**. Add the public professional test account
and complete its login/role authorization. The dashboard can generate a token
before OAuth is implemented. Later Business Login configuration needs exact OAuth
redirect URIs, deauthorization and data-deletion callbacks; obtain the **Instagram
App ID/Secret** from that product's settings. [App setup][appsetup]

**OPEN — review qualification:** the setup guide also describes App Review before
live-data access and a verified business before publishing the app. The specific
access-level guide permits owned-account Standard Access. Follow the dashboard
test path and confirm actual access/review requirements before unattended
production. Publishing a test post is not launching an app publicly. General
setup documentation includes webhooks, but the publishing guide permits status
polling; this test does not introduce a messaging webhook server.
[Setup][appsetup], [access levels][overview], [publishing][publishing]

The publishing-only scopes are exactly:

| Scope | Purpose |
| --- | --- |
| `instagram_business_basic` | Authorized account access |
| `instagram_business_content_publish` | Container creation, status and publishing |

Do not request comments, messaging, Page or ads scopes for this test.
[Publishing requirements][publishing]

## Authentication and local secrets

**VERIFIED:** use an **Instagram User access token** authorized by the professional
account for this app. Dashboard tokens last 60 days; OAuth tokens initially last
one hour. Identify the account with `GET /me?fields=user_id,username,account_type`.
Use the returned professional `user_id` as `IG_ID`, not a handle or Page ID.
[Get started][started]

For eventual OAuth, authorize through `https://www.instagram.com/oauth/authorize`.
Exchange its single-use code at `POST https://api.instagram.com/oauth/access_token`
with `client_id`, `client_secret`, `redirect_uri`, `code` and
`grant_type=authorization_code`. Exchange an unexpired short-lived token at
`GET https://graph.instagram.com/access_token` with `grant_type=ig_exchange_token`,
`client_secret` and `access_token`; the result lasts 60 days. Refresh at
`GET https://graph.instagram.com/refresh_access_token` with
`grant_type=ig_refresh_token` and `access_token`. The long-lived token must be
unexpired, at least 24 hours old, and have `instagram_business_basic`. Expired
tokens cannot be refreshed. Retain returned `expires_in`; do not assume permanent
validity. [Business Login][login]

**DECISION:** later secret storage must protect the app secret, tokens and temporary
authorization codes. Store expiry and authorized account context separately.
Never commit these secrets or expose them in logs, recorded request URLs or PRs.
The existing `aed.toml` loader is not a secret manager. No credentials were
obtained by this ticket.

## Minimal single-image sequence

Use the versioned Instagram base and authorized bearer token:

| Step | Request and result |
| --- | --- |
| Identify | `GET /me?fields=user_id,username,account_type`; verify the intended account |
| Check quota | `GET /{IG_ID}/content_publishing_limit?fields=quota_usage,config` |
| Create | `POST /{IG_ID}/media` with `image_url`; optional `caption`, `alt_text`; retain returned container `id` |
| Check readiness | `GET /{CONTAINER_ID}?fields=status_code,status`; wait for `FINISHED` |
| Publish | `POST /{IG_ID}/media_publish` with `creation_id={CONTAINER_ID}`; retain returned **media** `id` |
| Verify | Read `GET /{IG_ID}/media` and inspect the published post in Instagram |

Single photos need no video/carousel `media_type`. Publishing creates a real post,
not a dry run. Checking readiness even for photos is our conservative choice;
Meta's guide specifically discusses waiting for video processing. Container IDs
and published-media IDs are separate identities. [Publishing][publishing],
[container reference][container], [account/media reads][started]

## Image and media-access contract

**VERIFIED:** JPEG only (not PNG, SVG, MPO or JPS), maximum 8 MB, aspect ratio from
4:5 through 1.91:1, width 320–1440 pixels, sRGB. **1080×1350 = 4:5** fits. The shared
publishing guide links this media reference, whose examples use Facebook Login;
apply its image specifications without copying that login's scopes/host.
[Media reference][media]

**DECISION:** SVG remains a rendering intermediate. A future PNG export alone is
insufficient: plan a JPEG conversion/export before live integration. Flatten onto
the intended background and verify colour/phone legibility; renaming `.png` to
`.jpg` does not convert it.

**VERIFIED:** Meta fetches `image_url` from a public server. Local paths, `file:`
URLs, localhost and authenticated browser previews cannot satisfy this contract.
The documented resumable binary upload is for **video**, not this static-image
Instagram Login flow. [Publishing guide][publishing]

**DECISION:** the smallest first-test arrangement is a local server exposing a
dedicated directory containing only the test JPEG through a temporary public
**HTTPS tunnel**. Confirm an unauthenticated external GET returns image bytes,
without a login, warning page or browser challenge. Keep the server, machine and
tunnel available through processing/verification, then close them. This follows
Meta's public-URL contract; it is not a claim that Meta endorses a particular
tunnel provider. A short-lived public object host is another option. No hosting
is implemented or AWS service required.

## First controlled test and outstanding owner actions

1. **Owner:** verify the account is professional and public. Choose a dedicated
   test account or authorize a visible test on `@ask.every.day`.
2. **Owner:** configure the Business app/account role and obtain a dashboard token
   with the two publishing scopes. Verify account identity and access; record
   nonsecret IDs, access level and expiry, never the token in the repository.
3. **Developer:** prepare the compliant JPEG/public URL. Record version,
   dimensions/size and a distinguishable test caption.
4. Execute identity → quota → create → status → publish once. Retain the
   container ID before publishing and the returned media ID afterwards. Confirm
   the account, image and caption in Instagram and its media listing.
5. Record redacted outcomes/errors. Closing the tunnel does not remove the post;
   cleanup of a real Instagram post is a separate owner action.

**OPEN:** account type/public status, developer app, roles/access, tokens and tunnel
availability have not been inspected. These gate **live testing**, not mocked
implementation. **AED-022 can be Ready for client implementation with mocked
tests**; real-account acceptance must await those gates. If that ticket requires
immediate live completion, record the live portion as blocked on owner setup.
No successful live test is claimed here.

## Limits and recovery implications

**VERIFIED ambiguity:** the publishing guide says **100 API posts per moving
24 hours**, while its carousel section and the quota reference say **50**. Query
`quota_usage` and `config.quota_total`/`quota_duration`; do not hard-code either
number as a guaranteed quota. The quota reference's example uses
`rate_limit_settings` while its field table documents `config`; confirm actual
responses in the first test. [Publishing][publishing], [quota reference][limits]

Container creation separately permits 400 per rolling 24 hours, and containers
expire in 24 hours. [Media reference][media] API request throttling is separate:
the overview describes an app/user rolling budget of 4,800 times impressions,
excluding messaging and with exceptions for discovery/search. One daily post is
small compared with documented publishing caps but still subject to request
throttling and authorization errors. [Rate limiting][overview]

Container states are `IN_PROGRESS`, `FINISHED`, `PUBLISHED`, `ERROR`, `EXPIRED`.
Meta suggests checking once per minute for no more than five minutes. A timeout
is not proof that publishing failed. [Container][container], [publishing][publishing]

**DECISION for AED-024:** retain local attempt, container and published-media IDs
independently. After an uncertain publish response, query the known container
before retrying. `PUBLISHED` confirms publication, but the container reference
exposes no published-media-ID field or lookup edge. The account media list may
support reconciliation; caption/time matching alone is not unique proof. No
idempotency key or guaranteed exact crash-recovery mapping was established.
Stop for reconciliation rather than blindly creating another container. Existing
`Publication.external_id` alone does not implement this protocol.
[Container reference][container], [media listing][started]

## Future Insights

**VERIFIED:** Instagram Login supports Insights with `instagram_business_basic`
and **`instagram_business_manage_insights`**, using the same professional account
and access-level distinction. Account and media Insights use separate endpoints.
Some account metrics require 100 followers; unavailable data may be empty rather
than zero. Request the additional permission when implementing Insights, not for
this publishing-only test. Metric/version availability and mapping into our
historical snapshots remain future work. [Insights guide][insights]

## Evidence and scope

Sources below were read on the research date. The web reader returned rate-limit
or login errors for some developer pages; their public HTML was retrieved directly
and the embedded documentation text read instead. Meta's official Postman
collection independently confirms login paths/scope families. No third-party
tutorial is the authority. Documentation discrepancies are explicit above;
actual account behaviour remains untested.

Only this document changes. No client, HTTP dependency, hosting, secret manager,
account setup or runtime logging was added; operational logging is not applicable
to a research note. Canonical checks are `make setup` and `make check`.

[versions]: https://developers.facebook.com/docs/graph-api/changelog/
[overview]: https://developers.facebook.com/docs/instagram-platform/overview/
[appsetup]: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/create-a-meta-app-with-instagram/
[started]: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started/
[login]: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login/
[publishing]: https://developers.facebook.com/docs/instagram-platform/content-publishing/
[media]: https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media/
[container]: https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-container/
[limits]: https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit/
[insights]: https://developers.facebook.com/docs/instagram-platform/insights/

Supporting collection: [Meta Instagram API on Postman](https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login).
