# PNG export — AED-013

`ask_every_day.png_export.export_png(svg, config)` returns PNG bytes, using
`RendererConfig.width` and `height` (normally 1080×1350). The caller supplies SVG
from `render_svg` and owns all filesystem writes:

```python
from pathlib import Path

from ask_every_day.classic_prototype import SAMPLE_LINES, SAMPLE_POST
from ask_every_day.domain import RendererConfig
from ask_every_day.png_export import export_png
from ask_every_day.renderer import render_svg

config = RendererConfig()
svg = render_svg(SAMPLE_POST, config, lines=SAMPLE_LINES)
png = export_png(svg, config)
# Explicit caller-owned output; parent directory must already exist.
Path("classic.png").write_bytes(png)
```

The core creates no files or directories. No writer wrapper is needed to duplicate
`Path.write_bytes`. Use the same configuration for SVG rendering and export.
The existing Classic viewBox retains its aspect-ratio behavior at other sizes;
this does not introduce a responsive layout. The background comes from the SVG:
Classic at its native 4:5 ratio is opaque warm off-white; otherwise-transparent
SVG regions remain transparent. PNG is a local/archive artifact, **not** an
Instagram-ready upload. JPEG export belongs to AED-034.

## Installation and failure behavior

CairoSVG is a Python dependency installed in the project `.venv` by `make setup`.
Cairo and libffi are native host dependencies, installed separately. On macOS,
development expects Homebrew and these packages:

```sh
brew install cairo libffi
make setup
make check
```

Canonical `make setup` and `make test` (therefore `make check`) use
`scripts/with-cairo.sh`. On Darwin it obtains `brew --prefix cairo` and
`brew --prefix libffi`, verifies their library directories, and prepends their
keg-specific `lib` paths to `DYLD_FALLBACK_LIBRARY_PATH` immediately before
executing `uv`. No Intel/Apple Silicon installation path is hard-coded. Existing
fallback paths are appended; Make carries its supplied value through a temporary
non-DYLD variable because macOS protected process boundaries can strip DYLD
variables. The temporary transport variable is removed before the command runs.
No caller-supplied DYLD setting is needed. Missing prerequisites stop before
Python runs with the installation command above; nothing is installed automatically.

For a direct diagnostic or another local Cairo command, reuse the same helper:

```sh
sh scripts/with-cairo.sh uv run python -c "import cairosvg; print('CairoSVG OK')"
```

Future PNG/JPEG Make targets can use the same `CAIRO_RUN` command prefix.
`make prototype` produces SVG and needs no Cairo setup. Lint, formatting and
Pyright commands also retain their existing execution. On non-Darwin platforms,
the helper executes the supplied command without invoking Homebrew or setting
DYLD variables. Install native Cairo as appropriate for that platform; see
[official installation requirements](https://cairosvg.org/documentation/).
This helper configures development commands, not application runtime or global
shell settings. Cross-machine font/rasterization caveats below still apply.

Pillow appears transitively through CairoSVG's raster-image support; this project
adds no direct Pillow dependency or Pillow-based processing.

Malformed XML, non-SVG roots, entity/document-type declarations and converter
exceptions raise `PNGExportError` with the original cause where relevant. A
missing native runtime produces an installation hint. Wrong API argument types
raise `TypeError`. CairoSVG remains in safe mode (`unsafe=False`); external
resources are disabled and may be omitted by CairoSVG rather than failing. Supply
self-contained renderer output. This is not a general asset validator or a sandbox
for arbitrary untrusted uploads, huge images or pathological SVG.

## Reproducibility and visual review

Repeated conversion of identical SVG/configuration is tested byte-for-byte within
one environment. Cross-machine PNG identity is **not** guaranteed: Cairo, font
availability/selection, rasterizer versions and font caches can affect results.
System/native font discovery may read installed resources and maintain native
caches; the exporter itself has no storage API or hidden output writes.

Classic uses system serif/sans-serif font stacks. No system fonts are bundled.
CairoSVG has typography limitations including font-family selection and advanced
spacing; browser SVG and Cairo output need visual comparison, not an assumption
of pixel identity. See [official rendering limitations](https://cairosvg.org/documentation/#text-and-fonts).
The hand-set specimen deliberately uses main's existing manual lines; automatic
wrapping and highlight layout remain AED-011/AED-012 work.

The 1080×1350 hand-set specimen was rendered and visually inspected on macOS
with CairoSVG 2.9.1 / Cairo 1.18.6: warm opaque background, three unclipped serif
lines, muted highlights and both branding treatments were present. This verifies
that specimen on this host, not every prompt or font environment.

`make check` covers valid PNG structure/CRC/compressed data, exact dimensions,
Classic opacity, same-environment determinism, errors, absence of application
output files, safe external-resource handling and structured logs. No binary
fixtures are committed. Standard-library log records use `event`, `format`,
`width`, `height`, plus `error_type` on failure. Raw SVG/PNG and exception payloads
are never logged. This boundary has no Post or path, so it does not invent those
fields; later orchestration can add them.
