# Queue regression coverage (AED-019)

`tests/test_queue_regression.py` extends AED-018 without changing the generator.
The stress fixture generates the current Approved 400-Post library once for each
integer seed **0 through 99**, using the unchanged default search budget and rules.
Every result has independent assertions for length, exact ID multiplicity, unchanged
Post values, preceding category windows and preceding taxonomy-pair windows, plus
`validate_sequence`. Both Growth/Change and Life Audit/Change retain ten entries.

Seeds 0, 49 and 99 are additionally generated from reversed and deterministically
shuffled input (shuffle seed 2718), compared to their cached normal-order results:
**106 new full-library generations per suite run**. The observed stress set produced
100 distinct orders; the regression only requires observable diversity, not an
arbitrary uniqueness percentage or a promise that each seed has a unique result.
All seeds are fixed and failures include reproducible context.

Nine small table cases use exhaustive permutations as an independent feasibility
oracle, covering empty/single input, imbalanced category/pair counts, tied maxima,
exact spacing boundaries, alternative rules and the duplicated Change name.
A duplicate ID carrying different editorial values must also fail before search.
AED-018's existing tests retain bounded exhaustion and deterministic restart
recovery coverage; those examples are not copied here.

New persistence checks cover a real successful publication followed by regeneration,
connection closure/reopening, exclusion of the published Post, unchanged history,
consecutive replacement positions and stable persisted order on repeat generation.
A SQLite authorizer rejects the actual COMMIT after queue writes, exercising
rollback of the complete transaction and verification through a reopened database.
No success log may escape the failed commit. Existing AED-018 tests retain
ambiguous-history, generation-failure and mid-write failure coverage.

On the development Mac, the 100-generation fixture took approximately 2.3 seconds
and the new 16-test focused suite about 2.5 seconds. These are observations, not
wall-clock pass/fail assertions. No generator defect was found, no search budget
was raised, and no seed was removed.

Run `.venv/bin/pytest tests/test_queue_regression.py -s` after `make setup` for
stress measurements, and plain `make check` for the complete quality gate.
No dependencies or operational logging changes are needed for tests/docs.
AED-037 remains deferred: constraints apply within the newly generated queue,
without a prefix from previously published posts. No scheduling, CLI, retry,
publication-client or content-refresh behavior is added.
