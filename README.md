# Ask Every Day

Ask Every Day is a local-first Python automation project for the Instagram account
@ask.every.day. It provides approved content, deterministic queues, rendering, interactive
publishing and explicit recovery with validated domain models.

## Prerequisites

- Python 3.13+ (development uses Python 3.13, pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Make (for the convenience commands below)

Run the following commands from the repository root. `uv sync` creates `.venv`,
installs the package and development dependencies from `uv.lock`, and downloads
the selected Python version if needed.

## Set up

```sh
make setup
```

This runs `uv sync`. All check targets use `uv run`, which automatically uses
the project `.venv`; manual activation is unnecessary.

## Development checks

Local canonical validation remains `make setup` followed by `make check`.
GitHub Actions runs these same targets on Ubuntu for pull requests targeting
`main` and pushes to `main`, including the real PNG/JPEG integration tests.

Run `make` or `make help` to list the available commands.

| Command | Action |
| --- | --- |
| `make schedule-preview` | Inspect the daily policy without credentials/network or opt-in |
| `make schedule-status` | Inspect/revalidate unattended authorization |
| `make schedule-enable` | Explicit interactive approval for future daily publications |
| `make schedule-disable` | Revoke unattended approval |
| `make schedule-run` | Execute at most one authorized daily publication; default disabled |
| `make schedule-check-credentials` | Check local credentials without refresh/provider calls |
| `make schedule-launchd` | Generate an inactive user LaunchAgent |
| `make publication-status` | Inspect publication/checkpoint state offline without migration |
| `make reconcile-publication ATTEMPT_ID=1` | Inspect one attempt; remote reads and local resolution require explicit options |
| `make backup-state` | Back up SQLite and the palette together |
| `make migrate-state` | Back up state, then migrate the schema without publishing |
| `make render-next` | Prepare the next queued Post with its success-sequenced background |
| `make publish-next` | Native development preview; production approval uses Docker |
| `make docker-prepare-next DIRECTORY=...` | Explicitly prepare the next unattempted canonical Docker JPEG |
| `make docker-manual-preview DIRECTORY=...` | Read-only exact JPEG, caption, palette and hash; no credentials |
| `make docker-manual-publish DIRECTORY=...` | Docker TTY approval for exactly one post after verified cutover |
| `make instagram-login` | Connect Instagram through browser login and trusted local HTTPS |
| `make instagram-auth-bootstrap` | Store the App Secret through a hidden prompt in macOS Keychain |
| `make instagram-auth-status` | Inspect safe local Instagram credential metadata |
| `make instagram-logout` | Remove the local Instagram credential and App Secret |
| `make caption-review` | Audit 100 preview queues and export a 400-Post caption review CSV |
| `make launch-review` | Generate a 20-Post local visual/copy review gallery |
| `make stage-media POST_ID=AED-0001` | Temporarily stage one validated JPEG over public HTTPS |
| `make import-content` | Import the approved snapshot into configured local SQLite |
| `make prototype` | Render the Classic SVG design specimen to `output/` |
| `make validate-assets` | Validate strict PNG/JPEG inventory without writes |
| `make render-assets` | Render SQLite Posts to configured PNG/JPEG assets |
| `make test` | Run pytest |
| `make lint` | Run Ruff lint checks |
| `make format` | Apply Ruff formatting |
| `make format-check` | Check formatting without editing files |
| `make typecheck` | Run Pyright |
| `make check` | Run tests, lint, formatting checks, and type checking |

The underlying `uv` commands remain available in the Makefile.
Keep the Makefile and this command list up to date whenever frequently used
setup, testing, or application commands change.

Package code lives in `src/ask_every_day/` and tests in `tests/`.
Commit `uv.lock` to keep dependency versions reproducible.

The [Classic design spike](docs/classic-design.md) records the visual target for
future renderer work and includes an intentional SVG reference.

## Domain models

`ask_every_day.domain` provides frozen dataclasses with no storage or API access:

- `Post`: editorial content, with IDs from `AED-0001` to `AED-9999`. Category,
  subcategory, prompt, and template must be nonblank. An optional highlight must
  be a nonblank, exact, case-sensitive substring of the prompt. Optional
  `caption_override` preserves nonblank text exactly; `hashtag_set_id` stores a
  stable TAG-001–TAG-999 reference, validated against active sets on import.
- `Publication`: a post reference, typed attempt status (`Publishing`, `Published`,
  or `Failed`), an aware attempt timestamp, and optional result details. Published
  results require an aware publication timestamp no earlier than the attempt and
  cannot contain an error. Errors are valid only for failed attempts.
- `QueueEntry`: a post reference and positive, one-based position. Queue-wide
  uniqueness and lifecycle state belong to later queue and state-machine work.
- `AppConfig` and `RendererConfig`: explicit local paths and account handle, plus
  template and positive image dimensions (default `Classic`, 1080 × 1350).
  Paths are values only; construction does not access the filesystem.

Invalid values raise `ValueError`; incorrect runtime types raise `TypeError`.
Construction never trims, coerces, or silently normalizes inputs. Categories and
subcategories remain open text values; taxonomy loading and validation,
persistence identity, and publication transitions remain later work.

See [local configuration](docs/local-configuration.md) for TOML settings and the
`load_config()` API.

The [SVG renderer](docs/renderer.md) accepts `Post` and `RendererConfig` and returns
deterministic SVG. `make prototype` exercises it with the hand-set Classic specimen.

See [content importing](docs/content-import.md) for validation and safe rerun semantics.

Local commands emit timestamped JSON operational logs to stderr and keep functional
output on stdout. See [application logging](docs/logging.md) for the schema,
failure diagnostics and safe context rules.

## Generate local assets

After `make import-content`, run `make render-assets` to render persisted SQLite
Posts into the configured assets directory as `AED-NNNN.png` and `AED-NNNN.jpg`.
See [batch rendering](docs/batch-rendering.md) for rerun and failure semantics.

Run `make validate-assets` after rendering to check exact filenames and image
representations. See [asset inventory](docs/asset-inventory.md).

Generate a curated visual and copy review with `make launch-review`; see
[launch review](docs/launch-review.md) and [inspection findings](docs/launch-review-findings.md).

For controlled manual acceptance, [public media staging](docs/media-staging.md)
serves one canonical JPEG through an externally installed `cloudflared` Quick
Tunnel. It verifies public bytes and stays alive until Ctrl+C; it does not publish.

[Instagram authentication](docs/instagram-auth.md) provides browser OAuth, a
loopback HTTPS callback, account binding and macOS Keychain credential lifecycle.
Meta accepted the localhost redirect and the owner accepted the tester invitation.
Safe live login and account verification passed on 26 October using the AED-050
response-parser fix. Controlled publication acceptance passed on 26 October:
one approved image/caption was published and recorded as Published.
Authentication commands do not create Instagram media or publish posts. Webhooks are not
required for this scope, and App Review is deferred for tester acceptance.

See [publish next](docs/publish-next.md) for preview, one-post approval, state
persistence, cleanup and reconciliation limits.

[Background rotation](docs/background-rotation.md) configures the blue brand
palette and explains `make render-next`, persisted assignments and one-time
initialization for historical publications. Bulk renders are review drafts;
prepare the next canonical image before approving it.

[Publication recovery](docs/publication-recovery.md) documents schema-v3 migration,
coordinated backups, durable POST checkpoints and explicit evidence-based local
resolution. Uncertain outcomes block automatic replay; this is not a provider
exactly-once guarantee or an enabled scheduler.

[Daily scheduling](docs/scheduler.md) adds explicit, revocable authorization for a
short-lived portable command. It is disabled by default. The macOS LaunchAgent is
generated separately and is never installed or activated by setup. The
[runtime contract](docs/runtime-contract.md) defines credential injection and the
shared command for the later Linux container adapter.

[Linux production deployment](docs/linux-deployment.md) covers AED-056 host setup,
coordinated state migration, private credential handoff, disabled UTC cron and
owner-approved cutover. `make -f deployment/Makefile help` lists offline helpers.

[Local Docker Desktop acceptance](docs/local-docker.md) covers AED-057 private
production-state copies, mount checks, synthetic HTTPS staging and explicit
20:00 Europe/London one-post acceptance before AED-058 scheduling.

[Local font review](docs/font-review.md) documents the accepted Liberation Serif/Lato
editorial pairing and open-source typography specimens,
optional operator-provided fonts, style/hash inspection and renderer reapproval.

Production manual publication uses the [canonical Docker workflow](docs/canonical-manual-publishing.md),
including verified editorial font files and explicit fresh-state authority cutover.
