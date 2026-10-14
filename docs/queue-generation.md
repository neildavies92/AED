# Constrained shuffled queue (AED-018)

`generate_queue(posts, seed=..., rules=QueueRules(), max_attempts=32)` is pure:
no database, global random state, filesystem access or logging. It returns every
input Post exactly once, or raises `QueueGenerationError`; it never returns a
partial queue. Duplicate IDs are rejected before search. Empty input returns `[]`.

Rules come directly from `QueueRules`: defaults require three intervening posts
between categories and five between `(category, subcategory)` pairs. Growth/Change
and Life Audit/Change remain separate pairs. See [queue rules](queue-rules.md).

## Search and reproducibility

Input is sorted by immutable Post ID before using a locally owned
`random.Random(seed)`. An explicit integer seed is required. Identical content,
rules, seed and implementation version give the same order regardless of input
ordering. Different seeds vary tie-breaking; they need not produce different
orders for tiny datasets with few possible solutions.

Each construction greedily prioritizes the largest remaining category and then
pair counts, randomizing ties. Candidates must satisfy current cooldowns. Forward
checks reject choices when remaining repetitions cannot fit their earliest usable
slot plus required spacing, or the maximum frequency/tie-count lower bound exceeds
remaining slots. These checks are necessary bounds, not a complete feasibility
proof for the combined rules.

A dead end restarts from all input Posts using the continuing owned random stream.
There are at most `max_attempts` constructions, each with at most N positions and
N candidate checks per position; no recursion or unbounded retry. The default is
32. Each capacity check visits at most the distinct category/pair count. Rules
are never weakened, Posts dropped or duplicates inserted. The completed order is
validated with authoritative `validate_sequence` before returning.

Immediate capacity failures report proven spacing impossibility; search-budget
exhaustion explicitly means no solution was found within the budget, **not** proof
that none exists. Safe error context includes total/remaining counts, configured
gaps, seed and attempts, never prompt bodies. A caller may deliberately choose a
larger positive budget; unusual datasets can still exhaust it.

The current 400-Post library is tested using seeds `0, 1, 2, 42, 2026, -17, 99999`,
including reversed input order. Measured local generation for the first five seeds
was about 0.04 seconds per queue on the development Mac; this is an observation,
not a portable timing assertion. Small tests also demonstrate deterministic
restart recovery and bounded failure where independently feasible category/pair
counts cannot be combined into a valid sequence.

## Eligibility and persistence

`generate_and_store_queue(repository, seed=..., regenerate=False, rules=...,
max_attempts=32)` selects and persists through the existing repository interface.
Runtime Posts are assumed to originate from the approved snapshot import; no
second approval status is invented. Only Posts without publication attempts are
eligible (derived Ready or Queued). Publishing, Published and Failed Posts are
excluded. Conflicting queue/history membership, ambiguous attempt history or
queue references to missing Posts fail closed using existing publication policy.

A nonempty queue is rejected by default. Passing `regenerate=True` explicitly
replaces it in full using all currently eligible Posts, including existing queued
ones. It does not merge positions. Output positions are consecutive from 1 and
match generated order exactly. All ineligible content may legitimately yield an
empty queue. Generation does not mutate attempt history or perform lifecycle
transitions beyond storing queue membership.

The service owns a repository transaction around selection, generation and queue
replacement. SQLite therefore serializes eligibility reads and writes; a generation
or persistence error restores the original queue. Holding the write reservation
through generation is deliberate at this tiny local scale. Do not wrap this
service in another repository transaction. Example:

```python
from ask_every_day.config import load_config
from ask_every_day.queue_service import generate_and_store_queue
from ask_every_day.repository import open_repository

with open_repository(load_config()) as repository:
    entries = generate_and_store_queue(repository, seed=42)
```

The service logs one committed result with seed, counts and gaps; failures log
only safe error type/seed. No candidate-loop logs or content bodies. Standard
library `logging` extras work independently of centralized logging configuration.

Run plain `make setup` and `make check`; no manual Cairo environment overrides
are needed. No dependencies or CLI were added. Scheduling, publication retries,
remote publishing, queue consumption and post-publication history lookback rules
remain outside this ticket. The constraints apply within the generated queue;
AED-017 does not currently define a lookback prefix from past publications.
