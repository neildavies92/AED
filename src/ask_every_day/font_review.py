"""Offline font provenance and disposable Classic specimens; no publication state."""

import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from ask_every_day.content_import import load_snapshot
from ask_every_day.domain import RendererConfig
from ask_every_day.jpeg_export import export_jpeg
from ask_every_day.png_export import export_png
from ask_every_day.renderer import render_svg

FONT_ROOT = Path("/usr/local/share/fonts/aed")
STYLES = {
    "regular": (80, 0),
    "bold": (200, 0),
    "italic": (80, 100),
    "bold_italic": (200, 100),
}


def inventory(
    *,
    require_mounted: bool = False,
    families: tuple[str, ...] = ("Liberation Serif", "Lato"),
) -> list[dict]:
    records = []
    for family in families:
        for style, (weight, slant) in STYLES.items():
            result = subprocess.run(
                [
                    "fc-match",
                    "-f",
                    "%{family}\t%{style}\t%{fontversion}\t%{file}\t%{weight}\t%{slant}\t%{index}\n",
                    f"{family}:weight={weight}:slant={slant}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            fields = result.stdout.strip().split("\t")
            if len(fields) != 7:
                raise ValueError("font_metadata_invalid")
            (
                resolved,
                actual_style,
                version,
                filename,
                actual_weight,
                actual_slant,
                index,
            ) = fields
            path = Path(filename)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024 * 1024:
                raise ValueError("font_file_invalid")
            exact = family.casefold() in [
                f.strip().casefold() for f in resolved.split(",")
            ]
            correct_style = int(actual_weight) == weight and int(actual_slant) == slant
            mounted = path.resolve().is_relative_to(FONT_ROOT.resolve())
            if require_mounted and not (exact and correct_style and mounted):
                raise ValueError("licensed_family_or_style_unavailable")
            records.append(
                {
                    "requested_family": family,
                    "requested_style": style,
                    "family": resolved,
                    "style": actual_style,
                    "fontconfig_version": version,
                    "face_index": index,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "exact_family": exact,
                    "style_matches": correct_style,
                    "private_mount": mounted,
                }
            )
    return records


def compare(reference: Path, candidate: Path) -> dict:
    with Image.open(reference) as first, Image.open(candidate) as second:
        if first.size != (1080, 1350) or second.size != first.size:
            raise ValueError("comparison_dimensions")
        a, b = first.convert("RGB"), second.convert("RGB")
        difference = ImageChops.difference(a, b)
        return {
            "pixel_equal": difference.getbbox() is None,
            "mean_absolute_channel_difference": ImageStat.Stat(difference).mean,
            "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
            "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        }


def render(
    output: Path,
    data: Path,
    background: str,
    *,
    require_mounted: bool = False,
    body_family: str = "Liberation Serif",
    header_family: str = "Lato",
) -> dict:
    if output.exists():
        raise ValueError("new_review_directory_required")
    report = inventory(
        require_mounted=require_mounted,
        families=tuple(dict.fromkeys((body_family, header_family))),
    )
    posts = load_snapshot(
        data / "prompts.csv", data / "taxonomy.csv", data / "hashtags.csv"
    )
    post = next(p for p in posts if p.id == "AED-0311")
    config = RendererConfig(background_color=background)
    import re

    if any(
        re.fullmatch(r"[A-Za-z0-9 -]{1,60}", family) is None
        for family in (body_family, header_family)
    ):
        raise ValueError("font_family_invalid")
    svg = render_svg(post, config)
    svg = svg.replace("Liberation Serif, 'Times New Roman', serif", body_family)
    svg = svg.replace("Lato, Arial, sans-serif", header_family)
    jpeg = export_jpeg(export_png(svg, config))
    output.mkdir(mode=0o700)
    (output / "AED-0311-linux.jpg").write_bytes(jpeg)
    (output / "AED-0311.svg").write_text(svg)
    (output / "fonts.json").write_text(json.dumps(report, indent=2) + "\n")
    for path in output.iterdir():
        os.chmod(path, 0o600)
    return {
        "specimen": "AED-0311",
        "dimensions": [1080, 1350],
        "font_metadata_recorded": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "render", "compare"))
    parser.add_argument("--require-mounted", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("/opt/aed/data"))
    parser.add_argument("--background", default="#EDF4FB")
    parser.add_argument("--body-family", default="Liberation Serif")
    parser.add_argument("--header-family", default="Lato")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            result = inventory(
                require_mounted=args.require_mounted,
                families=tuple(dict.fromkeys((args.body_family, args.header_family))),
            )
        elif args.command == "render" and args.output is not None:
            result = render(
                args.output,
                args.data_dir,
                args.background,
                require_mounted=args.require_mounted,
                body_family=args.body_family,
                header_family=args.header_family,
            )
        elif args.command == "compare" and args.reference and args.candidate:
            result = compare(args.reference, args.candidate)
        else:
            parser.error("required output or comparison paths missing")
        print(json.dumps(result))
    except Exception:
        parser.exit(
            1,
            "Font review blocked; check font mount, requested styles and review paths.\n",
        )


if __name__ == "__main__":
    main()
