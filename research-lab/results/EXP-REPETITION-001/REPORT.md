# EXP-REPETITION-001

**Decision: inconclusive.** Candidate 8 wins, 27 draws, 6 losses in 41 games. The observed Elo estimate is +16.96. The registered comparison did not complete cleanly. A directional confidence claim is unavailable.

Fastchess logged 43 finishes in the planned 48 games (8 wins, 29 draws, 6 losses). The environment restarted with 41 complete PGNs saved. The W/D/L table and Elo estimate use those 41 verified games. No replacement games were played.

The candidate searches a second root line when its completed first choice immediately permits a repetition claim. An inferior or incomplete alternative preserves the drawing line. The only changed engine file is `src/search.cpp`. Both revisions produced the same 1,488,683-node default bench. Candidate bench rates were 815,269 and 805,128 nodes/s on a shared cloud host. Those two measurements do not establish a speed gain.

| Clock | Wins | Draws | Losses | Draw rate |
|---|---:|---:|---:|---:|
| 4+0.04 | 4 | 17 | 3 | 70.8% |
| 12+0.12 | 4 | 10 | 3 | 58.8% |

The match used the pinned Fastchess binary, identical engine settings, 12 full move histories, and both colors at each clock. The report retains all PGNs and any abnormal terminations. The interval clusters by opening family and adjusts for the overall, three stratum and two clock comparisons.

- Candidate selected the immediate repetition in 2/8 eligible root decisions.
- Baseline selected the immediate repetition in 2/8 eligible root decisions.

Validation findings: Cloud environment restarted before the worker could close this cell. No games replayed.

[Engine change](https://github.com/ShadyF4RR/Stockfish/commit/293da879bd27da174728cda7b5b46d8011f2a3ac) · [Match protocol](MATCH-PLAN.md) · [Recorded match](matches.json) · [Bench](build-bench.json)

The final binary passed 37 draw-rule checks, 20 upstream perft cases and 20 reproducible-search node budgets. The local Expect installation required its interpreter path for perft and on PATH for reproducible search. The registered search and test inputs were unchanged.
