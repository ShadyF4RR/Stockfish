# EXP-ADVERSARIAL-001

Measure head-to-head score and practical win rate for quiet-check ordering on the registered opening and opponent panel.

**Outcome** partial. 192 recorded games across test and control arms. 34 abnormal games. See the registered analyses for the separate head-to-head and practical endpoints. Abnormal or missing observations preclude an affirmative overall strength finding.

| Measure | Recorded value |
|---|---|
| Baseline commit | 1819b9ea37588be8adb044179fb54512f87c8249 |
| Candidate commit | c72e2482c77f8eabca37550f019ed55bfb8a7b79 |
| Changed source files | src/movepick.cpp |
| Build | \{&quot;ARCH&quot;: &quot;x86-64-avx2&quot;, &quot;COMP&quot;: &quot;gcc&quot;, &quot;CXX&quot;: &quot;g++&quot;, &quot;target&quot;: &quot;build&quot;\} |
| Correctness | Unreported |
| Games played | 192 |
| Planned games | 192 |
| Candidate wins / draws / losses | Unreported |
| Estimated Elo | Unreported |
| Confidence interval | Unreported |
| Started | Unreported |
| Last recorded update | Unreported |

## Performance

No numerical performance measurement was supplied.

## Recorded game groups

| Arm | Opponent | Split | Clock | W / D / L | Abnormal |
|---|---|---|---|---:|---:|
| baseline | weiss | discovery | 1+0.01 | 6 / 0 / 0 | 0 |
| candidate | weiss | discovery | 1+0.01 | 5 / 1 / 0 | 0 |
| candidate | ethereal | discovery | 1+0.01 | 6 / 0 / 0 | 4 |
| baseline | ethereal | discovery | 1+0.01 | 6 / 0 / 0 | 3 |
| baseline | fruit | discovery | 3+0.03 | 6 / 0 / 0 | 0 |
| candidate | fruit | discovery | 3+0.03 | 6 / 0 / 0 | 0 |
| candidate | weiss | discovery | 3+0.03 | 6 / 0 / 0 | 1 |
| baseline | weiss | discovery | 3+0.03 | 6 / 0 / 0 | 0 |
| baseline | stockfish | discovery | 3+0.03 | 2 / 4 / 0 | 0 |
| candidate | stockfish | discovery | 3+0.03 | 0 / 6 / 0 | 0 |
| candidate | fruit | discovery | 1+0.01 | 6 / 0 / 0 | 0 |
| baseline | fruit | discovery | 1+0.01 | 6 / 0 / 0 | 0 |
| baseline | stockfish | discovery | 1+0.01 | 2 / 3 / 1 | 0 |
| candidate | stockfish | discovery | 1+0.01 | 1 / 5 / 0 | 0 |
| candidate | ethereal | discovery | 3+0.03 | 6 / 0 / 0 | 5 |
| baseline | ethereal | discovery | 3+0.03 | 6 / 0 / 0 | 5 |
| baseline | fruit | holdout | 3+0.03 | 6 / 0 / 0 | 0 |
| candidate | fruit | holdout | 3+0.03 | 6 / 0 / 0 | 0 |
| candidate | weiss | holdout | 1+0.01 | 5 / 1 / 0 | 0 |
| baseline | weiss | holdout | 1+0.01 | 5 / 1 / 0 | 0 |
| baseline | fruit | holdout | 1+0.01 | 6 / 0 / 0 | 0 |
| candidate | fruit | holdout | 1+0.01 | 6 / 0 / 0 | 0 |
| candidate | weiss | holdout | 3+0.03 | 6 / 0 / 0 | 0 |
| baseline | weiss | holdout | 3+0.03 | 6 / 0 / 0 | 0 |
| baseline | ethereal | holdout | 3+0.03 | 6 / 0 / 0 | 4 |
| candidate | ethereal | holdout | 3+0.03 | 6 / 0 / 0 | 4 |
| candidate | ethereal | holdout | 1+0.01 | 6 / 0 / 0 | 4 |
| baseline | ethereal | holdout | 1+0.01 | 6 / 0 / 0 | 4 |
| baseline | stockfish | holdout | 1+0.01 | 1 / 5 / 0 | 0 |
| candidate | stockfish | holdout | 1+0.01 | 0 / 5 / 1 | 0 |
| candidate | stockfish | holdout | 3+0.03 | 1 / 5 / 0 | 0 |
| baseline | stockfish | holdout | 3+0.03 | 2 / 3 / 1 | 0 |

## Evidence

- [matches.json](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-ADVERSARIAL-001/matches.json)
- [preregistration.json](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-ADVERSARIAL-001/preregistration.json)
- [analysis.json](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-ADVERSARIAL-001/analysis.json)
- [PGNs and Fastchess logs](https://github.com/ShadyF4RR/Stockfish/tree/research/adversarial-tactics/research-lab/results/EXP-ADVERSARIAL-001/cells)
