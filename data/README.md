# Editorial snapshot — AED-006

[Google Sheets](https://docs.google.com/spreadsheets/d/1gL_9RS25QdAd9dTLzDEDoXsGEmfjanM5V1pda2wfqK4/edit)
is the editorial authoring source of truth. These CSV files are versioned exports
for review, not a second independently maintained content library.

Exported and checked against the live Sheet on 12 October 2026:

- `prompts.csv`: 400 Draft prompts from Content, IDs AED-0001–AED-0400.
- `taxonomy.csv`: the 40 active category/subcategory pairs from Taxonomy.
- The original 40 seed rows are preserved; 360 new questions were added.
- Every pair has ten prompts. There are 39 distinct subcategory names because
  Change occurs in both Growth and Life Audit; the pair is the distribution key.
- Only authoring fields are exported. Analytics/publication columns are excluded.

All prompts are original drafts or preserved project seeds. Highlights are exact
substrings. Generation QA reviewed semantic overlap, repeated openings, length,
and awkward wording; three overlapping questions and four wording issues were
revised. No normalized exact duplicates remain; no pair exceeded 0.73 character
sequence similarity in the generation check. Longest prompt: 87 characters.
Similarity checks cannot prove editorial distinctiveness. AED-007 owns the
subsequent editorial review and approval; no content has been marked Approved.

Run `make check` for the full project checks, including snapshot integrity tests.
Those tests cover contiguous IDs, uniqueness, length, distribution, metadata, and
highlight validity. The 100-character ceiling is a snapshot QA limit, not a
production renderer constraint. It does not guarantee fit in every template.

For future revisions, edit the Sheet first, export the seven documented prompt
columns in ID order plus the active taxonomy pairs, then rerun checks and review
the diff. This ticket adds no automated import/export or application integration.
There is no operational application code here, so structured logging is not
appropriate for these static data files and deterministic tests.
