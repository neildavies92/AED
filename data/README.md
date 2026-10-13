# Editorial snapshot — AED-007

[Google Sheets](https://docs.google.com/spreadsheets/d/1gL_9RS25QdAd9dTLzDEDoXsGEmfjanM5V1pda2wfqK4/edit)
is the editorial authoring source of truth. These CSV files are versioned exports
for review, not a second independently maintained content library.

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

For future revisions, edit the Sheet first, export the seven documented prompt
columns in ID order plus the active taxonomy pairs, then rerun checks and review
the diff. This ticket adds no automated import/export or application integration.
There is no operational application code here, so structured logging is not
appropriate for these static data files and deterministic tests.
