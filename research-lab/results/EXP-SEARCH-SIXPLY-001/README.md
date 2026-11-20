# EXP-SEARCH-SIXPLY-001 · Quiet move ordering

**Outcome: inconclusive.** 4 wins, 9 draws, 3 losses across eight color-reversed pairs. Candidate Elo estimate +21.7, 95% paired Hoeffding interval [-507.6, unbounded]. This screen provides insufficient evidence for official STC or a strength claim.

One line in `MovePicker::generate_and_score<QUIETS>()` halves the contribution from the six-ply continuation history when ranking quiet moves. Its update rule, all other terms, the network and search settings are unchanged. The independent reviewer approved the exact candidate for a bounded local screen. [Candidate commit](https://github.com/ShadyF4RR/Stockfish/commit/f2f67475aa6722e73f473d1203f8d833a65bc35f) · [Baseline](https://github.com/official-stockfish/Stockfish/commit/1819b9ea37588be8adb044179fb54512f87c8249) · [Review](review.json)

## Correctness and benchmark

Both engines used GCC 14.2.0, `ARCH=x86-64-avx2`, the same upstream build flags and network `nn-bddd9d57eb87.nnue`. The baseline was the previously verified binary. Parallel build jobs differed (baseline four, candidate two). UCI initialization and restricted move search passed. The candidate passed all 20 upstream perft positions (standard and Chess960) and reproducibility checks at all 20 upstream node budgets. Debian's `awk` symlink target was missing from the initial sandbox during reproducibility testing. The full reproducibility script passed unchanged after the target became available. [Manifests and raw measurements](bench.json) · [Validation log](validation.log)

| Engine | Default bench nodes, both runs | NPS, first run | NPS, second run |
|---|---:|---:|---:|
| Baseline | 1,488,683 | 861,506 | 856,056 |
| Candidate | 1,377,908 | 815,330 | 796,939 |

The default depth-13 single-thread bench used 16 MiB hash, fresh processes pinned to logical CPU 0, and baseline/candidate/candidate/baseline order. The candidate searched 7.44% fewer nodes on this suite. Its mean NPS was 6.13% lower across two runs. The search trees differ and the cloud host was shared. This sample cannot establish a speed regression or improvement. We did not instrument first-quiet cutoff counts.

## Match

The predeclared local screen used eight openings from the pinned Fastchess EPD suite, one game per color at each opening, seed 20261120, `4+0.04` seconds, one thread per engine, 16 MiB hash and one concurrent game. Fastchess recorded pentanomial counts of `[0, 1, 5, 2, 0]` in ascending candidate points per pair. The 95% interval uses the predeclared fixed-sample bound. No public Fishtest job or promotion was requested. [Protocol](plan.json) · [Match settings and compute](match.json) · [Games](games.pgn) · [Match log](fastchess.log)

The first PGN verifier rejected one input position. The opening book records h6 as the en-passant square in round six, although White has no pawn on g5 and cannot capture there. Fastchess recorded the same position with a dash. Replaying all 100 original book FENs through the pinned baseline's UCI `d` command returned the exact full FEN for every recorded start. The eight starts were distinct, each pair reversed colors, and no game was replaced or rerun. The published statistics were recomputed from the original PGN and reproduced again with the corrected general-purpose FEN validator. [Original verifier output](initial-validator.json)
