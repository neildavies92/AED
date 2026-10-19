"""Offline queue/caption audit with ignored CSV and aggregate JSON review outputs."""

import argparse
import csv
import json
import logging
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from ask_every_day.caption_catalog import (
    load_captions,
    load_profiles,
    validate_reachability,
)
from ask_every_day.content_import import load_snapshot
from ask_every_day.copy_policy import (
    Caption,
    CaptionProfile,
    CaptionSelection,
    compatible,
    select_caption,
)
from ask_every_day.domain import Post
from ask_every_day.logging_config import configure_logging
from ask_every_day.queue_generation import generate_queue

logger = logging.getLogger("ask_every_day.caption_review")

EMOJI_PALETTE = ("🪞", "✍️", "🌿", "💭")


def audit_queue(
    posts: Sequence[Post],
    captions: Sequence[Caption],
    profiles: dict[str, CaptionProfile],
    *,
    seed: int,
) -> tuple[list[tuple[Post, CaptionSelection]], dict[str, object]]:
    ordered = generate_queue(posts, seed=seed)
    selections = [
        (p, select_caption(p, captions, profiles[p.id], position=n))
        for n, p in enumerate(ordered, 1)
    ]
    by_id = {c.id: c for c in captions}
    semantic_failures = sum(
        not compatible(by_id[s.caption_id], profiles[p.id])
        for p, s in selections
        if s.caption_id is not None
    )
    last: dict[str, int] = {}
    distances = []
    run = maximum_run = unsafe = 0
    for position, (post, selected) in enumerate(selections, 1):
        if selected.caption in last:
            distances.append(position - last[selected.caption])
        last[selected.caption] = position
        run = (
            run + 1
            if selected.cta_role in {"comment_reply", "save_return", "follow"}
            else 0
        )
        maximum_run = max(maximum_run, run)
        unsafe += (
            selected.cta_role == "comment_reply"
            and not profiles[post.id].public_reply_ok
        )
    metrics: dict[str, object] = {
        "seed": seed,
        "posts": len(ordered),
        "cta_roles": dict(
            sorted(Counter(s.cta_role or "override" for _, s in selections).items())
        ),
        "general_fallbacks": sum(s.general_fallback for _, s in selections),
        "emoji_selections": sum(
            any(e in s.caption for e in EMOJI_PALETTE) for _, s in selections
        ),
        "minimum_repetition_distance": min(distances, default=None),
        "maximum_strong_run": maximum_run,
        "unsafe_comments": unsafe,
        "semantic_failures": semantic_failures,
    }
    return selections, metrics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit local caption selection across real queues"
    )
    parser.add_argument(
        "--seeds", type=int, default=100, help="Evaluate seeds 0 through N-1, plus 42"
    )
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds must be positive")
    configure_logging()
    try:
        _write_review(args.seeds)
    except (OSError, ValueError, csv.Error) as error:
        logger.error(
            "caption_review_failed",
            extra={
                "event": "caption_review_failed",
                "error_type": type(error).__name__,
            },
        )
        parser.exit(1, "Caption review failed; check snapshots and output directory.\n")


def _write_review(seed_count: int) -> None:
    data = Path("data")
    posts = load_snapshot(
        data / "prompts.csv", data / "taxonomy.csv", data / "hashtags.csv"
    )
    captions = load_captions(data / "captions.csv", data / "taxonomy.csv")
    profiles = load_profiles(data / "caption_profiles.csv", [p.id for p in posts])
    validate_reachability(posts, captions, profiles)
    results = []
    output = Path("output")
    output.mkdir(exist_ok=True)
    for seed in sorted(set(range(seed_count)) | {42}):
        selections, metrics = audit_queue(posts, captions, profiles, seed=seed)
        results.append(metrics)
        if seed == 42:
            with (output / "caption-review.csv").open(
                "w", encoding="utf-8", newline=""
            ) as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    (
                        "position",
                        "post_id",
                        "prompt",
                        "category",
                        "subcategory",
                        "prompt_form",
                        "response_mode",
                        "time_focus",
                        "public_reply_ok",
                        "caption_id",
                        "caption",
                        "cta_role",
                        "general_fallback",
                    )
                )
                for position, (post, selection) in enumerate(selections, 1):
                    profile = profiles[post.id]
                    writer.writerow(
                        (
                            position,
                            post.id,
                            post.prompt,
                            post.category,
                            post.subcategory,
                            profile.prompt_form,
                            profile.response_mode,
                            profile.time_focus,
                            profile.public_reply_ok,
                            selection.caption_id,
                            selection.caption,
                            selection.cta_role,
                            selection.general_fallback,
                        )
                    )
    (output / "caption-review-metrics.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Audited {len(results)} queues; review CSV and aggregate JSON in output/")

    logger.info(
        "caption_review_completed",
        extra={
            "event": "caption_review_completed",
            "post_count": len(posts),
        },
    )


if __name__ == "__main__":
    main()
