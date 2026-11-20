# EXP-QSEARCH-CHECKCAP-001

**Decision — eligible for a local screen.** The research coordinator accepted a testing-only request in its deferred backlog. Its three daily research slots were already used. The dedicated PC has no verified enrollment for this campaign. Zero match games have been recorded. Elo, its confidence interval and the draw rate remain unknown.

## Engine change

At the quiescence capture filter in `src/search.cpp`, checking captures now pass a static exchange evaluation threshold of −282 internal units. The ordinary threshold is −74. The extra 208 units represent one pawn. A forced check reply can buy time that an exchange calculation on a single square overlooks. Draw adjudication, move ordering and UCI options are unchanged. [Engine commit](https://github.com/ShadyF4RR/Stockfish/commit/fb5c6f82983e23b1ad0c6518960e8dc4a0c5b300) · [Official baseline](https://github.com/official-stockfish/Stockfish/commit/1819b9ea37588be8adb044179fb54512f87c8249)

The earlier root repetition experiment required the completed first choice to offer an immediate repetition and a searched alternative to qualify. Both engines took repetition in two of eight eligible decisions. The interrupted 41-PGN sample could establish neither a change in that decision nor an Elo gain. [Earlier report](../EXP-REPETITION-001/REPORT.md)

## Measurements

| Single-thread default bench | Baseline | Candidate |
|---|---:|---:|
| Nodes at fixed depth | 1,488,683 | 1,388,515 |
| Median nodes/s | 849,144 | 808,545 |

Four runs per binary on the same pinned Xeon logical CPU, ordered ABBABAAB. The candidate searched 6.73% fewer nodes at fixed depth and processed 4.78% fewer nodes per second. It narrowly cleared the lab's 95% median NPS floor. The two versions traverse different trees. This measurement establishes neither a CPU efficiency improvement nor a playing-strength gain. Both builds used GCC 14.2, AVX2 and the same embedded NNUE. [Bench runs](bench.json) · [Baseline build](baseline-build.json) · [Candidate build](candidate-build.json)

All 20 upstream perft cases and all 20 reproducible-search budgets passed. The 37 full-history fixtures passed independent adjudication and candidate UCI legality checks at depth 8. Cases cover threefold claims, fivefold repetition, the 50/75-move thresholds and mate precedence. The candidate saved the draw in the losing-choice fixture and continued playing in the winning-choice fixture. [Perft](perft.log) · [Reproducibility](reprosearch.log) · [Rules and root moves](rules.json)

In the four pre-existing tactical discovery positions, both engines chose the same four root moves at depth 12. Each agreed twice with baseline at depth 18. This panel supplies no evidence of improved sacrifice recognition. [Position results](tactical.json) · [Discovery positions](https://github.com/ShadyF4RR/Stockfish/blob/b222dc63dc17810bf5a9c765dd3fa75b0dd02977/research-lab/adversarial/positions.json)

## Next experiment

Complete the registered Fastchess screen of this commit on a verified worker. Use 50 distinct openings with colors reversed, 4+0.04, seed 20261120, identical engine settings and a 110-minute outer limit. Use Fastchess revision `894616028492ae6114835195f14a899f6fa237d3` and its openings.epd with SHA-256 `421a4efcddb95c3e70b1f9ad6abbeab1edcb6ab8e9ae743d94c42a5d78a6e82a`. Preserve all PGNs and apply the lab's fixed-sample paired 95% interval to the complete campaign. A promising screen qualifies for independent confirmation. Promotion and official submission remain pending. [Match request](match-request.json) · [Coordinator backlog](https://github.com/ShadyF4RR/Stockfish/blob/a1156d0bf2729048f9fe63ebb2063e4d09ea041c/queue.json)
