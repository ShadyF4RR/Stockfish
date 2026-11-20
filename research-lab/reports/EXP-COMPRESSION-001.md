# EXP-COMPRESSION-001

A lossless compression change may reduce engine or network storage and loading costs while preserving decoded data and engine behavior. Measure its effect on resident memory, startup time, CPU use and search speed before considering matches.

**Outcome** documented. Packaging result documented. Owner identifies MSZIP and direct GUI/HTML use in the earlier experiment. Its exact files, implementation and 135 MB input were not recovered. Independent reconstruction restores the official native engine automatically. No playing-strength claim.

| Measure | Recorded value |
|---|---|
| Baseline commit | edb0d9db6731067ec50ce619ff372b463bc4dd5d |
| Candidate commit | Unreported |
| Changed source files | research-lab/proofs/mszip/, .github/workflows/mszip-package.yml |
| Build | Official Stockfish 19 Linux x86-64 universal release, prebuilt; no recompilation. |
| Correctness | All four restores passed SHA-256 and length checks. Gzip outputs passed cmp, NNUE diagnostics matched at three positions, all eight benches searched 2497913 nodes. The actual MSZIP launchers passed UCI, a forced legal move and the SF19 release bench on native Windows and Linux GitHub runners. A Linux desktop GUI and Chromium HTML client drove the packaged engine. |
| Games played | 4 |
| Planned games | Unreported |
| Candidate wins / draws / losses | Unreported |
| Estimated Elo | Unreported |
| Confidence interval | Unreported |
| Started | 2026-11-20T20:58:46+00:00 |
| Last recorded update | 2026-11-20T21:20:33+00:00 |

## Performance

- Linux official executable 103153472 bytes
- Linux MSZIP launcher 88174740 bytes
- Windows official executable 103046300 bytes
- Windows MSZIP launcher 88265216 bytes

## Evidence

- [README.md](https://github.com/ShadyF4RR/Stockfish/blob/research/adversarial-tactics/research-lab/results/EXP-COMPRESSION-001/README.md)
