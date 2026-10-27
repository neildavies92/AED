# Publication background rotation — AED-051

Classic keeps its 1080×1350 canvas, fonts, sizes, coordinates, line breaking,
highlight spans and caption policy. Only colours change. Defaults are Ice Blue
`#E2EFF9`, Mist Blue `#EDF4FB`, Soft Blue `#D3E6F5`, primary text `#1C3651`, and
accent/highlight `#2F608C`. The handle, underline and footer use the same ink palette.

Configure `[palette]` in `aed.toml` (see `aed.example.toml`):

```toml
[palette]
backgrounds = ["#E2EFF9", "#EDF4FB", "#D3E6F5"]
text_color = "#1C3651"
accent_color = "#2F608C"
seed = 42
```

Exactly three distinct `#RRGGBB` colours are required; case variants count as the
same colour. Both ink colours must have at least 4.5:1 contrast against every
background. The seed is an integer from 0 through 2^63−1. Unknown settings and
invalid values fail configuration loading. Publication assignments use canonical uppercase hex.
The pure renderer/specimen defaults to Ice Blue and has no rotation cursor.

## Prepare before approval

```sh
make render-next
make publish-next
make publish-next CONFIRM=1
```

`render-next` prepares only the next queued Post's canonical `AED-NNNN.png` and
`AED-NNNN.jpg`, using the actual next successful publication colour. It makes no
network, credential, staging or Instagram call. Both preparation and confirmed
publication hold the existing per-database advisory lock, so preparation cannot
replace an image while the operator is approving or publishing it.

`publish-next` remains a local preview. It now includes background colour, themed
publication sequence and asset readiness. It does not generate files. A valid
old JPEG is insufficient: the persisted assignment, rendered SVG fingerprint and
canonical JPEG hash must all match. Run `render-next` if the image is missing,
corrupt, untracked or stale after queue/config/template changes, inspect that
image, then approve its new hash. The composed caption and remote publication
sequence are unchanged.

A matching prepared PNG/JPEG pair is reused byte-for-byte without invoking the
rasterizer, including across process restarts. Rendering inputs or damaged files
require explicit re-preparation before approval. `make render-assets` remains an
ID-order draft/review renderer using the first configured background. It skips
all prepared Posts and all Posts with any publication attempt, including Failed,
Publishing and Published. It cannot overwrite the approved or historical asset.

The approval fingerprint binds the selected colour/sequence as well as the exact
Post, account, copy and JPEG bytes. Existing plan rechecks and staging's expected
SHA256 remain in force. Publication never silently renders a replacement. An
uncertain POST still leaves Publishing and blocks both selection and preparation;
there is no automatic retry or reset.

## Success-driven bags and durable state

The rotation is a deterministic shuffled bag, independent of Post ID, queue
position and global random state. Each group of three **successfully published
tracked images** uses each colour exactly once. At each new bag, a versioned
SHA256 ranking selects among the six permutations, excluding the previous bag
order and any order starting with the last successfully published colour.
There is no process-local RNG cursor or use of Python's randomized `hash()`.

The ignored `<database>.palette.json` sidecar lives beside the resolved SQLite
path. It records the bag, slot, successful-publication sequence, palette key and
SVG/PNG/JPEG digests for each prepared Post. It stores no editorial text, tokens or
provider media IDs. Back it up and restore it **together with SQLite**.

Selection reconstructs progress from SQLite's Published records in publication
completion order (timestamp, then attempt ID), joined to these assignments.
Preparing, skipping or failing a Post does not consume a slot. Regenerating the
queue changes which Post receives that slot. If a previously prepared Post moves
later, its old assignment becomes stale and must be prepared again. Existing
publication records and terminal-state policy are preserved.

Seed changes wait until the current bag finishes. Change the set of background
colours only at a completed bag boundary; changing it mid-bag fails closed until
the old palette finishes its bag. This preserves three-colour balance. Ink changes
can happen at any point but require preparation/review if they change image bytes.
A new bag always avoids the last successful background, including shared colours
between old and new palettes.

Metadata reads are bounded and reject malformed, duplicate-key or symlink files.
Writes use a private temporary file, fsync, atomic replacement and directory fsync.
SQLite transactions coordinate history reads with preparation and bulk rendering;
no SQLite schema or publication-state migration is needed. The asset pair and
sidecar are not one filesystem transaction: an interrupted replacement can leave
stale files, but hash/sequence checks prevent their approval. Re-run preparation
to repair them. Unknown or inconsistent successful history fails closed rather
than guessing a colour or claiming cross-crash publishing idempotency.

## Existing history and a missing ledger

A fresh database with no Published records initializes automatically. If Published
records predate this feature, provide the verified last background **once**:

```sh
sh scripts/with-cairo.sh uv run python -m ask_every_day.render_next \
  --previous-background '#F4F0E8'
```

`#F4F0E8` is the verified old Classic background used in the initial controlled
acceptance; use the actual verified value for another database. This records
historical publication IDs as a baseline without editing their records or images.
It does not mark deleted Instagram media unpublished. The first new colour avoids
that boundary; all new tracked successes then obey the three-colour bags.

A lost ledger after successful publication also fails closed. Restore its matching
backup and reconcile history; do not use the bootstrap option to reset an existing
rotation or delete the ledger to work around uncertainty. An existing ledger
rejects a second bootstrap argument. Successful publications made through another
untracked tool require reconciliation, not guessed metadata.

Structured events include colour, Post ID, queue position, palette sequence and
publication attempt ID when available. Editorial text, image payloads, capability
URLs and credentials are excluded. This feature adds no live API calls or
scheduler. AED-024 adds explicit recovery without requeueing attempts; historical
Published records remain terminal.

## Verification — 26 October 2026

`make setup` passed. The final `make check` passed with **1,139 tests**, Ruff lint,
Ruff format (102 files) and Pyright (zero errors/warnings). Tests cover long balanced
sequences across seeds, failures, restarts/fresh processes, queue regeneration,
legacy bootstrap, corrupt/inconsistent history, atomic-write failures and exact
prepared/approved JPEG consistency. No provider API was invoked for this change.

Ice Blue, Mist Blue and Soft Blue JPEGs of AED-0395 and a long-prompt PNG of AED-0236
were generated locally and visually checked at 1080×1350: readable ink, unchanged
Classic placement/highlights and no clipping. The deliberate SVG/PNG specimen
was refreshed; local review images remain ignored under `output/aed-051-review/`.

## Publication recovery — AED-024

Recovery retains the original assignment and JPEG digest, validates the resulting
Published ordering and matching palette metadata inside the local success
transaction, and never rewrites attempted images. Missing/mismatched sidecars keep
the attempt unresolved. `make backup-state` / `make migrate-state` back up SQLite
and the palette together; see [publication recovery](publication-recovery.md).
