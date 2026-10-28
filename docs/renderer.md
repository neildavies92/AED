# SVG rendering — AED-010

```python
from ask_every_day.domain import Post, RendererConfig
from ask_every_day.renderer import render_svg

post = Post(
    "AED-0001", "What feels like home?", "Self-awareness", "Identity", "Classic"
)
svg = render_svg(post, RendererConfig())
```

`render_svg` returns an SVG string. It reads packaged templates independently of
the working directory and performs no filesystem writes or network access. The
same post, configuration, optional manual layout, and template version produce
identical bytes. It uses strict Jinja variables and automatic XML escaping; the
result must parse as an SVG document. No timestamps or environment metadata enter
the output. System fonts still affect eventual visual display, not SVG bytes.

Classic is the only supported template. `RendererConfig.template` selects it;
`Post.template` must match, so a content/configuration conflict fails explicitly.
Unsupported/mismatched templates, inconsistent manual layouts, missing/broken
packaged templates, and invalid XML produce `RenderError` (a `ValueError`). Template
or XML failures retain their original exception as the cause. Callers supply
validated `Post` and `RendererConfig` instances; other types raise `TypeError`.

Configuration width and height set the SVG viewport. Classic retains its
1080×1350 design coordinate system (`viewBox`); SVG's default aspect-ratio behavior
scales it proportionally, letterboxing if a different aspect ratio is requested.
This preserves typography and spacing without inventing responsive layouts.

## Classic branding — AED-042

The single attribution is a centered `@ask.every.day` at x=540, baseline y=147,
with `text-anchor="middle"`. One Lato/Arial text element uses 23px type and
1.5px letter spacing; adjacent spans give `@` the blue accent `#2F608C` and
`ask.every.day` the primary blue `#1C3651`, without a heavier weight.
The 56px underline runs from (512,190) to (568,190), with the blue accent stroke
and 2px width. Centering follows the complete SVG text, not a guessed left offset.

The footer contains only `A QUESTION TO SIT WITH.` at x=112/y=1138, retaining
its 20px sans-serif type, 1.5px spacing and `#1C3651` fill. The duplicate bottom
handle is removed. Prompt layout/highlights, background, canvas and accessible
description are unchanged. The deliberate SVG/PNG specimen in `docs/design/`
reflects this canonical branding; prompt layout policy is unchanged.

