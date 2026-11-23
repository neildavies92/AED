"""Audit published palette assignments and canonical JPEGs offline."""

import argparse
import hashlib
import json
import logging
from collections import Counter
from io import BytesIO
from pathlib import Path

from PIL import Image

from ask_every_day.config import load_config
from ask_every_day.domain import AppConfig, PublicationStatus
from ask_every_day.logging_config import configure_logging
from ask_every_day.media_staging import _jpeg
from ask_every_day.palette import Assignment, next_assignment
from ask_every_day.publication_assets import AssetError, _published, read_ledger
from ask_every_day.repository import Repository, open_repository

_LOG = logging.getLogger(__name__)


def inspect_jpeg(
    repo: Repository, config: AppConfig, post_id: str, assignment: Assignment
) -> tuple[str, list[str], bool | None]:
    try:
        data = _jpeg(repo, config, post_id)
    except FileNotFoundError:
        return "missing", [], None
    except (OSError, ValueError, SyntaxError):
        return "unreadable_or_invalid", [], None
    expected = bytes.fromhex(assignment.background[1:])
    with Image.open(BytesIO(data)) as image:
        # The Classic template's corners are clear of text and decoration.
        width, height = image.size
        samples = [
            image.getpixel(point)
            for point in (
                (8, 8),
                (width - 9, 8),
                (8, height - 9),
                (width - 9, height - 9),
            )
        ]
    colours = []
    matches = True
    for sample in samples:
        assert isinstance(sample, tuple) and len(sample) == 3
        colours.append("#" + bytes(sample).hex().upper())
        # JPEG rounds RGB channels. Allow small differences from the SVG fill.
        if max(abs(a - b) for a, b in zip(sample, expected, strict=True)) > 3:
            matches = False
    return hashlib.sha256(data).hexdigest(), colours, matches


