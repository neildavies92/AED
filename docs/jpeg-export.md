# Instagram-ready JPEG — AED-034

The publishing representation is a separate, byte-only conversion:

```python
from ask_every_day.jpeg_export import export_jpeg, validate_jpeg

jpeg_bytes = export_jpeg(png_bytes)
validate_jpeg(jpeg_bytes)  # None on success, JPEGExportError on invalid output.
```

Supply PNG bytes from the existing Post → SVG → PNG pipeline. PNG export and its
local/archive purpose remain unchanged. Neither function accepts a path or URL,
writes files, creates directories or contacts an external service. Callers own
storage. No convenience writer duplicates `Path.write_bytes`.

## Fixed image and encoder contract

The boundary deliberately requires **1080×1350** input and output (4:5). Wrong
sizes fail rather than being resized or cropped silently. Source must be a single
PNG in RGB or RGBA mode. Palette, grayscale, animated PNG and color-key
transparency are rejected; explicit RGBA transparency is composited onto the
Classic warm background `(244, 240, 232)` before encoding.

| Setting | Value |
| --- | --- |
| Encoder | Pillow JPEG (direct dependency, version locked in uv.lock) |
| Output mode | RGB, opaque, no alpha channel |
| Quality | 95 |
| Chroma subsampling | 0: 4:4:4, retaining color detail around text |
| Progressive | False: baseline output |
| Optimize | False |
| Metadata | Fresh image; no EXIF, ICC profile, comment, source text or timestamps |
| Maximum output size | 8,000,000 bytes, a conservative decimal interpretation of 8 MB |

No encoder option implicitly changes dimensions or lowers quality to satisfy the
size ceiling. Oversized output fails. Encoder settings are fixed rather than an
unnecessary options framework. No encoder-generated variable timestamp is added;
repeat input yields identical bytes in the locked environment. Pillow/libjpeg
changes can change the result across environments. Upstream SVG/font/rasterization
portability limits also still apply.

## sRGB interpretation, not arbitrary color conversion

Classic's SVG uses sRGB-style CSS colors; Cairo's ordinary RGB PNG output has no
embedded ICC profile in the current pipeline. This exporter treats **untagged
project PNG pixels as sRGB** and deliberately produces an **untagged JPEG to be
interpreted as sRGB**. RGB mode alone does not establish color provenance.
Callers must not supply untagged Adobe RGB/Display P3 pixels and expect detection
or conversion. The exporter is for this known pipeline, not a general color
management service.

A valid PNG `sRGB` rendering-intent chunk (0–3) is accepted. Any embedded ICC
profile, gamma override or chromaticity chunk is rejected, even if it might be
compatible with sRGB. This conservative boundary avoids silently stripping a
conflicting profile. It does not dynamically generate ICC profiles, embed profile
timestamps, or claim to color-convert external assets. Tests cover untagged
sources, valid/invalid sRGB intent and rejection of profile overrides.

## Validation and failures

`export_jpeg` checks source format, dimensions, mode, frame count and color
metadata, verifies PNG integrity, decodes it fully, and validates the encoded
result. `validate_jpeg` checks the byte ceiling, SOI/EOI markers, decoded JPEG
format/RGB mode, dimensions and absence of ICC/EXIF/comment metadata, then loads
pixels through Pillow. This catches invalid headers, ordinary truncation, wrong
formats, modes and sizes. It is decoder-based validation, not an independent
entropy-stream auditor; Pillow can tolerate some damaged streams with an early
EOI marker. It cannot establish the color provenance of arbitrary untagged bytes.

Invalid data raises `JPEGExportError` (ValueError), chaining decoder/encoder
exceptions where relevant. Non-byte API arguments raise TypeError. No global
Pillow truncation/decompression settings are relaxed. Source dimensions are
checked before pixel decoding. Image metadata and payloads are not included in
operational logs.

Export success uses standard-library `logging` with `event=jpeg_exported`,
`format`, `width`, `height` and `byte_size`. Failure records use
`event=jpeg_export_failed` with format/dimensions and `error_type`; callers supply
logging handlers. The reusable validator itself has no logging side effects.
No dependency on the parallel logging workstream exists.

## Verification and boundaries

Run the normal commands:

```sh
make setup
make check
```

The existing Cairo workflow handles native setup for SVG/PNG integration tests;
JPEG conversion itself only imports Pillow and does not need native Cairo.
Pillow is now an explicit runtime dependency because application code directly
uses its encoder; no other image library is added.

Tests cover real Classic Post → SVG → PNG → JPEG conversion, deterministic bytes,
format/mode/dimensions, metadata, alpha compositing, corrupt input/output, limits,
no application file writes and safe logs. Existing PNG tests remain unchanged.
The manual Classic specimen was visually checked on macOS: serif text, highlight
colors, opaque warm background and branding are intact, with no clipping. The
representative JPEG is **99,323 bytes**, 1080×1350, using Pillow 12.3.0 and the
current locked pipeline. Generated previews are not committed.

[Existing Instagram prerequisite research](instagram-publishing.md) is the source
for this ticket's size/format/sRGB constraints; no fresh Meta/API work was done.
This exporter supplies bytes only. Hosting, public URLs, uploads, batch rendering
and general asset inventory validation remain outside its scope.
