"""Pure anti-repetition policy; generation and persistence live elsewhere."""

from collections.abc import Iterable
from dataclasses import dataclass

from ask_every_day.domain import Post


@dataclass(frozen=True)
class QueueRules:
    """Minimum intervening posts between repeated categories/taxonomy pairs."""

    category_min_gap: int = 3
    subcategory_min_gap: int = 5

    def __post_init__(self) -> None:
        for field, minimum in (("category_min_gap", 1), ("subcategory_min_gap", 0)):
            value = getattr(self, field)
            if type(value) is not int:
                raise TypeError(f"{field} must be an integer")
            if value < minimum:
                raise ValueError(f"{field} must be at least {minimum}")


class QueueValidationError(ValueError):
    """First policy violation, with one-based positions for caller diagnostics."""

    def __init__(
        self,
        rule: str,
        post: Post,
        position: int,
        previous_position: int,
        minimum_gap: int | None = None,
    ) -> None:
        self.rule = rule
        self.post_id = post.id
        self.position = position
        self.previous_position = previous_position
        self.category = post.category
        self.subcategory = post.subcategory
        self.minimum_gap = minimum_gap
        self.actual_gap = position - previous_position - 1
        detail = (
            "ID already appeared"
            if minimum_gap is None
            else f"{self.actual_gap} intervening posts; requires {minimum_gap}"
        )
        super().__init__(
            f"{rule} at position {position} ({post.id}, "
            f"{post.category}/{post.subcategory}); previous position "
            f"{previous_position}: {detail}"
        )


def validate_sequence(posts: Iterable[Post], rules: QueueRules = QueueRules()) -> None:
    """Raise on the first violation; return None for valid finite sequences.

    IDs are globally unique; category and subcategory gaps count intervening
    posts. Subcategory identity is the exact (category, subcategory) pair. Ties
    report duplicate ID, then category spacing, then subcategory spacing.
    """
    if not isinstance(rules, QueueRules):
        raise TypeError("rules must be QueueRules")
    ids: dict[str, int] = {}
    categories: dict[str, int] = {}
    subcategories: dict[tuple[str, str], int] = {}
    for position, post in enumerate(posts, start=1):
        if not isinstance(post, Post):
            raise TypeError(f"sequence item at position {position} must be a Post")
        if post.id in ids:
            raise QueueValidationError("duplicate_id", post, position, ids[post.id])
        previous = categories.get(post.category)
        if previous is not None and position - previous - 1 < rules.category_min_gap:
            raise QueueValidationError(
                "category_gap", post, position, previous, rules.category_min_gap
            )
        pair = (post.category, post.subcategory)
        previous = subcategories.get(pair)
        if previous is not None and position - previous - 1 < rules.subcategory_min_gap:
            raise QueueValidationError(
                "subcategory_gap", post, position, previous, rules.subcategory_min_gap
            )
        ids[post.id] = position
        categories[post.category] = position
        subcategories[pair] = position
