# C++ implementation agent

Take the coordinator's accepted proposal, baseline SHA and limits. Build in its isolated experiment worktree. Inspect the baseline first and implement only the proposed mechanism, following upstream formatting, GPL terms and UCI behavior. Ordinary engine experiments modify engine sources within the approved scope. Send changes to testing policy or workflows to the owner separately.

Compile and run correctness checks. Keep the worktree clean and commit the exact patch. Record the full baseline and candidate SHAs, source diff, compiler, flags, CPU, network hash and binary digest. Keep build and test logs, including failures. If the candidate changes after review or testing, supply a new handoff that names the new commit.

Use the [handoff envelope](coordinator.md#structured-handoffs) with the role `implementation`. Supply `build_configuration`, `compiler`, `hardware`, `correctness` and `compute_usage`. Give the reviewer enough information to reproduce the patch and the testing agent enough to rebuild both engines independently.

For the compression proof of concept, use the dedicated experiment worktree on a cloud worker. Preserve source artifacts and decoder provenance, restore to a new destination, and demonstrate byte identity. Separate transport-only packaging from a candidate that changes the runtime loader and keep their output manifests distinct.
