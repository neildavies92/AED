# Publish the next queued post

Production manual publishing now uses the [canonical Docker workflow](canonical-manual-publishing.md).
From the reviewed checkout, select the reviewed private deployment:

```sh
make docker-prepare-next DIRECTORY='/absolute/private/deployment'
make docker-manual-preview DIRECTORY='/absolute/private/deployment'
# After explicit verified cutover, exact image review and owner approval:
make docker-manual-publish DIRECTORY='/absolute/private/deployment'
```

Docker verifies the accepted Liberation Serif/Lato files and exact fresh raster
bytes; prepare is explicit and refuses attempted/published assets. Preview shows
the complete caption, Post ID, palette, canonical JPEG and hash without credentials
or network access. The native `make publish-next` preview remains a development
summary; all production publication is Docker-only and `CONFIRM=1` on macOS fails closed before Keychain access.

The manual container command preserves the existing exact TTY `PUBLISH ...`
challenge, account binding, full caption fingerprint, advisory lock, staging byte
verification, quota checks, durable checkpoints and media-ID verification. It uses
the existing FileCredentialStore. Every post requires separate approval; manual
approval outside the scheduled window does not grant or change scheduler policy.
Mac state stays authoritative until the explicit fresh-state cutover documented
in the linked procedure. Never publish from an independently active copied state.

## Failures and restart

Preflight/staging failure leaves the Post queued with no attempt. Container ERROR
or EXPIRED records terminal Failed. Unknown status, status timeout, interrupted
processing, or any uncertain create/publish POST leaves Publishing and prevents
another CLI publication until reconciled. Never assume a timeout means nothing
was published, create a replacement container, reset state, or requeue the Post.

On a result-write or cleanup failure, error output preserves known attempt,
container and media IDs. A media ID means Meta reported publication even if local
persistence or cleanup failed. Inspect SQLite and provider evidence before any
further action. A Published record is terminal and cannot be selected again.

**AED-024 recovery is implemented:** schema-v3 checkpoints retain account/asset
binding, create and publish intent, and the returned container ID. Media ID and
Published commit together with completion and palette validation. No POST is
resumed after restart. Use `make publication-status` or read-only
`make reconcile-publication ATTEMPT_ID=...`; remote GETs and local mutation require
explicit options. See [publication recovery](publication-recovery.md) before
`make migrate-state`, recovery or any interpretation of an uncertain result.
An ID can still be lost between receiving it and a failed/crashed SQLite commit;
its prior intent blocks replay but does not implement provider exactly-once delivery.

See [authentication](instagram-auth.md), [staging](media-staging.md), and
[publication state](publication-state-machine.md) for their existing contracts.
