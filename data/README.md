# Editorial snapshot — AED-007

[Google Sheets](https://docs.google.com/spreadsheets/d/1gL_9RS25QdAd9dTLzDEDoXsGEmfjanM5V1pda2wfqK4/edit)
is the editorial authoring source of truth for prompts, taxonomy and hashtags.
Those CSVs are versioned exports for review. AED-043 explicitly authorizes local
caption/profile snapshot authoring; the expanded copy model is not yet mirrored
in Sheets. Synchronization after owner acceptance is a separate follow-up.

Exported and checked against the live Sheet on 13 October 2026:

- `prompts.csv`: 400 Approved prompts from Content, IDs AED-0001–AED-0400.
- `taxonomy.csv`: the 40 active category/subcategory pairs from Taxonomy.
- All 400 stable IDs are preserved; weak questions were rewritten in place.
- Every pair has ten prompts. There are 39 distinct subcategory names because
  Change occurs in both Growth and Life Audit; the pair is the distribution key.
- Only authoring fields are exported. Analytics/publication columns are excluded.

All 400 prompts passed the AED-007 editorial review. It rewrote 69 questions and
improved highlights on 52 additional rows. Highlights remain exact substrings.
Every category/subcategory pair retains ten prompts. No exact or normalized
exact duplicates remain; the highest normalized character-sequence similarity
is 0.697. Longest prompt: 90 characters. Similarity checks assist review but
cannot prove semantic distinctiveness. See [the review record](../docs/editorial-review.md)
for methodology, overlap decisions, and the change rationale.

Run `make check` for the full project checks, including snapshot integrity tests.

Those tests cover contiguous IDs, uniqueness, length, distribution, metadata, and
highlight validity. The 100-character ceiling is a snapshot QA limit, not a
production renderer constraint. It does not guarantee fit in every template.

For future revisions, edit the Sheet first, export the nine documented prompt
columns in ID order plus the active taxonomy pairs, then rerun checks and review
the diff. This ticket adds no automated import/export or application integration.
There is no operational application code here, so structured logging is not
appropriate for these static data files and deterministic tests.

AED-008 adds `captions.csv` and `hashtags.csv`, reviewed against the live Captions
and Hashtags tabs on 16 October 2026. All 12 captions and six sets are retained;
active flags use `TRUE`/`FALSE`, hashtag tokens use spaces, and category mappings
use `; ` separators. See the [copy strategy](../docs/caption-hashtag-strategy.md)
for deterministic selection and override precedence. AED-038 appends `caption_override` and `hashtag_set_id` to prompts.csv. A fresh
16 October 2026 Content/Hashtags readback confirmed all 400 overrides/references
blank and all six hashtag sets active; original seven columns are unchanged.
Sheet hashtag names resolve exactly to stable TAG IDs before export. Empty or
whitespace-only metadata exports as blank cells; populated captions retain exact
text. See [import documentation](../docs/content-import.md) for validation,
normalization and safe schema-v2 persistence.

## AED-043 caption policy snapshots

`caption_profiles.csv`: exactly 400 rows with `post_id,prompt_form,response_mode,
time_focus,public_reply_ok`; no prompt duplication. All production forms are
question. Modes are reflect/recall/compare/imagine/decide/notice/appreciate/relate/act;
time focus is past/present/future/timeless/mixed. Public reply uses TRUE/FALSE and
is conservatively false for private disclosures. One primary mode per Post.
Profiles were individually classified from actual meaning, then reviewed again.

`captions.csv`: 134 active rows, 120 targeted (three per exact taxonomy pair) and
14 General (`*`/`*`). CAP-001–012 text and IDs are preserved. Fields are `id,caption,
active,category,subcategory,cta_role,prompt_forms,response_modes,time_focus,
public_reply_required,notes`. Compatibility uses `|` or `*` alone; no expressions.
CTA roles are reflect/journal/save_return/comment_reply/follow. Only comment_reply
requires public-reply permission. 33 lines use one restrained emoji; 101 use none.
The palette is 🪞/✍️/🌿/💭. Normalized duplicate text is rejected.

The dedicated caption loader validates complete snapshots before review. Metadata
is not added to SQLite. Selection is deterministic from Post/profile/catalogue and
one-based queue position, with exact override precedence, semantic filtering,
private-safe CTA fallback and exact-taxonomy preference within each CTA lane.
No runtime LLM, semantic inference, external API or Sheets dependency is involved.
See [copy strategy](../docs/caption-hashtag-strategy.md) for vocabulary, counts and
algorithm, and [caption review](../docs/caption-review.md) for evidence. Use
`make caption-review` and `make launch-review`; outputs remain ignored.
