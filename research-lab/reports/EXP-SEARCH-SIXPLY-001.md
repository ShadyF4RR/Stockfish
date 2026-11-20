# EXP-SEARCH-SIXPLY-001

Reducing the six-ply continuation history weight in quiet move ordering may improve cutoff selection and playing strength.

**Outcome** inconclusive. 4 wins, 9 draws, 3 losses across eight color-reversed pairs. Candidate Elo estimate +21.7, 95% paired Hoeffding interval \[-507.6, unbounded\].

| Measure | Recorded value |
|---|---|
| Baseline commit | 1819b9ea37588be8adb044179fb54512f87c8249 |
| Candidate commit | f2f67475aa6722e73f473d1203f8d833a65bc35f |
| Changed source files | src/movepick.cpp |
| Build | \{&quot;ARCH&quot;: &quot;x86-64-avx2&quot;, &quot;COMP&quot;: &quot;gcc&quot;, &quot;CXX&quot;: &quot;g++&quot;, &quot;target&quot;: &quot;build&quot;\} |
| Correctness | \{&quot;binary\_sha256&quot;: &quot;b7adc6946a0bb975a183e8d32a69439c6f165f0a6350233856efaf85aeac036e&quot;, &quot;commit&quot;: &quot;f2f67475aa6722e73f473d1203f8d833a65bc35f&quot;, &quot;notes&quot;: &quot;Full perft and UCI passed on first launch. Exposed /etc/alternatives for awk in a fresh sandbox and reran the upstream reproducibility test unchanged.&quot;, &quot;perft&quot;: &quot;passed&quot;, &quot;perft\_positions&quot;: 20, &quot;reprosearch&quot;: &quot;passed&quot;, &quot;reprosearch\_node\_budgets&quot;: 20, &quot;uci&quot;: &quot;passed&quot;\} |
| Games played | 16 |
| Planned games | 16 |
| Candidate wins / draws / losses | 4 / 9 / 3 |
| Estimated Elo | 21.74306492903707 |
| Confidence interval | -507.5606494350888 to unbounded above \(fixed-sample paired Hoeffding\) |
| Started | 2026-11-20T21:47:56+00:00 |
| Last recorded update | 2026-11-20T21:55:32+00:00 |

## Performance

- Baseline mean bench 858781 NPS
- Candidate mean bench 806134 NPS

## Evidence

- [README.md](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-SEARCH-SIXPLY-001/README.md)
- [plan.json](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-SEARCH-SIXPLY-001/plan.json)
- [bench.json](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-SEARCH-SIXPLY-001/bench.json)
- [validation.log](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-SEARCH-SIXPLY-001/validation.log)
- [games.pgn](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-SEARCH-SIXPLY-001/games.pgn)
- [fastchess.log](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-SEARCH-SIXPLY-001/fastchess.log)
- [match.json](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-SEARCH-SIXPLY-001/match.json)
- [initial-validator.json](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-SEARCH-SIXPLY-001/initial-validator.json)