def audit(config: AppConfig) -> dict:
    """Audit every success, then report the most recent 20 in completion order."""
    rows = []
    issues: list[dict[str, object]] = []
    legacy_count = 0
    tracked: list[Assignment] = []
    stale = []
    previous = None
    previous_colour = None
    missing_assets = 0
    seen_posts: set[str] = set()
    with open_repository(config, read_only=True) as repo, repo.transaction():
        history = repo.list_publications()
        published = _published(repo)
        try:
            ledger = read_ledger(config)
        except AssetError:
            ledger = None
            issues.append({"code": "palette_ledger_invalid"})
        if ledger is None and published and not issues:
            issues.append({"code": "palette_ledger_missing"})
        legacy = set(ledger.legacy_publications) if ledger else set()
        if ledger:
            previous_colour = ledger.previous_background
            for attempt_id in sorted(legacy - {item.id for item in published}):
                issues.append(
                    {"code": "legacy_attempt_missing", "attempt_id": attempt_id}
                )
        for item in published:
            publication = item.publication
            assert publication.published_at is not None
            post_id = publication.post_id
            asset = ledger.assets.get(post_id) if ledger else None
            row = {
                "post_id": post_id,
                "attempt_id": item.id,
                "published_at": publication.published_at.isoformat(),
                "tracking": "untracked",
                "sequence": None,
                "bag_number": None,
                "slot": None,
                "background": None,
                "jpeg": "unverified",
            }

            def flag(code: str, attempt_id: int = item.id) -> None:
                issues.append({"code": code, "attempt_id": attempt_id})

            if post_id in seen_posts:
                flag("duplicate_published_post")
            seen_posts.add(post_id)
            if item.id in legacy:
                legacy_count += 1
                row["tracking"] = "legacy"
                if tracked or asset is not None:
                    flag("contradictory_legacy_assignment")
            elif asset is None:
                if ledger:
                    flag("untracked_publication")
            else:
                current = asset.assignment
                tracked.append(current)
                row.update(
                    tracking="tracked",
                    sequence=current.sequence,
                    bag_number=current.bag_number,
                    slot=current.slot,
                    background=current.background,
                )
                index = len(tracked) - 1
                if current.sequence != index + 1:
                    flag("noncontiguous_sequence")
                if (current.bag_number, current.slot) != divmod(index, 3):
                    flag("bag_or_slot_out_of_order")
                if current.background == previous_colour:
                    flag("consecutive_background_repeat")
                if previous and index % 3:
                    if (current.bag, current.palette_key) != (
                        previous.bag,
                        previous.palette_key,
                    ):
                        flag("bag_changed_midway")
                else:
                    if previous and current.bag == previous.bag:
                        flag("consecutive_bag_order_repeat")
                if index % 3 == 0 and current.palette_key == config.palette.key:
                    expected = (
                        next_assignment(
                            config.palette,
                            previous,
                            previous_background=ledger.previous_background
                            if ledger
                            else None,
                        )
                        if previous is None or previous.slot == 2
                        else None
                    )
                    if expected is not None and current.bag != expected.bag:
                        flag("deterministic_bag_mismatch")
                previous, previous_colour = current, current.background
                checkpoint = repo.get_checkpoint(item.id)
                if checkpoint and checkpoint.asset_sha256 != asset.jpeg_sha256:
                    flag("checkpoint_jpeg_hash_mismatch")
                result, samples, background_matches = inspect_jpeg(
                    repo, config, post_id, current
                )
                if result == "missing":
                    missing_assets += 1
                    row["jpeg"] = "missing"
                elif result != asset.jpeg_sha256:
                    flag(
                        result
                        if result == "unreadable_or_invalid"
                        else "canonical_jpeg_hash_mismatch"
                    )
                    row["jpeg"] = "mismatch"
                else:
                    row["jpeg"] = "verified"
                if background_matches is False:
                    flag("background_mismatch")
                row["sampled_backgrounds"] = samples
            rows.append(row)
        if ledger:
            known = {post.id for post in repo.list_posts()}
            for post_id in sorted(ledger.assets.keys() - known):
                issues.append({"code": "orphaned_asset_assignment", "post_id": post_id})
            prepared = (ledger.assets.keys() & known) - seen_posts
            try:
                expected = next_assignment(
                    config.palette,
                    previous,
                    previous_background=ledger.previous_background,
                )
            except ValueError:
                expected = None
            stale = sorted(
                post for post in prepared if ledger.assets[post].assignment != expected
            )
            try:
                if ledger != read_ledger(config):
                    issues.append({"code": "palette_ledger_changed_during_audit"})
            except AssetError:
                issues.append({"code": "palette_ledger_changed_during_audit"})
    complete_bags = 0
    for offset in range(0, len(tracked) - 2, 3):
        first, second, third = tracked[offset : offset + 3]
        if {item.background for item in (first, second, third)} != set(first.bag):
            issues.append({"code": "unbalanced_bag", "bag_number": offset // 3})
        else:
            complete_bags += 1
    return {
        "status": "inconsistent"
        if issues
        else "incomplete"
        if missing_assets or not tracked
        else "verified",
        "published_count": len(rows),
        "legacy_count": legacy_count,
        "tracked_count": len(tracked),
        "untracked_count": len(rows) - legacy_count - len(tracked),
        "complete_balanced_bags": complete_bags,
        "tracked_colour_counts": dict(Counter(item.background for item in tracked)),
        "missing_canonical_jpegs": missing_assets,
        "stale_preparations": stale,
        "unresolved_attempts": [
            item.id
            for item in history
            if item.publication.status is PublicationStatus.PUBLISHING
        ],
        "issues": issues,
        "latest_publications": rows[-20:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    configure_logging()
    try:
        result = audit(load_config(args.config))
    except Exception:
        _LOG.error(
            "palette_audit_unavailable", extra={"event": "palette_audit_unavailable"}
        )
        result = {
            "status": "unavailable",
            "issues": [{"code": "palette_audit_unavailable"}],
        }
    print(json.dumps(result, indent=2))
    return {"verified": 0, "incomplete": 2}.get(result["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
