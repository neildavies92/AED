# Launch content review

After copying `aed.example.toml` to `aed.toml` and running `make import-content`:

```sh
make launch-review
```

Open `output/launch-review/index.html` locally. The gallery shows 20 real production
JPEGs, IDs, categories/subcategories, exact selected captions, hashtag IDs/names and
tags, CTA role and caption ID, semantic profile (form/mode/time/public-reply),
seed-42 preview queue position, and explicit override indicators. Prompt alt text provides accessibility.
The curated order alternates categories; it is not a publication queue.

For custom settings:

```sh
sh scripts/with-cairo.sh uv run python -m ask_every_day.launch_review --config other.toml
```

Output is always `output/launch-review/` relative to the configuration directory.
Copy CSVs default to repository-relative `data/captions.csv` and `data/hashtags.csv`;
`--captions`, `--profiles`, `--taxonomy` and `--hashtags` allow explicit paths.
Profile CSV defaults to `data/caption_profiles.csv`, taxonomy to `data/taxonomy.csv`. Run Make from the repository root.
The command opens existing SQLite with `mode=ro`: it cannot create/migrate a DB or
change Posts, queue or publication history. Approved status belongs to the existing
snapshot import boundary; SQLite Post has no approval field. Missing curated IDs
fail with import guidance. Custom databases must contain approved imported content.

The generator reuses `render_svg` → `export_png` → `export_jpeg`, without hand layout,
resizing or browser approximations. Copy uses `select_caption` and
`select_hashtag_set`, passing persisted `caption_override` and `hashtag_set_id`.
The existing hashtag loader and dedicated `caption_catalog` loader validate the
expanded copy/profile snapshots and exact Post-ID coverage. A pure in-memory
`generate_queue(posts, seed=42)` supplies actual preview positions; there are no
publication-state exclusions or persisted queue mutations in this editorial preview.
No final publication-copy snapshot is persisted.

## Curated coverage

Each row is one category's pair; IDs are immutable. Review order shows the first
column across categories, then the second column. Lengths span 35–90 characters;
line counts cover 2, 3, 4, 5 and 6; highlight segments cover 1, 2 and 3.

| Category | First ID and reason | Second ID and reason |
| --- | --- | --- |
| Self-awareness | AED-0047: short, single-line emphasis | AED-0063: five lines, wrapped emphasis |
| Growth | AED-0085: short change question | AED-0006: long habit question, two highlight segments |
| Relationships | AED-0009: comma and short emphasis | AED-0012: four-line trust question |
| Past & Healing | AED-0172: short regret prompt | AED-0175: apostrophe, long regret contrast |
| Future | AED-0190: short control question | AED-0017: medium length, four lines |
| Confidence | AED-0254: short needs question | AED-0236: longest production prompt, 90 characters |
| Purpose & Work | AED-0257: short contribution question | AED-0284: long satisfaction/relief contrast |
| Gratitude & Perspective | AED-0030: shortest production prompt, two lines | AED-0303: long comparison question |
| Life Audit | AED-0343: short rest question | AED-0364: six lines, three highlight segments |
| Everyday Life | AED-0365: short ownership question | AED-0398: long commitment question |

The 20 selected captions are a sample of the expanded 134-caption catalogue.
All five category-specific hashtag sets
appear (TAG-002–006); General TAG-001 is a fallback, not a production mapping.
All 400 Approved Posts currently contain highlights and blank copy overrides.
Thus **no-highlight and overrides cannot be represented honestly in this production
set**. Tests use clearly synthetic fixtures for no-highlight, exact caption override
and explicit General selection; existing copy-policy tests cover fallback/disabled
behavior. No production data is altered to manufacture coverage.

## Reruns, safety and review

With identical Posts, copy snapshots, native fonts and renderer environment,
filenames, HTML and JPEG bytes are deterministic. Rendering stages the complete
pack first, then replaces each JPEG and finally the index. Replacement is atomic
per file, not a directory transaction: interrupted replacement can leave a mixture;
rerun to recover. Ordinary exceptions clean staging. Unexpected output files,
directories or symlinks fail rather than being removed. Do not edit concurrently.
Native serif font availability remains the renderer's existing portability limit.

Generated output is ignored and must not be committed. No network, publication, persisted-queue writes, dependencies or renderer changes
are introduced by caption selection. Preview queue generation is pure. Structured
summary events record counts/error types only, alongside existing renderer events;
editorial text and images are never logged. The gallery is for launch QA and future
design iterations, not an owner approval record. See [findings](launch-review-findings.md).

Observed first local generation: **1.395 seconds for 20 JPEGs**. This is diagnostic,
not a performance threshold. Tests render only this set (and one synthetic edge
case), validate JPEGs, exercise deterministic reruns and protect coverage.

## Full caption audit

`make caption-review` runs seeds 0–99 and writes `output/caption-review.csv` (all
400 Posts in real seed-42 order, with selected copy/profile) and
`output/caption-review-metrics.json` (per-seed counts, repetition and safety).
For a shorter audit, `uv run python -m ask_every_day.caption_review --seeds 10`
evaluates 0–9 plus 42. It reads versioned CSVs, not SQLite. Reports contain editorial
text intentionally; stdout/stderr contain only counts, safe events/error types and
paths. These files are ignored and are not publication records. Pure policy/audit
functions do not log. See [caption findings](caption-review.md).
