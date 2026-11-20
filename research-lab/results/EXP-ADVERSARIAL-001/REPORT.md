# Adversarial Stockfish match screen

**Both objective strength and practical win-rate effects remain inconclusive.**

Completed 23 of 32 registered cells without invalid games. The manifest records 192 of 192 games. Fastchess recorded 34 abnormal terminations. The registered decision rule requires valid results in every cell.

Each invalid or unobserved game is allowed any legal result in the sample ranges below. The 95% simultaneous envelope adds fixed-sample opening-family uncertainty to those limits. Values are percentage-point differences. A positive value favors the candidate.

| Split | Endpoint | Compatible sample range | 95% envelope |
|---|---|---:|---:|
| holdout | H2H score minus 50% | [+0.0, +0.0] | [-50.0, +50.0] |
| holdout | Independent-panel win delta | [-22.2, +22.2] | [-100.0, +100.0] |
| holdout | Independent-panel score delta | [-22.2, +22.2] | [-100.0, +100.0] |
| discovery | H2H score minus 50% | [+4.2, +4.2] | [-50.0, +50.0] |
| discovery | Independent-panel win delta | [-30.6, +19.4] | [-100.0, +100.0] |
| discovery | Independent-panel score delta | [-29.2, +20.8] | [-100.0, +100.0] |

On the holdout, candidate–Stockfish games in complete normal pairs recorded 1/10/1 candidate W/D/L. For the matching opening/color quartets against independent engines, the candidate recorded 27/1/0 and baseline recorded 27/1/0. These counts omit unmatched openings.

For the registered direct comparison, the uncertainty envelope covers the entire 0–100% score range, corresponding to an unbounded logistic Elo interval. The 3 holdout families provide too little independent evidence to distinguish a small gain from a regression.

**Raw holdout win/draw/loss percentages**, including forfeits:

| Panel | Arm | Wins | Draws | Losses |
|---|---|---:|---:|---:|
| head to head | candidate | 8.3% | 83.3% | 8.3% |
| head to head | baseline | 25.0% | 66.7% | 8.3% |
| independent | candidate | 97.2% | 2.8% | 0.0% |
| independent | baseline | 97.2% | 2.8% | 0.0% |

### Observed cells

W/D/L includes every recorded outcome, including forfeits. Invalid counts follow the candidate/baseline order. The complete-pair column counts openings with two normal candidate games for direct Stockfish play, or four normal games for a practical comparison. The final column shows candidate score minus 50% against Stockfish and candidate-minus-baseline outright win rate against each independent engine. These selected-subset differences are descriptive.

| Split | Opponent | Clock | Candidate W/D/L | Baseline W/D/L | Invalid C/B | Complete pairs | Difference (pp) |
|---|---|---|---:|---:|---:|---:|---:|
| holdout | stockfish | 1+0.01 | 0/5/1 | 1/5/0 | 0/0 | 3/3 | -8.3 |
| holdout | stockfish | 3+0.03 | 1/5/0 | 2/3/1 | 0/0 | 3/3 | +8.3 |
| holdout | fruit | 1+0.01 | 6/0/0 | 6/0/0 | 0/0 | 3/3 | +0.0 |
| holdout | fruit | 3+0.03 | 6/0/0 | 6/0/0 | 0/0 | 3/3 | +0.0 |
| holdout | ethereal | 1+0.01 | 6/0/0 | 6/0/0 | 4/4 | 1/3 | +0.0 |
| holdout | ethereal | 3+0.03 | 6/0/0 | 6/0/0 | 4/4 | 1/3 | +0.0 |
| holdout | weiss | 1+0.01 | 5/1/0 | 5/1/0 | 0/0 | 3/3 | +0.0 |
| holdout | weiss | 3+0.03 | 6/0/0 | 6/0/0 | 0/0 | 3/3 | +0.0 |
| discovery | stockfish | 1+0.01 | 1/5/0 | 2/3/1 | 0/0 | 3/3 | +8.3 |
| discovery | stockfish | 3+0.03 | 0/6/0 | 2/4/0 | 0/0 | 3/3 | +0.0 |
| discovery | fruit | 1+0.01 | 6/0/0 | 6/0/0 | 0/0 | 3/3 | +0.0 |
| discovery | fruit | 3+0.03 | 6/0/0 | 6/0/0 | 0/0 | 3/3 | +0.0 |
| discovery | ethereal | 1+0.01 | 6/0/0 | 6/0/0 | 4/3 | 1/3 | +0.0 |
| discovery | ethereal | 3+0.03 | 6/0/0 | 6/0/0 | 5/5 | 0/3 | — |
| discovery | weiss | 1+0.01 | 5/1/0 | 6/0/0 | 0/0 | 3/3 | -16.7 |
| discovery | weiss | 3+0.03 | 6/0/0 | 6/0/0 | 1/0 | 2/3 | +0.0 |

Any all-win baseline cell has zero observed win-rate headroom. An apparent shift on the remaining normal subset can also reflect which games timed out. Only the holdout was eligible for primary decisions. These curated opening families and engine opponents do not establish general Elo or human win rate.

[Registered analysis rules](../../adversarial/analysis-plan.md)

## Search speed

Registered candidate and baseline averaged 237,440 and 270,008 nodes per second respectively (-12.06% candidate change). These short benchmarks on a shared worker visited different search trees. [Per-run evidence](../EXP-TACTICAL-QUIETCHECK-001/bench.json)