CairoSVG currently centers adjacent anchored spans independently
([upstream issue 317](https://github.com/Kozea/CairoSVG/issues/317)). The PNG boundary
therefore adapts only the `classic-brand` element in its in-memory conversion copy:
the same Cairo backend/font/scale measures the complete handle, then positions the
left-anchored runs with their boundary spacing preserved. The returned canonical
SVG keeps x=540 and `text-anchor="middle"`; no font-width guess, prompt modification,
dependency or general text-layout replacement is introduced. Raster regression
compares this split handle with an unsplit centered control at full and half size.

## Automatic Classic layout — AED-011

Without `lines`, `layout_prompt` automatically wraps the prompt using fixed,
conservative character advances at the unchanged 76px type size. The usable
advance width is 856 design pixels from x=112. Up to six 102px-spaced baselines
are centered on y=688. There is no shrinking, truncation, hyphenation or manual
per-prompt break list. All 400 Approved prompts in the versioned corpus fit.

The pure layout function returns immutable `LayoutLine` values with exact `text`,
zero-based half-open `start`/`end` source offsets, estimated `width`, and `baseline`.
Concatenating the text reproduces the prompt exactly, including leading,
multiple and trailing spaces. Breaks occur only at spaces; punctuation remains
attached to words, including separate punctuation-only tokens. SVG `xml:space`
preserves those spaces. These source offsets drive exact highlight intersections
without changing line breaks or positions (AED-012).

The current supported alphabet is printable ASCII, matching the whole approved
corpus. Non-ASCII, tabs/newlines/control characters, blank text, overwide words
(including their spacing) and more than six lines fail with `LayoutError`.
`render_svg` reports these as actionable `RenderError` values retaining the cause.
Explicit manual specimen lines retain the previous renderer contract.

### Metrics and portability

The estimates use fixed hundredths of an em: spaces 32, narrow glyphs 35,
wide glyphs 100, remaining uppercase 80, and other glyphs 60. They deliberately
ignore the width savings from Classic's negative letter spacing. No local fonts
are queried, so line breaks and SVG output are reproducible across environments.
These are conservative heuristics, not precise font measurements or a guarantee
for arbitrary installed serif fallbacks. Changing the font, font size or spacing
requires recalibration; production font portability remains a separate decision.

On 14 October 2026, all 400 SVGs were measured in local Chrome with macOS Georgia:
maximum actual line width 704.75px; all ink fit x=104..968 and y=350..970. The 8px
left allowance covers normal serif glyph overhang around the x=112 anchor, well
inside the canvas. Line counts: 1 two-line, 50 three-line, 232 four-line,
113 five-line, and 4 six-line prompts. The representative prompt and longest
prompt (AED-0236) were visually inspected at 1080×1350. Browser/font-dependent
measurements are manual validation evidence, not brittle pixel tests.

## Explicit specimen compatibility

The keyword-only `lines` argument accepts a tuple of `(prefix, highlight, suffix)`
string triples. Their space-joined text must reproduce the exact prompt, and the
space-joined highlighted fragments must reproduce its optional highlight. This
is the existing hand-set specimen convention, retained for design review,
not a general layout engine. Callers using manual lines must review visual fit.

`make prototype` uses this same renderer with the original three-line specimen,
then writes `output/classic-prototype.svg`. Its output remains identical to the
approved SVG in `docs/design/`. Output paths and directory creation belong only
to the helper, not the core renderer. The existing Makefile remains sufficient.

The standard-library logger emits success/failure records with structured `extra`
fields: `event`, `post_id`, `template`, `width`, `height`, `line_count` after layout,
and failure `error_type`. Pure layout functions do not log.
The caller controls logging handlers/formatting. Full prompt text and exception
payloads are not logged. No dependencies were added.

Deferred: renderer regression suite (AED-014) and batch/output orchestration.
SVG-to-PNG export is documented separately in [PNG export](png-export.md).

## Exact highlight styling — AED-012

Normal `render_svg(post, config)` locates `post.highlight_text` once in the
original prompt, then intersects that source range with each automatic line's
unchanged half-open offsets. Prefix, highlighted fragment, and suffix concatenate
to the exact original line, including spaces and punctuation. Highlight fragments
across lines concatenate to the exact configured phrase. No line-local searches,
word normalization, extra spaces or per-prompt definitions are used.

Matching is case-sensitive and exact. `None` means no highlight. Missing or blank
values are rejected by the domain and highlight helper; multiple occurrences,
including overlapping matches, are rejected as ambiguous. This stricter rendering
policy leaves the existing domain contract unchanged. All 400 Approved prompts
pass it without editorial changes. Renderer failures raise `RenderError`, with a
`HighlightError` cause; errors contain no prompt or highlight payload.

The blue accent `#2F608C` tspan changes fill only. The primary blue prompt, left
anchor x=112, centered baseline block, font, line spacing, footer and canvas remain
unchanged. XML escaping stays in Jinja. Pure highlight helpers do not log; renderer
records add `highlight_segment_count` (including any space-only intersections).

The explicit `lines` seam remains solely for the existing historical specimen
byte-compatibility contract. It still requires exact prompt/highlight reproduction
and now obeys the same unique-match policy. Production callers use automatic
layout; the specimen helper and template need no separate highlight algorithm.

Focused tests cover line boundaries/crossings, offsets, whitespace, punctuation,
XML-sensitive text, ambiguity (including overlaps), absent highlights, deterministic
output, all 400 prompts' geometry, and a real SVG-to-PNG integration. Short, sample
and longest-post PNGs were visually checked on macOS using the supported Cairo
wrapper; no clipping or alignment change was observed. No dependencies added.

## Configurable colours — AED-051

`RendererConfig` supplies validated `background_color`, `text_color` and
`accent_color` values. Defaults are Ice Blue `#E2EFF9`, primary `#1C3651` and
accent `#2F608C`. Only fills and the underline stroke change; all geometry,
typography, exact highlights and XML escaping retain their existing contracts.
`load_config` reads the central `[palette]` table. The standalone specimen
uses a fixed Ice Blue background and remains byte-deterministic.

Publication rotation belongs to [asset preparation](background-rotation.md),
which chooses a colour from actual successful history and passes explicit renderer
values. Rendering an arbitrary Post does not advance that sequence. Both deliberate
SVG and PNG design references were refreshed for this intentional palette change.

## Accepted editorial typography

The owner selected Liberation Serif prompts and Lato supporting text on 28 October
2026. The Classic template now requests those families, retaining serif/sans-serif
fallbacks for local development. Docker installs fonts-lato 2.015-1 from its existing
Debian snapshot alongside Liberation. Native smoke verifies regular/bold/italic/
bold-italic family/style resolution. Code/font changes require fresh scheduler
authorization; previously published canonical assets are never regenerated.


Publication preparation and approval use the [canonical Docker renderer](canonical-manual-publishing.md).
All eight accepted font faces are verified by version and file hash. A fresh
in-memory raster must match cached PNG/JPEG bytes; matching SVG alone does not
prove typography. Native development fallbacks cannot authorize a publication.
