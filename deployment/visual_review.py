"""Render Mac references beside native Linux smoke images without publication."""

import argparse
import json
import platform
import shutil
from pathlib import Path

from PIL import Image

from ask_every_day.content_import import load_snapshot
from ask_every_day.domain import AppConfig
from ask_every_day.jpeg_export import export_jpeg
from ask_every_day.png_export import export_png
from ask_every_day.publication_assets import themed_renderer
from ask_every_day.renderer import render_svg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--linux-dir", type=Path, default=Path("output/aed-053-review"))
    parser.add_argument("--output", type=Path, default=Path("output/aed-056-review"))
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("Generate Mac references on macOS; Linux images come from smoke")
    source = Path("data")
    posts = load_snapshot(
        source / "prompts.csv", source / "taxonomy.csv", source / "hashtags.csv"
    )
    args.output.mkdir(parents=True, exist_ok=True)
    cards = []
    for key in ("AED-0395", "AED-0236"):
        post = next(p for p in posts if p.id == key)
        linux = args.linux_dir / f"{key}-linux.jpg"
        with Image.open(linux) as image:
            assert image.size == (1080, 1350)
            pixel = image.convert("RGB").getpixel((0, 0))
            # JPEG quantization may differ by one; select the actual brand swatch.
            config = AppConfig(Path("unused"), Path("unused"), "@ask.every.day")

            def distance(colour):
                return sum(
                    (pixel[i] - int(colour[1 + 2 * i : 3 + 2 * i], 16)) ** 2
                    for i in range(3)
                )

            background = min(config.palette.backgrounds, key=distance)
        renderer = themed_renderer(config, background)
        (args.output / f"{key}-mac.jpg").write_bytes(
            export_jpeg(export_png(render_svg(post, renderer), renderer))
        )
        shutil.copyfile(linux, args.output / linux.name)
        cards.append(
            f'<section><h2>{key} · {background}</h2><div class="pair"><figure><img src="{key}-mac.jpg" alt="Mac reference"><figcaption>macOS reference · Georgia / Arial</figcaption></figure><figure><img src="{key}-linux.jpg" alt="Linux candidate"><figcaption>Linux candidate · DejaVu Serif / Liberation Sans</figcaption></figure></div></section>'
        )
    (args.output / "index.html").write_text(
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>AED-056 rendering review</title><style>body{margin:40px auto;max-width:1250px;padding:0 24px;background:#f4f7fa;color:#1c3651;font:17px/1.5 system-ui}h1{font-size:36px}h2{font-size:22px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}figure{margin:0}img{width:100%;display:block}figcaption{padding:12px 0}section{margin:36px 0}.notice{padding:18px;background:#e2eff9;border-left:4px solid #2f608c}@media(max-width:700px){.pair{grid-template-columns:1fr}}</style><h1>Ask Every Day · Linux rendering review</h1><p class="notice">Owner decision pending. Compare body weight, line spacing, highlights and header weight at full resolution. Both are 1080 × 1350. No Instagram publication or production asset changes.</p><p>The pinned Linux runtime uses open font fallbacks. Pixel and typography parity are not claimed. Accept the Linux appearance explicitly, or lawfully mount matching fonts and repeat this review.</p>"""
        + "".join(cards)
        + "<p>Generated locally from reviewed content. Canonical transferred images retain their original bytes.</p></html>"
    )
    print(
        json.dumps(
            {
                "review": str(args.output.resolve() / "index.html"),
                "owner_acceptance": "pending",
            }
        )
    )


if __name__ == "__main__":
    main()
