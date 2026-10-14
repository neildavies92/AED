# Anti-repetition policy — AED-017

```python
from ask_every_day.queue_rules import QueueRules, validate_sequence

rules = QueueRules(category_min_gap=3, subcategory_min_gap=5)
validate_sequence(posts, rules)  # None on success; QueueValidationError on failure.
```

The default constraints are hard requirements, not preferences:

- Post IDs cannot repeat anywhere in the supplied sequence.
- The same category needs at least **three intervening posts**: position 1 can
  repeat at position 5, not position 4. Categories can never be adjacent.
- The same `(category, subcategory)` pair needs at least **five intervening posts**:
  position 1 can repeat at position 7, not position 6.

Positions are one-based. Gap = current position − previous position − 1.
Both spacing requirements apply independently. Different subcategories in the
same category still obey category spacing. `Growth / Change` and
`Life Audit / Change` are different taxonomy nodes; the bare name is not an ID.
Names are compared exactly without case folding or trimming, consistent with
Post values. Taxonomy membership and editorial approval are upstream concerns.

`QueueRules` is frozen. Both fields require integers, rejecting booleans, strings
and floats. Category gap must be at least 1, preserving the no-adjacency rule;
subcategory gap may be 0 to disable additional pair spacing while category
spacing still applies. A smaller pair gap than category gap is valid but redundant.
Larger nonnegative gaps are accepted as policy settings, not a promise that any
particular library can satisfy them.

`validate_sequence` consumes a finite iterable of Post values once and raises
`QueueValidationError` (a ValueError) on the first violation. Attributes include
`rule` (`duplicate_id`, `category_gap`, `subcategory_gap`), `post_id`, `position`,
`previous_position`, `category`, `subcategory`, `actual_gap` and `minimum_gap`.
The latter is None for duplicate IDs. The message contains useful positions and
identity without the prompt body. At the same position, duplicate ID takes
precedence, then category, then pair spacing. Incorrect argument/item types raise
TypeError. Empty, single-item and short nonrepeating sequences are valid.

Validation has no writes, database access, randomness or logging. It does not
mutate the input, reorder posts, create QueueEntry values, check cyclic wraparound
or know previously published history. AED-018 must decide which complete candidate
sequence to validate; it must not validate chunks independently when constraints
must cross their boundaries. Runtime is linear in supplied sequence length, with
maps tracking seen IDs and the most recent category/pair positions.

## Feasibility of the current library

The versioned Approved library has ten categories with forty posts each; each
category has four taxonomy pairs with ten posts each. A test-only constructive
certificate cycles the ten categories for each of four pairs and repeats this ten
times. Every category recurs at distance 10 (nine intervening posts); every pair
recurs at distance 40 (39 intervening posts). All 400 unique IDs are included and
the complete sequence passes the default validator.

This proves the current finite library admits an arrangement under the default
rules. It is not the final publication queue, a randomized generator, or proof
that every partial prefix can be extended. Changing taxonomy/counts or gaps
requires reassessing feasibility. The certificate lives only in tests and is not
persisted. No necessary-count heuristic is presented as a sufficient proof.

`make setup` and `make check` cover boundaries, global duplicates, latest-occurrence
spacing, pair identity, deterministic failures, configuration and the full-library
certificate. No dependencies or operational command are added. Queue generation
(AED-018), scheduling, persistence and publication transitions remain separate.
