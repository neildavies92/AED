"""Bounded seeded queue construction, independent of persistence and logging."""

import random
from collections import Counter
from collections.abc import Sequence

from ask_every_day.domain import Post
from ask_every_day.queue_rules import QueueRules, validate_sequence


class QueueGenerationError(ValueError):
    """A complete valid queue was not found; no partial queue is returned."""

    def __init__(
        self,
        reason: str,
        total: int,
        remaining: int,
        rules: QueueRules,
        seed: int,
        attempts: int,
    ) -> None:
        self.total = total
        self.remaining = remaining
        self.seed = seed
        self.attempts = attempts
        super().__init__(
            f"{reason}; total={total}, remaining={remaining}, "
            f"category_gap={rules.category_min_gap}, subcategory_gap={rules.subcategory_min_gap}, "
            f"seed={seed}, attempts={attempts}"
        )


def _fits[K](
    counts: Counter[K], last: dict[K, int], next_position: int, remaining: int, gap: int
) -> bool:
    for key, count in counts.items():
        if count:
            wait = max(0, last.get(key, -gap - 1) + gap + 1 - next_position)
            if wait + (count - 1) * (gap + 1) + 1 > remaining:
                return False
    maximum = max(counts.values(), default=0)
    tied = sum(count == maximum for count in counts.values())
    return maximum == 0 or (maximum - 1) * (gap + 1) + tied <= remaining


def generate_queue(
    posts: Sequence[Post],
    *,
    seed: int,
    rules: QueueRules = QueueRules(),
    max_attempts: int = 32,
) -> list[Post]:
    """Return every input once, or fail after a bounded greedy search.

    Normalize by ID, prioritize scarce category/pair capacity, randomize ties,
    and prune choices whose remaining cooldown windows cannot fit. Restarts use
    the same locally owned RNG stream. Budget exhaustion is not proof of impossibility.
    """
    if type(seed) is not int:
        raise TypeError("seed must be an integer")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if not isinstance(rules, QueueRules):
        raise TypeError("rules must be QueueRules")
    if any(not isinstance(post, Post) for post in posts):
        raise TypeError("posts must contain Post values")
    baseline = sorted(posts, key=lambda post: post.id)
    total = len(baseline)
    if len({post.id for post in baseline}) != total:
        raise QueueGenerationError("Duplicate post IDs", total, total, rules, seed, 0)
    initial_categories = Counter(post.category for post in baseline)
    initial_pairs = Counter((post.category, post.subcategory) for post in baseline)
    if not _fits(initial_categories, {}, 0, total, rules.category_min_gap) or not _fits(
        initial_pairs, {}, 0, total, rules.subcategory_min_gap
    ):
        raise QueueGenerationError(
            "Spacing capacity is impossible", total, total, rules, seed, 0
        )
    rng = random.Random(seed)
    best_remaining = total
    for attempt in range(1, max_attempts + 1):
        remaining_posts = baseline.copy()
        categories, pairs = initial_categories.copy(), initial_pairs.copy()
        last_categories: dict[str, int] = {}
        last_pairs: dict[tuple[str, str], int] = {}
        ordered: list[Post] = []
        while remaining_posts:
            position = len(ordered)
            candidates = [
                post
                for post in remaining_posts
                if position
                - last_categories.get(post.category, -rules.category_min_gap - 1)
                > rules.category_min_gap
                and position
                - last_pairs.get(
                    (post.category, post.subcategory), -rules.subcategory_min_gap - 1
                )
                > rules.subcategory_min_gap
            ]
            ranked = sorted(
                candidates,
                key=lambda post: (
                    -categories[post.category],
                    -pairs[(post.category, post.subcategory)],
                    rng.random(),
                ),
            )
            selected: Post | None = None
            for candidate in ranked:
                category = candidate.category
                pair = (category, candidate.subcategory)
                categories[category] -= 1
                pairs[pair] -= 1
                previous_category = last_categories.get(category)
                previous_pair = last_pairs.get(pair)
                last_categories[category] = last_pairs[pair] = position
                count = len(remaining_posts) - 1
                fits = _fits(
                    categories,
                    last_categories,
                    position + 1,
                    count,
                    rules.category_min_gap,
                ) and _fits(
                    pairs, last_pairs, position + 1, count, rules.subcategory_min_gap
                )
                if fits:
                    selected = candidate
                    break
                categories[category] += 1
                pairs[pair] += 1
                if previous_category is None:
                    del last_categories[category]
                else:
                    last_categories[category] = previous_category
                if previous_pair is None:
                    del last_pairs[pair]
                else:
                    last_pairs[pair] = previous_pair
            if selected is None:
                break
            ordered.append(selected)
            remaining_posts.remove(selected)
        best_remaining = min(best_remaining, len(remaining_posts))
        if not remaining_posts:
            validate_sequence(ordered, rules)
            return ordered
    raise QueueGenerationError(
        "Search budget exhausted", total, best_remaining, rules, seed, max_attempts
    )
