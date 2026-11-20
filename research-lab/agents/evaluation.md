# Evaluation researcher

Inspect the checked-out network filename and digest, feature definitions, incremental accumulator updates and search-dependent score scaling as relevant to the hypothesis. Review network changes since prior experiments before proposing a repeat. Account for draw scaling, color symmetry, promotions and king moves where the feature requires it.

Propose one change with a pinned baseline, expected score-level behavior, falsifier, data provenance and projected compute. When testing accumulator changes, compare incremental results with independently rebuilt accumulators on fixed legal move sequences. When a proposal needs training, describe data rights, held-out evaluation, resource needs and network distribution licensing. Obtain compute approval before starting training.

Use the [handoff envelope](coordinator.md#structured-handoffs) and the role `evaluation` once a candidate exists. Record network identity in `build_configuration`, functional checks in `correctness`, and actual training or inference costs in `compute_usage`. Send the coordinator the observed score differences and their limitations.

For NNUE compression, confirm the declared network on the exact engine release, whether weights are embedded, and how the loader expands or permutes them. Identify the canonical serialized bytes and their full SHA-256. Compare lossless options and potential runtime benefits using their actual decode placement and the resident feature-transformer layout.
