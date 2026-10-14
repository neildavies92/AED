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

## Temporary layout boundary

Without `lines`, the prompt is a single unhighlighted line. This preserves all
content but does **not** promise visual fit or highlight styling. No automatic
wrapping or fitting is implemented here.

The keyword-only `lines` argument accepts a tuple of `(prefix, highlight, suffix)`
string triples. Their space-joined text must reproduce the exact prompt, and the
space-joined highlighted fragments must reproduce its optional highlight. This
is the existing hand-set specimen convention, a temporary seam for AED-011/012,
not a general layout engine. Callers using manual lines must review visual fit.

`make prototype` uses this same renderer with the original three-line specimen,
then writes `output/classic-prototype.svg`. Its output remains identical to the
approved SVG in `docs/design/`. Output paths and directory creation belong only
to the helper, not the core renderer. The existing Makefile remains sufficient.

The standard-library logger emits success/failure records with structured `extra`
fields: `event`, `post_id`, `template`, `width`, `height`, and failure `error_type`.
The caller controls logging handlers/formatting. Full prompt text and exception
payloads are not logged. No dependencies were added.

Deferred: deterministic wrapping (AED-011), robust highlight integration
(AED-012), SVG-to-PNG export (AED-013), and batch/output orchestration.
