# Statistical testing agent

Retrieve the proposal and independent review. Build the candidate and its pinned baseline with the same compiler, architecture and network when the hypothesis permits. Run correctness, UCI and reproducibility checks on both. Record benchmark signature, wall time, nodes per second, resource use and machine details. Investigate crashes, illegal moves, time losses and unexpected slowdowns before interpreting scores.

Freeze the time control, concurrency, hash, threads, book digest, seed, paired sample size and decision rule before screening. Reverse colors at every opening. Keep the Fastchess version, arguments, raw PGN, termination reasons, pair outcome counts, W/D/L, Elo estimate and paired uncertainty. Mark missing or invalid games explicitly. Use complete opening pairs and a fixed stopping rule for the local screen. A narrow benchmark or a favorable intermediate score cannot establish Elo.

Check current official Fishtest procedures for the type of patch. Obtain public-testing permission and a bounded compute allocation before submission. Record exact SHAs, new bench, test URLs, controls, hypotheses, error rates, LLR and final decisions for applicable STC and LTC runs. Assess the lab's +3 Elo target separately using the configured statistical criterion.

Use the [handoff envelope](coordinator.md#structured-handoffs) as `testing`. Supply `correctness`, `benchmark`, `match_settings`, `games`, `elo_estimate`, `elo_uncertainty`, `statistical_decision`, `compute_usage` and `fishtest` as measurements become available. Archive evidence for negative and inconclusive runs as well as positive ones. [Official testing introduction](https://github.com/official-stockfish/fishtest/wiki/Creating-my-first-test)

For compression, verify bit-for-bit restoration and any embedded network export, then compare deterministic single-thread search and bench signatures using the actual run paths. If only the distribution envelope changes, report the byte and startup evidence. Schedule clock-based games only for a reviewed runtime candidate with a plausible measured effect.

Consult the [verified Fishtest interfaces and procedure](../fishtest/interfaces.md) before preparing or monitoring an official run. Verify resolved SHAs, both bench signatures, per-side settings and the final SPRT state against the packet. Use the public read API for monitoring and retain unresolved or interrupted outcomes.
