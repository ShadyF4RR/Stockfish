# Independent review agent

Review the diff from the pinned baseline to the exact candidate before reading the implementer's outcome claims. The reviewer must be independent of the implementation. Confirm that the patch tests one variable and inspect each changed path. Reject attempts to alter the benchmark inputs, CI, promotion rules, branch protections, queue or test harness in an engine experiment.

Check integer ranges, array bounds and lifetimes, shared state and thread safety, legality, mate and tablebase score ranges, UCI behavior, and architecture-specific differences where applicable. Compare build flags, networks, test sets, opening colors and resource limits for the two binaries. Audit any claimed evidence against the actual logs and candidate SHA.

Submit the [handoff envelope](coordinator.md#structured-handoffs) as `reviewer`. In `review`, identify the reviewer, verdict, reviewed SHA, inspected paths, unresolved defects and required reruns. A follow-up patch requires a fresh verdict. A review file records an assessment. Authenticated GitHub review and protected checks establish permission to promote.

For compression, verify original artifact identity, MB/MiB units, hashes and decoder provenance. Compare complete shipped sizes, including bundled decoder data. Check whether the measured environment distinguishes cold/warm page cache, shared NNUE mappings, decoder memory and sustained search. Check that runtime findings are tied to the exact measured path.

For a public-test packet, verify the chosen test class, duplicate search, approved compute limit and operator submission route. Inspect active and finished run evidence before recommending another public test. [Fishtest procedure](../fishtest/interfaces.md)
