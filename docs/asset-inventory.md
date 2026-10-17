# Validate rendered assets

Run `make validate-assets` after `make import-content` and `make render-assets`.
For another configuration, use:

```sh
uv run python -m ask_every_day.asset_inventory --config other.toml
```

Expected IDs come from the configured SQLite repository, never CSV, Sheets or the
files being inspected. Every Post requires exactly `{id}.png` and `{id}.jpg` in
`assets_path`. The inventory is flat and case-sensitive: `.jpeg`, uppercase
extensions, unknown IDs, duplicate-name variants, staging files, arbitrary files
and nested directories all fail. Expected assets must be regular files, not
symlinks. A symlink asset root also fails. Empty repositories fail actionably.

PNG bytes undergo actual format/dimension checks, Pillow verification and full
decoding. Dimensions must match `RendererConfig`. No new PNG metadata restrictions
are imposed. JPEG bytes use the existing `validate_jpeg` contract: complete RGB
JPEG, 1080×1350, maximum 8 MB, and excluded metadata. The validator does not compare
pixels across formats or recognize whether an image depicts the correct prompt.

`validate_asset_inventory(repository, config)` returns `AssetInventoryResult` with
Post/PNG/JPEG counts. Failures raise `AssetInventoryError`. Diagnostics report the
total issue count and at most ten deterministically sorted, bounded filenames and
reasons. Unknown entries are reported without decoding or traversing them. The
CLI prints counts on success and exits nonzero on failure. Existing structured
logging records start/success/failure and safe counts/path/error type, never image
contents, editorial text, metadata payloads or per-file success chatter.

## Read-only behavior

Validation does not create directories, rewrite images, delete/rename entries,
change queue state or contact the network. Normal filesystem access-time updates
are outside the application guarantee. CLI SQLite connections use `mode=ro` and
the existing `SQLiteRepository` reader. Unlike ordinary `open_repository`, this
cannot create a missing database or migrate an old one: run the normal import or
migration workflow first. The connection always closes, including on failure.

These checks assume the inventory is not concurrently edited while being read;
there is no filesystem snapshot or lock spanning validation and later use.
Investigate unexpected files manually; rerun `make render-assets` for missing or
invalid expected files. Rendering preserves unexpected entries, so a rerun alone
does not fix every inventory error. No automatic cleanup or repair is provided.

## Test scope

Focused cases cover strict names, missing/unreadable/corrupt/mislabeled/truncated
images, metadata/dimension errors, symlinks/directories, duplicate expected IDs,
bounded diagnostics, logging, and unchanged file bytes/mtimes/database state.
Integration imports the complete production snapshot into temporary SQLite, then
copies three persisted representative Posts (AED-0001, AED-0236, AED-0364) into a
second temporary SQLite database through the real repository. After reopening,
real batch rendering and validation use that repository without mocks. This
exercises importer → SQLite reopen → batch → real decoders without repeating
AED-015's expensive full-400/800-file rendering acceptance test. No dependencies,
binary fixtures, repair framework or publishing behavior are added.
