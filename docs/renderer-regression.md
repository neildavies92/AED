# Classic renderer regression coverage

`tests/test_renderer_regression.py` selects six Approved posts by immutable ID
from the editorial snapshot. Normal `render_svg(post, config)` drives every case;
no manual layout or copied prompt fixtures are used.

| ID | Reason | Lines | Nonempty highlight spans |
| --- | --- | --- | --- |
| AED-0030 | Short prompt, highlight within one line | 2 | 1 |
| AED-0001 | Representative question, crossing highlight | 3 | 2 |
| AED-0209 | Medium prompt and four-line geometry | 4 | 2 |
| AED-0236 | Longest current prompt | 5 | 2 |
| AED-0238 | Apostrophe and adjacent question-mark punctuation | 5 | 2 |
| AED-0364 | Six-line vertical limit, highlight across three lines | 6 | 3 |

Six structural cases protect viewport, design colors/type, exact text and
highlight reconstruction, explicit baselines, vertical centering, left alignment,
span structure, footer and repeatable SVG bytes. One small derivative tests XML
metacharacters absent from approved content without editing the snapshot.

Six raster cases run real SVG → PNG → JPEG conversion and independently decode
both formats with Pillow. They check full image validity, 1080×1350 dimensions,
opaque RGB, nonempty artwork, absent EXIF/ICC/comments, JPEG size below 8 MB and
no output files in an isolated working directory. Color interpretation remains
the existing untagged sRGB pipeline contract; these tests do not infer color
provenance from arbitrary images.

Run `make check` for canonical verification, or run only this suite using:

```sh
sh scripts/with-cairo.sh uv run pytest tests/test_renderer_regression.py -q
```

The suite adds 13 focused cases without another 400-post rendering loop. Existing
corpus tests retain broader layout/highlight coverage. No raster byte equality,
pixel snapshots, packaged fonts or screenshot infrastructure is introduced.
Actual system-font rasterization may vary between macOS and Linux; deterministic
SVG structure and valid image representations are the portable contract.

When an intentional design or editorial change affects these six cases, review
its output and update the explicit expected geometry deliberately. Do not refresh
expectations merely to suppress failures. No application logging changes are
needed because this ticket adds tests and documentation only.
