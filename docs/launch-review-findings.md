# Launch review findings — 17 October 2026

Generated the 20-Post pack documented in [launch review](launch-review.md) using the
actual production SVG → Cairo PNG → JPEG pipeline on the development Mac. Inspected
all 20 JPEGs together in a contact sheet and AED-0030, AED-0236 and AED-0364 at full
size. A second agent independently inspected the full gallery and these three
full-resolution JPEGs and agreed with the nonblocking assessment. Read all 20 resolved captions and category hashtag mappings. These are agent
QA observations, **not owner sign-off** or a claim about audience performance.

## Blocking before first live post

The initial agent inspection found no concrete rendering/copy defect. During
owner review on 18 October, the original top `ASK EVERY DAY` and duplicate bottom
handle were judged redundant. The requested branding refinement is AED-042,
recorded below. Owner visual/copy acceptance remains pending.

## Desirable iteration after launch

- AED-0364 ends with the single-word line “explicit?” and occupies six lines;
  AED-0063 ends with “suits you?”. They remain readable and contained, but the owner
  may prefer future editorial attention to short final lines. This is a preference,
  not evidence of clipping or broken layout.
- The curated order groups shorter examples before longer ones to expose density
  differences. Caption CAP-008 repeats on consecutive AED-0236/AED-0284; this is the
  documented ID rotation, not a proposed live sequence. Evaluate repetition in the
  actual publication queue later without changing this review's policy.
- “Be honest with yourself on this one” on AED-0030 is more directive than the
  quiet “enough” question. It is acceptable within the existing pool, but owner
  preference about that tone is a useful review question.

## Acceptable as-is in this inspection

- The initial pack had consistent warm background, left alignment, serif scale,
  olive highlights and top brand/bottom sign-off/handle. Two-line AED-0030 has much
  more open space than six-line AED-0364, while the text remains vertically balanced.
- Multi-line emphasis on AED-0236/0364 stays legible and restrained; punctuation
  remains attached. No clipping, overlapping brand/footer or obvious JPEG artefacts
  were seen at the inspected sizes.
- Captions provide brief reflection/writing invitations; the save/return wording
  does not demand likes or comments. No medical claims, pseudo-therapy promises or
  promotional claims were identified in the selected combinations.
- Category-specific sets match all ten categories. Their shared journaling tags
  are editorially coherent; no reach/performance claim is made.

## Acceptance gate

Owner: open the generated gallery, inspect the JPEGs and copy as a set, and record
approval or concrete requested changes. No owner approval has yet been recorded.
AED-040 remains Blocked on AED-042 and owner sign-off, with Completed blank.
AED-042 remains In Progress through PR CI, pending merge, integrated-main CI and
owner review of the regenerated pack. Neither ticket is complete from agent QA.

## AED-042 branding refinement — 18 October 2026

The owner selected one top attribution: centered `@ask.every.day` and a centered
short underline, with a subtle sage `@` and stronger dark account name. The bottom
handle is removed; `A QUESTION TO SIT WITH.` retains its position and styling.
This is a branding change, not a prompt/template redesign.

Implemented x=540/y=147 with one middle-anchored SVG text element, 23px
Arial/Helvetica and 1.5px letter spacing. The spans use `#8D957F` and `#343B35`;
the existing 56px/2px sage rule is centered from x=512 to 568 at y=190.
The deliberate SVG/PNG specimen was refreshed. CairoSVG's anchored-span overlap
required a narrow, measured header-only adaptation at PNG export; the canonical
SVG remains centered and prompt rendering is untouched. See [renderer](renderer.md).

Regenerated all 20 production JPEGs with `make launch-review` and inspected the
actual gallery as a set, plus AED-0030, AED-0236 and AED-0364 at full resolution.
The handle and rule appear centered and consistent. The `@` is noticeably lighter
but readable; the account name remains subordinate to the question. Removing the
duplicate handle leaves a quiet, intentional footer rather than an accidental gap.
No clipping, prompt shift or obvious JPEG artefact was observed. Before/after
comparisons for those three Posts confirm identical prompt SVG groups and exact
PNG pixels across the prompt/footer-phrase region.

No additional concrete launch-blocking visual issue was found in this regenerated
set. Earlier short-final-line and caption preferences remain unchanged and outside
this ticket. No new follow-up ticket is needed. These are agent observations;
**owner sign-off on the new branding is still required**.
