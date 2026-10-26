# Ask Every Day

Ask Every Day is a local-first Python automation project for the Instagram account
@ask.every.day. This foundation provides an installable package and development
tools, plus validated domain models. Application workflows will be added in later tickets.

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
response-parser fix; controlled image-publication acceptance remains pending.
These commands do not create Instagram media or publish posts. Webhooks are not
required for this scope, and App Review is deferred for tester acceptance.
