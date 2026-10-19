# AED-043 caption review — 19 October 2026

Implementation/editorial QA, not owner sign-off. Based on green integrated main
`9f13fa7` (AED-042 merged); its baseline passed 648 tests plus Ruff and Pyright.
Caption selection changes no image/rendering code, prompt, highlight, hashtag,
queue-generation/persistence rule, dependency or SQLite schema.

## Authoring and second review

Read all 400 prompts grouped by the 40 exact taxonomy pairs. Assign one primary
response mode, temporal focus and public-reply judgement to each immutable ID;
then review the resulting classifications against each complete prompt again.
No automatic keyword classifier was used. All 400 are genuinely questions.
The nine modes occur 14–81 times, and all five time focuses occur 44–134 times;
no suspicious all-corpus default was used. The dominant reflect/notice modes fit
the reflective corpus. See the full counts in [copy strategy](caption-hashtag-strategy.md).

151 profiles are private, 249 permit optional public invitations. The second pass
made AED-0047 private because the prompt explicitly asks about a private pleasure.
A form/mode/time match alone cannot override that flag. Grief, regret, forgiveness,
private identity and conflict examples receive conservative flags. These are
editorial decisions to review with the owner, not clinical classifications.

All 134 active captions have unique normalized text, <=75 characters and at most
one emoji. Three captions per pair cover quiet/return/reply functions; seven
sensitive pairs replace reply copy with another quiet line. Original twelve IDs
and texts remain exact. Two General follow lines bring the General pool to 14.
Every active caption is compatible with at least one real Post (ignoring overrides).
General quiet copy is intentionally retained for future/synthetic forms and sparse
catalogues even though the current production queue uses only General follow copy.

33/134 captions have an emoji (24.63%): mirror 5, writing hand 13, sprig 11,
thought 4; 101 have none. A first queue audit found emoji marks overrepresented
in frequently used quiet copy. Moving ten marks to occasional return/reply copy
kept the catalogue count while reducing actual seed-42 emoji selections to 27%.
A narrow memory/lesson quiet line also caused unnecessary General fallbacks; the
copy was reworded to serve its pair accurately, then compatibility reviewed. No
spacing rule, private flag or semantic constraint was weakened to hide repetition.

## Real queue evidence

`make caption-review` evaluates the existing generator with seeds 0–99, actual
positions 1–400, and committed caption/profile snapshots: 100 queues / 40,000
selections. It writes ignored `output/caption-review.csv` (seed 42, all 400 Posts)
and `output/caption-review-metrics.json`. Nothing is written to SQLite.

| CTA role | Seed 42 count | Share | Count range across seeds 0–99 |
| --- | ---: | ---: | ---: |
| reflect | 149 | 37.25% | 136–169 |
| journal | 118 | 29.50% | 98–131 |
| comment_reply | 49 | 12.25% | 40–58 |
| save_return | 71 | 17.75% | 62–80 |
| follow | 13 | 3.25% | 13 |

Quiet roles together are always 267/400 (66.75%). Strong roles are 133/400
(33.25%). Private-comment fallbacks raise save/return above the aspirational
5–10% and reduce comments below 15–25%; privacy takes priority. This is the
specified 30/9/3 cadence, not quota-driven reassignment.

- All Posts resolve; **zero semantic violations and zero unsafe comments** in all
  40,000 selections. Tests independently check declarations for nine spread seeds.
- General selections: exactly 13 per queue (3.25%), all scheduled follow CTAs.
- Exact-caption repetition distance: minimum 6 positions in seed 42; per-seed
  minima 6–9 across all seeds. Distance 6 means five intervening Posts. No adjacent
  duplicates; targeted copy inherits the existing exact-pair spacing.
- Maximum consecutive strong-CTA run: 1 in every queue. Follow occurs at positions
  30, 60, …, 390, at least 30 apart even when the same follow line is selected.
- Emoji selections: 108/400 (27%) in seed 42; 84–121 (21–30.25%) across seeds.
  No selected caption has more than one emoji.
- Selection is stable under reversed catalogues, reversed Post input, different
  process hash seeds and unchanged global random state. Exact overrides remain
  untouched and explicitly unclassified; there are none in production currently.

## Twenty-image editorial review

Generated `make launch-review` with local ignored configuration and imported
400-Post SQLite. Inspected the actual rendered gallery, reading all twenty image
prompts beside their captions/profile/CTA/position. All JPEGs validate at 1080×1350
and are byte-identical to the merged AED-042 review images: centered handle,
unchanged prompt/highlight/footer, no new image changes or artefacts.

| Post | Seed-42 position | Caption | CTA |
| --- | ---: | --- | --- |
| AED-0047 | 231 | CAP-014 | save_return |
| AED-0085 | 384 | CAP-027 | comment_reply |
| AED-0009 | 44 | CAP-037 | reflect |
| AED-0172 | 28 | CAP-055 | journal |
| AED-0190 | 81 | CAP-062 | save_return |
| AED-0254 | 121 | CAP-084 | journal |
| AED-0257 | 212 | CAP-085 | reflect |
| AED-0030 | 115 | CAP-100 | reflect |
| AED-0343 | 62 | CAP-112 | reflect |
| AED-0365 | 199 | CAP-121 | reflect |
| AED-0063 | 137 | CAP-019 | journal |
| AED-0006 | 298 | CAP-028 | reflect |
| AED-0012 | 293 | CAP-046 | reflect |
| AED-0175 | 330 | CAP-134 | follow |
| AED-0017 | 355 | CAP-061 | journal |
| AED-0236 | 296 | CAP-076 | reflect |
| AED-0284 | 303 | CAP-096 | comment_reply |
| AED-0303 | 340 | CAP-100 | reflect |
| AED-0364 | 259 | CAP-118 | journal |
| AED-0398 | 272 | CAP-130 | journal |

Observed nine reflection, six journal, two return, two optional reply and one
follow caption; three carry a single emoji. Regret/self-worth/misunderstanding
examples do not solicit disclosure. The follow at 330 invites retention without
asking for the regret answer. No caption assumes an unrelated historical
comparison or simply repeats the image question. The tone remains quiet and the
metadata is secondary, readable and escaped. Both reply invitations are optional.

19 distinct captions appear: CAP-100 fits both contentment questions, at actual
positions 115 and 340 (225 apart). The gallery's curated visual order is explicitly
not publication order; the full CSV makes real adjacency reviewable. In actual
queue order stronger calls are separated by quiet invitations with differing
subjects. No new blocking visual/copy issue was found in this review.

## Validation and remaining acceptance

Focused policy/catalogue/metadata/gallery suite: 134 passed. `make setup` passed;
full `make check`: 691 passed, Ruff lint/format clean (81 files), Pyright zero
errors/warnings. Actual PR CI completion is recorded in the PR. Regression coverage includes strict
CSV validation, exact profile membership, synthetic statements, safe fallback,
explicit preview, stable-ID ties, exact overrides, unchanged hashtags, no network,
read-only DB, escaping, real JPEGs, deterministic reruns and safe report logs.

AED-043 stays In Progress, Completed blank, after green PR CI. Merge, green
integrated-main CI, regenerated launch review and owner review remain required.
AED-040 stays Blocked. Follow-up: synchronize approved caption/profile editorial
metadata to Sheets in a separate ticket after owner acceptance; do not redesign
Sheets or add a runtime dependency here. No publishing work was started.
