# Caption and hashtag strategy — AED-008

Google Sheets is the editorial source of truth. Reviewed on 16 October 2026:
all 12 active captions and six active hashtag sets are retained unchanged in
`data/captions.csv` and `data/hashtags.csv`. IDs, copy, active flags, notes and
category mappings match the Sheet; CSV headers use the repository's snake_case
convention. `active` is exactly `TRUE` or `FALSE`.

The caption pool mixes quiet invitations, writing prompts and reminders to return.
The save/return lines offer a practical use rather than demanding engagement;
"What comes up for you?" works as a private reflection, not a request for comments.
One restrained mirror emoji fits the brand. No caption needs rewriting for novelty.
Each hashtag set contains five valid, distinct evergreen tags. Broad journaling
tags intentionally recur across sets; only one set is used per post. Category
relevance and consistent tone motivate the choices, with no claim about reach.

## Deterministic selection

Caption precedence:

1. A nonblank `Content.Caption Override` wins, preserved exactly, including
   whitespace and line breaks. `None`, empty or whitespace-only values mean absent.
2. Otherwise sort active captions by immutable CAP ID and select zero-based index
   `(numeric_post_id - 1) % active_caption_count`. No active captions is an error.

For the current pool AED-0001 selects CAP-001, AED-0012 selects CAP-012,
AED-0013 selects CAP-001 and AED-0400 selects CAP-004. No random generator,
process-salted hash or queue position is involved. Identical ID and editorial
data give identical results regardless of pool ordering. Changing active entries
can change future selections; this is not a historical publication-copy snapshot.

Hashtags are enabled by default, limited to one five-tag set:

1. An explicit nonblank `Content.Hashtag Set` must match exactly one active set
   by ID or exact, case-sensitive name. The Sheet dropdown currently supplies names;
   policy callers may also supply immutable TAG IDs. Unknown, inactive or ambiguous
   selections fail rather than silently falling back. Whitespace-only means absent;
   otherwise values are not trimmed or case-normalized.
2. Without an explicit set, use the single active category-specific mapping below.
3. Without an active specific mapping, use the active `All categories` fallback
   (currently TAG-001 General). With no active fallback, emit no hashtags.
   Multiple active matches at either level are an error.

| Set | Categories |
| --- | --- |
| TAG-001 General | Fallback only; may also be selected explicitly |
| TAG-002 Reflection | Self-awareness; Past & Healing |
| TAG-003 Growth | Growth; Confidence; Life Audit |
| TAG-004 Relationships | Relationships |
| TAG-005 Future | Future; Purpose & Work |
| TAG-006 Gratitude | Gratitude & Perspective; Everyday Life |

All ten current categories have exactly one specific mapping. A future unmapped
category uses the same fallback rule; new snapshot mappings must still use valid
taxonomy categories. General is never randomly mixed with the specific set.

A caller may explicitly set `enabled=False` to omit hashtags, including an otherwise
valid explicit selection. Invalid explicit selections still fail validation so a
disabled run cannot hide stale editorial references. This is a policy argument,
not a new TOML setting or special sentinel in the Sheet dropdown. Future publication
assembly should preserve the selected caption and append the selected set in its
stored order after a blank line; append nothing when disabled/no set resolves.

## Pure policy and runtime boundary

`ask_every_day.copy_policy` supplies frozen `Caption`/`HashtagSet` values plus
`select_caption` and `select_hashtag_set`. Callers supply editorial data and optional
overrides. It performs no file/database/network access and no logging. IDs, active
flags, blank copy, hashtag syntax, duplicate IDs/names/tags and ambiguous selection
are validated. Current supported hashtag tokens use ASCII letters, digits and
underscores after `#`; do not silently broaden this when editing the snapshot.

The existing Post/SQLite/importer does not retain Caption Override or Hashtag Set.
All 400 current Content rows have both fields empty, but future overrides would
otherwise be lost. A separately tracked follow-up must version/import/persist those
fields with safe rerun/drift semantics before runtime publication assembly consumes
them. This ticket changes no domain fields, schema, importer or publishing flow.

Config's Caption Selection and Hashtag Selection rows now describe these rules.
Other Config settings and Content rows are unchanged. Edit the Sheet first, export
the same columns in immutable ID order without altering values, verify exact
readback against CSV, then run `make setup` and `make check`. Tests validate the
snapshot, ten mappings, overrides, disabled/inactive behavior and cross-process
determinism. No live Sheets access is needed in tests or at runtime.
