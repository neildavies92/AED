"""Deterministic local launch QA gallery using production images and copy policy."""

import argparse
import csv
import logging
import os
import sqlite3
import tempfile
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from html import escape
from pathlib import Path

from ask_every_day.config import load_config
from ask_every_day.content_import import load_hashtag_snapshot
from ask_every_day.copy_policy import (
    Caption,
    HashtagSet,
    select_caption,
    select_hashtag_set,
)
from ask_every_day.domain import Post, RendererConfig
from ask_every_day.jpeg_export import export_jpeg
from ask_every_day.logging_config import configure_logging
from ask_every_day.png_export import export_png
from ask_every_day.renderer import render_svg
from ask_every_day.repository import SQLiteRepository

logger = logging.getLogger(
    "ask_every_day.launch_review" if __name__ == "__main__" else __name__
)
REVIEW_IDS = (
    "AED-0047",
    "AED-0085",
    "AED-0009",
    "AED-0172",
    "AED-0190",
    "AED-0254",
    "AED-0257",
    "AED-0030",
    "AED-0343",
    "AED-0365",
    "AED-0063",
    "AED-0006",
    "AED-0012",
    "AED-0175",
    "AED-0017",
    "AED-0236",
    "AED-0284",
    "AED-0303",
    "AED-0364",
    "AED-0398",
)


@dataclass(frozen=True)
class ReviewItem:
    post: Post
    caption: str
    hashtags: HashtagSet | None


def load_captions(path: Path) -> tuple[Caption, ...]:
    """Read the existing four-column snapshot, rejecting malformed editorial rows."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream, strict=True)
        if next(rows, []) != ["id", "caption", "active", "notes"]:
            raise ValueError("Unexpected caption snapshot columns")
        captions = []
        for row in rows:
            if len(row) != 4 or row[2] not in {"TRUE", "FALSE"}:
                raise ValueError("Malformed caption snapshot row")
            captions.append(Caption(row[0], row[1], row[2] == "TRUE"))
    select_caption("AED-0001", captions)
    return tuple(captions)


def select_review(
    posts: Sequence[Post], captions: Sequence[Caption], hashtags: Sequence[HashtagSet]
) -> tuple[ReviewItem, ...]:
    """Select by immutable IDs, independently of caller ordering or queue state."""
    by_id = {post.id: post for post in posts}
    if len(by_id) != len(posts):
        raise ValueError("Duplicate Post IDs")
    if any(post_id not in by_id for post_id in REVIEW_IDS):
        raise ValueError(
            "Review Posts missing; import the approved production snapshot"
        )
    return tuple(
        ReviewItem(
            by_id[post_id],
            select_caption(post_id, captions, override=by_id[post_id].caption_override),
            select_hashtag_set(
                by_id[post_id].category,
                hashtags,
                explicit_set=by_id[post_id].hashtag_set_id,
            ),
        )
        for post_id in REVIEW_IDS
    )


def gallery_html(items: Sequence[ReviewItem]) -> str:
    """Escape every editorial value, including image alternative text."""
    cards = []
    for item in items:
        p = item.post
        tags = item.hashtags
        cards.append(f'''<article><img src="{escape(p.id)}.jpg" alt="{escape(p.prompt)}"
 width="1080" height="1350"><h2>{escape(p.id)}</h2>
<p>{escape(p.category)} / {escape(p.subcategory)}</p>
<p class="copy">{escape(item.caption)}</p>
<p>{escape(tags.id + " — " + tags.name if tags else "No hashtag set")}</p>
<p class="copy">{escape(" ".join(tags.hashtags) if tags else "")}</p>
<p>Caption override: {"yes" if p.caption_override else "no"} · Hashtag override:
{"yes" if p.hashtag_set_id else "no"}</p></article>''')
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ask Every Day — launch review</title><style>
body{margin:2rem;background:#ece9e3;color:#25231f;font:16px system-ui}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:2rem}
article{background:white;padding:1rem;min-width:0}img{width:100%;height:auto}
h2{font-size:1.1rem}p{overflow-wrap:anywhere}.copy{white-space:pre-wrap}
</style><h1>Ask Every Day — launch review</h1>
<p>REVIEW_ITEM_COUNT production Posts · curated review order, not the publication queue.
All production Posts have highlights; no-highlight coverage is synthetic in tests.</p>
<p>Owner visual and copy sign-off remains required before launch.</p><main>"""
        + "\n".join(cards)
        + "</main></html>\n"
    ).replace("REVIEW_ITEM_COUNT", str(len(items)), 1)


def generate_review(
    items: Sequence[ReviewItem], config: RendererConfig, output: Path
) -> Path:
    """Stage a full pack, then replace each file and the index last; no DB writes."""
    logger.info(
        "launch_review_started",
        extra={"event": "launch_review_started", "post_count": len(items)},
    )
    try:
        names = {f"{item.post.id}.jpg" for item in items} | {"index.html"}
        if not items or len(names) != len(items) + 1:
            raise ValueError("Review items must be nonempty and unique")
        if output.is_symlink() or (output.exists() and not output.is_dir()):
            raise ValueError("Review output must be a directory, not a symlink")
        if output.exists() and any(
            p.name not in names or p.is_symlink() or not p.is_file()
            for p in output.iterdir()
        ):
            raise ValueError("Review output contains unexpected or nonregular entries")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".launch-review-", dir=output.parent
        ) as temporary:
            staged = Path(temporary)
            for item in items:
                jpeg = export_jpeg(export_png(render_svg(item.post, config), config))
                (staged / f"{item.post.id}.jpg").write_bytes(jpeg)
            (staged / "index.html").write_text(gallery_html(items), encoding="utf-8")
            output.mkdir(exist_ok=True)
            for name in sorted(names - {"index.html"}) + ["index.html"]:
                os.replace(staged / name, output / name)
    except Exception as error:
        logger.warning(
            "launch_review_failed",
            extra={"event": "launch_review_failed", "error_type": type(error).__name__},
        )
        raise
    logger.info(
        "launch_review_completed",
        extra={"event": "launch_review_completed", "post_count": len(items)},
    )
    return output / "index.html"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the local launch review gallery"
    )
    parser.add_argument("--config", type=Path, default=Path("aed.toml"))
    parser.add_argument("--captions", type=Path, default=Path("data/captions.csv"))
    parser.add_argument("--hashtags", type=Path, default=Path("data/hashtags.csv"))
    args = parser.parse_args()
    configure_logging()
    try:
        config = load_config(args.config)
        with closing(
            sqlite3.connect(config.database_path.as_uri() + "?mode=ro", uri=True)
        ) as connection:
            posts = SQLiteRepository(connection).list_posts()
        items = select_review(
            posts, load_captions(args.captions), load_hashtag_snapshot(args.hashtags)
        )
        path = generate_review(
            items,
            config.renderer,
            args.config.absolute().parent / "output" / "launch-review",
        )
        print(f"Generated {len(items)} review items: {path}")
    except (OSError, ValueError, sqlite3.Error, csv.Error) as error:
        logger.error(
            "launch_review_failed",
            extra={"event": "launch_review_failed", "error_type": type(error).__name__},
        )
        parser.exit(
            1,
            "Launch review failed; check imported database, copy snapshots and output directory.\n",
        )


if __name__ == "__main__":
    main()
