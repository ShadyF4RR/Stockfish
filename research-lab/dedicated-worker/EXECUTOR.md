# Running a leased chess screen

The supervisor claims the experiment and records its start in the shared dispatch ledger. It launches the executor with the exact minutes reserved by that lease. Use the same controller for a dedicated PC and a cloud worker. Downloads use the public fork and official NNUE servers. Engine builds, tests and matches run in network-isolated Linux namespaces with an empty environment, private temporary storage and read-only inputs.

```sh
python3 -I research-lab/dedicated-worker/executor.py \
    --worker pc --request "$request" --output "$new_directory" \
    --fastchess /opt/stockfish-fastchess --max-minutes "$reserved_minutes"
```

The supervisor projects six fields from the lease into the request file. It supplies the experiment ID, baseline and candidate full commit IDs, pair count (1–50), positive unsigned 32-bit seed and supported clock. This executor supports 4+0.04 and 12+0.12 when enabled by the dispatch policy. The optional `--nnue-dir` supplies a local cache of verified upstream networks. Only the supervisor resumes an existing lease. The registered batch plan binds its binaries, opening book, seed, clock and layout.

The PC runner checks the WSL2 kernel, an unprivileged service identity, a VM total at or below 12 GiB and an effective service cgroup memory ceiling. The shipped installation sets the service ceiling to 10 GiB, leaving Windows at least 4 GiB on a 16 GiB machine and reserving guest headroom. Build jobs are capped at four and further limited by memory, CPU quota and affinity. Match throughput can use up to eight admitted vCPU slots. The worker counts the memory of both engines on every simultaneous board plus the manager. Cloud workers use their own cgroup and OS reserves.

Both verified builds must pass UCI, perft, and search-reproducibility tests and complete two canonical benches. The upstream baseline must match its published signature. A candidate median speed below 95% of baseline returns `hold_performance` before starting any games.

Calibration uses two distinct opening pairs and the baseline binary on both sides. Every layout gets the same seed, clock, four games and reversed colors. Up to four layouts cover one one-thread game, the maximum feasible simultaneous one-thread games, and two- and four-thread alternatives. Concurrency is bounded by the smaller of the calibration and primary batch sizes, available CPU and RAM. A PC requires a working multi-thread comparison. Each match must have normal terminations, correct engines and a complete paired PGN. The fastest measured games per hour selects the layout for the primary candidate-versus-baseline screen. Four games per layout can identify gross differences and carry considerable timing noise. Only identical preselected positions are compared across layouts. The primary openings are a disjoint seeded subset of the pinned source book.

The production screen uses up to five registered opening pairs per checkpoint batch. Fastchess plays each opening and color under a persistent game identity with the calibrated limit on simultaneous games. A recovered worker reads completed PGNs and admits untouched identities. It preserves an uncertain started game for investigation. Calibration uses the original paired Fastchess driver.

Successful output contains `result.json`, six build/validation/bench manifests, `calibration.json`, and one PGN, book, driver log and manifest per batch. `games` counts completed primary games, `calibration_games` counts calibration games, and `actual_games` sums them. A maximum primary request consumes 100 primary and up to 16 calibration games. Match rules reject time forfeits, incomplete pairs, and changed artifacts. An invalid run still preserves completed PGNs for accounting. The supervisor publishes and binds the evidence to the actual worker and lease. A local protocol test creates no worker receipt.
