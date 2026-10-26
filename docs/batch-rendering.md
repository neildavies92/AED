# Batch rendering

Import Approved editorial content before generating local assets:

```sh
make import-content
make render-assets
```

The batch reads persisted Posts from the configured SQLite repository in ID order.
It does not read CSV or Google Sheets at runtime. Approval belongs to the import
boundary; the database's Post model has no separate approval status. An empty
repository fails with an instruction to import content. Smaller nonempty local
repositories are supported and the summary reports their actual count.

Each Post passes through the existing automatic SVG renderer, PNG exporter and
JPEG exporter. Both files are written under `AppConfig.assets_path`, creating that
directory as needed: `AED-0001.png`, `AED-0001.jpg`, and so on. PNG is the local
archive form; JPEG is the publication representation. SVG is not persisted.
Renderer settings come from `AppConfig.renderer`; incompatible JPEG dimensions or
templates fail through the existing pipeline rather than being silently changed.

For another configuration file:

```sh
sh scripts/with-cairo.sh uv run python -m ask_every_day.batch_render --config other.toml
```

`make render-assets` already uses the Cairo helper. No manual DYLD settings are
required. Configured relative paths resolve against the config file's directory.
The database parent directory must already exist, as required by the repository.

## Reruns and failures

A rerun replaces only the expected ID-based PNG/JPEG files. It creates no numbered
or timestamped duplicates and does not delete unrelated or stale files. All
rendering/export completes in memory for a Post before either asset is staged.
Both files are staged in the output directory, flushed and synced, then replaced
individually with `os.replace`. Controlled failures clean temporary files and stop
the batch; diagnostics identify the failed Post without logging editorial content.
The CLI exits nonzero. Earlier completed Posts remain usable.

This is atomic **per file**, not per pair or batch. A replacement failure or crash
between PNG and JPEG replacements may leave a new PNG alongside an old JPEG.
Abrupt process termination may also leave staging files. Fix the cause and rerun;
complete inventory/stale-file validation remains AED-016. The batch does not
invent a filesystem transaction manager or cleanup policy.

The reusable `render_assets(repository, config)` returns `BatchRenderResult` with
Post, PNG and JPEG counts. The CLI configures existing structured JSON logging;
batch start/completion summaries and a safe failure ID/count supplement existing
renderer/export events. Output paths, IDs and error types are logged, never prompt,
highlight, caption, hashtag or image payloads. Successful CLI stdout reports counts.

There is no network access, Instagram publishing, queue mutation, caption assembly
or lifecycle-directory work here. Tests generate real assets in temporary storage,
including all 400 imported Posts and all 800 expected files; no binary fixtures
are committed.

After rendering, run `make validate-assets`; the [inventory validator](asset-inventory.md)
reports missing, unexpected or malformed assets without modifying them.

## Publication-safe preparation — AED-051

Bulk rendering uses the first configured palette background as a review draft;
ID order never assigns publication colours. Posts with any attempt history or
a persisted prepared asset are skipped, preserving their bytes even if the palette
changes. `BatchRenderResult.skipped_count` reports protected Posts; other counts
report actual completed renders. Malformed asset metadata stops bulk rendering.

Use `make render-next` for the next queued canonical asset before approval.
See [background rotation](background-rotation.md) for success-driven sequencing,
explicit re-preparation and sidecar backup/recovery requirements.
