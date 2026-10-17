# Local end-to-end dry run

Run `sh scripts/with-cairo.sh uv run pytest tests/test_end_to_end_dry_run.py -s`
from the repository after `make setup`. It also runs under `make test` / `make check`.
No new production command or orchestration layer is needed for this test proof.

The successful scenario uses real components throughout:

1. Import the checked-in `data/prompts.csv`, `data/taxonomy.csv` and
   `data/hashtags.csv` through `import_snapshot()` into temporary SQLite.
   Check 400 Posts, schema v2 and the currently blank copy metadata (stored as
   `None`). Read the source bytes again at completion to confirm no changes.
2. Generate and store the entire constrained queue with seed **42**, checking
   complete unique membership, consecutive initial positions, and spacing through
   the existing validator. With the current snapshots/algorithm, the first Post
   is **AED-0395**. Verify Ready before queuing and Queued after reopening.
3. Copy that exact stored Post, including metadata, via `import_posts()` into a
   second temporary SQLite database. Reopen it, verify equality with the original,
   and call the real `render_assets()` application boundary. This performs real
   SVG rendering, CairoSVG PNG export, Pillow JPEG export and filesystem writes.
   Decode the representative PNG/JPEG and verify format, RGB mode and configured
   dimensions. This deliberately separated rendering subflow lets the existing
   all-Posts batch API run unchanged without rendering another 400 images; the
   existing AED-015 batch test still covers the complete production corpus.
4. On the original database, call `PublicationService.begin_publish()` at
   **2026-10-17T12:00:00.123456Z**. Verify Publishing, one attempt and removal of
   exactly the selected queue entry. Close/reopen while Publishing.
5. Simulate a successful local result with `record_success()` at
   **2026-10-17T12:00:05.654321Z**, using external ID `dry-run-media-001`.
   Reopen again and verify Published, exact persisted history including timestamps
   and ID, the unchanged Post, and the remaining 399 queue entries and next Post.

A second integration scenario imports/queues the same corpus, records a synthetic
failure, and verifies Failed/history after reopen. A new attempt is rejected;
there is no retry. Queue positions after removal retain their repository values;
only creation requires consecutive positions.

Both tests block socket connections, connection helpers, DNS resolution, datagram
sending and standard HTTP requests. No tokens, Instagram client, uploads, tunnels
or external requests participate. All runtime databases and assets live under
pytest temporary paths; developer configuration, databases and assets are unused.
No core importer, repository, queue, publication or rendering component is mocked.

Only the publication outcome is simulated. This proves local integration, not
Meta credentials, public HTTPS media hosting, live container creation,
`media_publish`, or remote idempotency/reconciliation. It does not implement
AED-023 publish-next, AED-024 retries/reconciliation, AED-037 history-prefix rules,
caption assembly or the independently developed AED-016 inventory validator.
SQLite migration edge cases remain covered by dedicated schema tests; this
scenario initializes a fresh v2 database. Production operational logging remains
unchanged, with no new content logging or dependencies.

Observed focused run: **2 passed in 2.46 seconds**, including **0.70 seconds** for
the successful scenario on the development machine. Timings are diagnostic;
there is no machine-dependent performance threshold.
