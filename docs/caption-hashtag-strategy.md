# Caption and hashtag strategy — AED-043

The global twelve-caption ID rotation was deterministic but context-blind. Copy now
uses reviewed, versioned per-Post profiles, exact taxonomy and publication position.
No runtime LLM, keyword/punctuation inference, network or Google Sheets access is
involved. Selection never reads arbitrary prompt text to guess meaning.

## Editorial snapshots

`data/caption_profiles.csv` contains exactly 400 rows, one for each Approved Post,
with no duplicated prompt text. Columns: `post_id`, `prompt_form`, `response_mode`,
`time_focus`, `public_reply_ok`. All 400 current Posts are questions; statement
support is exercised with synthetic fixtures, not invented production content.

| Field | Vocabulary / meaning |
| --- | --- |
| prompt_form | question, statement |
| response_mode | reflect (consider meaning), recall (retrieve experience), compare (contrast), imagine (explore a possibility), decide (choose), notice (observe), appreciate (value), relate (consider a connection), act (identify a practical step) |
| time_focus | past, present, future, timeless (habitual/general), mixed (connects periods) |
| public_reply_ok | TRUE/FALSE; whether actively inviting public disclosure is appropriate |

Use one primary mode; taxonomy is a subject, not a mode. These are deliberately
read and authored editorial judgements. For example AED-0013 recalls an ordinary
memory, AED-0030 appreciates enough, AED-0236 compares without competition.
Grief, regret, forgiveness, relationship conflict and private identity material
are conservatively private. AED-0047 explicitly asks about a private pleasure and
was marked private during the second review pass. This is CTA suitability, not a
medical/safety classifier. Re-review profiles whenever prompts change.

Profile counts: question 400; act 19, appreciate 14, compare 30, decide 41,
imagine 71, notice 73, recall 50, reflect 81, relate 21; future 79, mixed 53,
past 44, present 134, timeless 90; public replies permitted 249, private 151.
These distributions reflect the actual corpus, not quotas.

`data/captions.csv` has 134 active immutable IDs: the original CAP-001–012 text is
unchanged; CAP-013–132 supply exactly three targeted captions for each of the 40
exact Category/Subcategory pairs; CAP-133–134 add two quiet follow invitations.
There are 120 targeted captions and 14 General fallbacks. General uses `*`/`*`;
partial wildcards and unknown taxonomy pairs fail.

Columns: `id`, `caption`, `active`, `category`, `subcategory`, `cta_role`,
`prompt_forms`, `response_modes`, `time_focus`, `public_reply_required`, `notes`.
Compatibility cells contain `*` alone or unique allowed values separated by `|`.
Flags are exactly TRUE/FALSE. Casefolded, NFKC-normalized, whitespace-collapsed
caption text must be unique. Loaders reject malformed rows/columns, unknown values,
duplicate IDs, missing/extra profiles and unreachable active copy before review.

| CTA role | Action | Catalogue count |
| --- | --- | ---: |
| reflect | Quiet internal thought | 34 |
| journal | Writing/private exploration | 23 |
| save_return | Save or revisit | 42 |
| comment_reply | Explicit optional public response | 33 |
| follow | Follow the account | 2 |

Only `comment_reply` requires public-reply permission, enforced when constructing
captions. The other roles never solicit public disclosure. Sensitive pairs use a
second quiet caption instead of a comment invitation. Legacy CAP-007 remains
reflection: its text asks no one to post publicly. CAP-008 allows uninterrupted
thought without explicitly asking for writing, so its role is reflect.

Broad compatibility is appropriate for copy without form/mode/time assumptions;
question-specific wording is restricted to questions. Memory-detail reply copy is
restricted to recall and past/mixed focus. No historical-comparison caption can
ignore declared time/mode restrictions to fill a lane. Future statement Posts may
use compatible General copy until targeted statement copy is authored.

33 captions (24.6%) have one emoji; 101 have none. The only palette is 🪞, ✍️, 🌿,
💭. No emoji dependency is needed. Tests enforce the actual palette, at most one
mark, unique text and the current <=120-character editorial bound. Most lines are
much shorter. No urgency, engagement bait, therapy claims or stacked actions.

## Deterministic caption selection

`select_caption(post, captions, profile, position=...)` is pure. It returns exact
text plus caption ID, CTA role and General-fallback metadata. An exact nonblank
`Post.caption_override` wins unchanged, even without a profile or catalogue;
metadata is marked as owner override rather than assigning an inferred role.
The existing domain/import boundary normalizes absent cells and rejects blank
populated overrides. Automatically selected copy is not a persisted history record.

For non-overrides, the profile must match the Post ID. Active captions must match
form, primary mode, time focus and public-reply permission. No fallback ever drops
these constraints. Publication positions are positive one-based integers:

1. Every 30th: follow -> save_return -> reflect/journal.
2. Otherwise every 9th: save_return -> reflect/journal.
3. Otherwise every 3rd: comment_reply -> save_return -> reflect/journal.
4. Other positions: reflect/journal only.

Within **each lane**, prefer exact Category/Subcategory, then compatible General.
Only after both fail, try the next safe lane. This lane-first ordering allows rare
General follow invitations without replacing every scheduled CTA with targeted
quiet copy. A private Post in a comment slot gets a compatible save/return caption,
then quiet copy if needed. Quiet positions never escalate to a strong CTA.
No candidate raises a clear Post-ID-only error.

Equally eligible candidates sort by immutable CAP ID; choose
`(numeric_post_id + position - 2) % candidate_count`. Caller ordering, process hash
seed and global random state cannot affect selection. There is no second queue or
stateful anti-repetition algorithm. Changing snapshots/position can change selected
copy; callers requiring historical copy must retain it at a future publication
boundary.

Position is a required keyword. Explicit `position=None` means **quiet preview**,
using `(numeric_post_id - 1) % candidate_count`, not an invented publication slot.
Launch review instead uses actual positions in a pure seed-42 400-Post preview
queue. It never reads, replaces or advances the persisted queue.

`caption_catalog.py` owns CSV I/O; `copy_policy.py` has none and emits no logs.
SQLite remains schema v2: profiles are versioned copy policy, not core Post state.
Domain, importer, repository and queue-service behavior are unchanged.

## Hashtags (unchanged)

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

## Review and maintenance

Run `make caption-review` for 100 real queues (seeds 0–99), an ignored seed-42
400-row CSV and aggregate JSON in `output/`. Run `make launch-review` to judge the
20 production JPEG/caption combinations. See [caption review](caption-review.md)
for measured distributions, repetition and editorial findings.

Semantic suitability wins over percentage targets. In seed 42, quiet roles are
66.75%, comment_reply 12.25%, save_return 17.75%, follow 3.25%. The comment share is
below the aspirational 15–25% because private Posts redirect scheduled comments to
save/return; no privacy flag is relaxed to hit a quota. Strong CTAs remain 33.25%.

For AED-043 the repository snapshots are the authorized copy-authoring source;
the existing Sheets tabs have not been expanded or synchronized. Prompt, taxonomy
and hashtag authoring rules remain unchanged. Follow-up after owner acceptance:
synchronize the expanded catalogue/profile vocabulary to editorial Sheets in a
separate ticket. Runtime must continue using reviewed snapshots, never live Sheets.
