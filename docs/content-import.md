# Approved content import — AED-033

Run from the repository root after `make setup`:

```sh
cp aed.example.toml aed.toml
make import-content
```

The configured database's parent directory must already exist. The example uses
the existing `data/` directory. The importer creates the database file through
the repository, but creates no directories or assets. Existing configuration
loading resolves the database relative to the TOML file. Source paths default to
`data/prompts.csv` and `data/taxonomy.csv` relative to the working directory;
explicit paths support invocation elsewhere:

```sh
uv run python -m ask_every_day.content_import --config aed.toml \
    --source data/prompts.csv --taxonomy data/taxonomy.csv
```

Google Sheets remains the editorial authoring source. Runtime import reads only
the versioned CSV artifacts and has no authentication, network, or Sheets API.

## Validation and reruns

`load_snapshot(source, taxonomy)` validates the entire input before opening the
database in the CLI. It returns ID-sorted `Post` values without modifying strings.
Both CSVs require exactly their documented column sets, with no duplicate header
names or missing/extra values. Header order may vary; UTF-8 with or without a BOM
is supported. Invalid CSV, invalid domain fields or metadata fail explicitly.

The production contract requires 400 unique IDs AED-0001–AED-0400, only Approved
rows and Classic templates, distinct normalized prompt text, and ten rows per
each of the 40 distinct taxonomy pairs. Subcategory identity includes category,
so the two Change groups stay separate. Blank highlights become `None`; populated
highlights must satisfy the existing exact-substring domain rule. This validates
structure, not editorial quality or eventual layout fit.

`Repository.import_posts(posts)` performs an atomic **create or compare**:

- Missing posts are inserted in ID order.
- Identical existing posts are left untouched and counted as unchanged.
- Any differing persisted field or stored ID absent from the snapshot raises
  `ContentDriftError` and makes no content changes.
- No automatic updates, deletes, force-overwrite option, queue or publication
  changes occur. Review drift and reconcile it explicitly before rerunning.

The importer owns no SQL. The SQLite implementation locks comparison and all
inserts inside one `BEGIN IMMEDIATE` transaction; a later insertion failure rolls
back earlier rows. Other SQLite failures retain their exception types. Snapshot
validation failures happen before writes; the CLI also avoids creating an empty
database for invalid files. A valid-file import into a new database may initialize
its empty schema before a later import failure, but cannot leave partial content.

The result contains imported/unchanged counts; a second identical run reports
zero imported and 400 unchanged. The CLI exits nonzero on configuration, file,
validation, drift or SQLite errors, with exception type rather than content.

## Checks and logging

`make setup` and `make check` cover full metadata round trips, repeated import,
bad statuses/IDs/columns/taxonomy/highlights/templates, local drift, database
rollback with an injected late failure, and command invocation from another
working directory. No dependency is added.

Standard-library records carry `event`, source path and imported/unchanged counts
(updated count is always zero), or exception type on failure. Repository logs
report the single atomic operation; prompt payloads are omitted. Applications
may configure handlers for structured output; the CLI emits concise event names
and a count summary. No content export helper or live editorial sync is added.

Queue generation, rendering, workflow transitions and publishing remain separate.
