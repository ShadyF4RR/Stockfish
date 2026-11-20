# Replaying the opponent panel

Use the two Stage A build directories with matching configuration, the exact binaries recorded in the registration, Fastchess, Linux cgroup v2, Bubblewrap, and Python 3.9+. The harness hashes both engines, checks both correctness records and pins selected positions, the analysis plan, Fastchess and each opponent binary. The generated schedule contains all 32 cells before any game. Each of the two arms plays the same openings with both colors. The evaluator that estimates move difficulty runs separately from the chess games.

| Opponent ID | Source and reproduction |
| --- | --- |
| stockfish | The baseline engine from the registered build manifest. Baseline selfplay calibrates the direct candidate-versus-Stockfish cell. |
| fruit | Debian `fruit_2.1.dfsg-9_amd64.deb`, SHA-256 `fabbe937de701f7ca91396b4bd924784a139023bdc8af4dd37508ac6ebcf72d1`. Extract with `dpkg-deb -x` and use `usr/games/fruit`. Fruit 2.1 is a single-thread classical engine. Disable its own opening book. [Debian package](https://deb.debian.org/debian/pool/main/f/fruit/fruit_2.1.dfsg-9_amd64.deb) |
| ethereal | Ethereal V12.00 at commit `37ccbfe233ac418a49cb9a5c9f54d62b060d44ad`. On the measured x86-64 worker, build with `make -C src CC='gcc -fcommon' popcnt`. This is Andrew Grant's classical UCI engine. [Pinned source](https://github.com/AndyGrant/Ethereal/tree/37ccbfe233ac418a49cb9a5c9f54d62b060d44ad) |
| weiss | Weiss v2.0 at commit `0db49c7270f5c6286a983390a5ea2780e1e0c9f4`. Build with `make -C src basic WARN='-Wall -Wextra -Wshadow'`. Disable NoobBook and OnlineSyzygy. Weiss derives from VICE and credits inspiration from Ethereal and Stockfish. These are distinct projects with overlapping chess knowledge. [Pinned source](https://github.com/TerjeKir/weiss/tree/0db49c7270f5c6286a983390a5ea2780e1e0c9f4) |

Use Fastchess commit `894616028492ae6114835195f14a899f6fa237d3`. The sample records its actual binary checksum. In Bash, define absolute paths and provide a new local output directory.

```sh
options=(--baseline "$BASE_BUILD" --candidate "$CANDIDATE_BUILD"
         --fastchess "$FASTCHESS" --fruit "$FRUIT" --ethereal "$ETHEREAL" --weiss "$WEISS" --out "$OUT")
python3 -B research-lab/adversarial/matches.py register "${options[@]}"
python3 -B research-lab/adversarial/matches.py play "${options[@]}"
```

The worker takes a host lock and uses the cloud profile's OS reserves. It admits two concurrent one-thread games, pins processes to the admitted cores, enforces a virtual-memory limit per process, and shares a single 18-minute deadline across cells. Each cell uses a fresh network-isolated namespace. Fastchess reuses engine processes across games within that cell and sends UCI new-game commands. The artifact includes the PGN, launch log, opening file and observed terminations for each cell. Time forfeits and incomplete pairs remain in the record. The registration precludes retries and affirmative decisions from incomplete cells. The sample's 1+0.01 and 3+0.03 clocks are short local tests. Official STC/LTC evidence and representative opening-book tests are separate requirements. [Registration and raw evidence](../results/EXP-ADVERSARIAL-001/preregistration.json)
