# Stockfish Elo research lab

This is the control plane for the ShadyF4RR fork. Start at [AGENTS.md](../AGENTS.md). Research takes place in Codex Cloud and on GitHub. The optional home worker needs a separately configured connection. It is never a prerequisite for development.

## Phase 1 and approval

The setup PR targets `research/integration`. Both research branches were created from `1819b9ea37588be8adb044179fb54512f87c8249`. The fork's `master` branch and Stockfish sources are outside the setup diff. In a new checkout, configure official Stockfish as a fetch-only remote and create the local baseline ref at the verified pin.

```sh
git remote add upstream https://github.com/official-stockfish/Stockfish.git
git remote set-url --push upstream DISABLED
git fetch origin research/baseline research/integration
git branch research/baseline 1819b9ea37588be8adb044179fb54512f87c8249
```

Review the [promotion rules and fork administration steps](promotion.md) before enabling any writer. The owner approves infrastructure changes. Phase 2 selects one [Elo hypothesis](reports/first-hypotheses.md) and runs a supervised experiment. [EXP-COMPRESSION-001](experiments/compression-001.md) also has a persistent queue entry. A packaging proof uses a separate branch and a verified Stockfish 19 release. Its early results are distinct from experiments on the development baseline.

## Running a supervised cycle

1. Consult the [ledger](experiments/README.md), history, [agent roles](agents/), and [resource limits](config/). Initialize the ledger from the approved infrastructure commit as documented. Propose a single change and use the coordinator's `start` command to reserve a slot and create a pinned worktree.
2. Commit the candidate on its experiment branch. Build each revision on the same worker using the approved harness. The engine build archives the checked-in source at HEAD and verifies the NNUE. Use fresh paths outside the source trees. The default configuration uses GCC and AVX2 on x86-64. Supply a supported `ARCH` for other CPUs. Set `NNUE_DIR` to a verified local cache if upstream downloads are unavailable.
3. Run correctness and benchmark checks for **both** binaries. For a functional patch, record the candidate's expected new bench in its commit following upstream convention. Resolve unexpected changes. Run an independent review before local screening.
4. Use a licensed EPD opening set and the same declared settings and seed for each balanced comparison. Preserve raw PGN, logs and hashes. Review the interval and actual outcomes before deciding whether to allocate any more games. See [match testing](scripts/local-match.sh) and the [results guide](results/README.md).
5. Use structured, exact-commit role handoffs. Archive failures, regressions and inconclusive evidence in the ledger. Use [promote.sh](scripts/promote.sh) in the trusted checkout to inspect a promising record. A preflight can identify blockers. A positive promotion decision requires independent service verification under the [fork policy](promotion.md).

Example for a Bash/Linux worker with Git, GCC/g++, GNU make, Python 3.9+, coreutils, tar and expect. Use the lab scripts from the reviewed checkout.

```sh
lab="$PWD/research-lab/scripts"
"$lab/build.sh" /path/to/baseline /tmp/sf-base-build
"$lab/build.sh" /path/to/candidate /tmp/sf-candidate-build
"$lab/validate.sh" /tmp/sf-base-build
"$lab/validate.sh" /tmp/sf-candidate-build
"$lab/benchmark.sh" /tmp/sf-base-build 1488683
"$lab/benchmark.sh" /tmp/sf-candidate-build "$CANDIDATE_BENCH"
"$lab/local-match.sh" /tmp/sf-base-build /tmp/sf-candidate-build /data/openings.epd /tmp/sf-screen
```

The first bench value is specific to the initial baseline. The match runner requires Fastchess on PATH. A new, empty run uses conservative defaults. The JSON-compatible YAML files can be read with the Python standard library. Keep assigned worktrees and untrusted binaries away from the credentialed coordinator.

## Choosing match concurrency

The default cloud worker measures its own capacity. On a connected Linux worker with eight cores and 16 GB of RAM, pass `--profile local_8c_16g`. The profile applies on the machine running the command. Development and calibration can run entirely in the cloud.

The runner counts physical cores available to the process and checks the Linux cgroup v2 CPU quota, memory ceiling and currently available RAM. It reserves two cores and 4 GiB for the operating system. On the stated local machine it can use at most six cores and 12 GiB, and it uses less when the worker has less free capacity. Each active board needs memory for both engines, including their network weights and hash tables. Pondering is disabled so only one engine searches on each board. The worker pins the match processes to the admitted cores and enforces per-process memory limits. A worker lock prevents overlapping match pools under the same account.

By default the runner fits as many one-thread games as the measured capacity and requested game count allow. To measure a worker, run the same baseline on both sides. Calibration compares simultaneous single-thread games with a two-thread allocation using the same opening pairs, seed, time control and binaries.

```sh
"$lab/local-match.sh" /tmp/sf-base-build /tmp/sf-base-build /data/openings.epd /tmp/sf-calibration --calibrate
```

Read `throughput.json` for measured games per hour and links to each allocation's PGN-backed result. You can reuse an observed allocation with `--concurrency N --threads T` on the same worker. The runner rechecks capacity before each run. The initial cloud sample measured 394 games/hour for two concurrent single-thread games and 215 games/hour for one two-thread game. Each allocation played four games. [Cloud calibration and raw evidence](results/phase1-throughput/README.md)

## CI, scheduling and continuous operation

The `research-ci` job builds the event's exact engine SHA, checks upstream perft, search reproducibility and UCI, compares the bench to the recorded signature and retains diagnostic artifacts for 14 days. Permanent evidence belongs in the ledger or a persistent results store. All jobs in the lab workflows use a read-only GitHub token, a timeout, and checkout without persisted credentials. The workflow accepts GitHub merge-group events for future protected integration testing.

The scheduler file defines a daily 04:17 UTC or manually dispatched **read-only queue inventory**. Its job is disabled unless `RESEARCH_QUEUE_INVENTORY=enabled`. Scheduled Actions and manual dispatch require the workflow on the fork's default branch. Merging this PR into the integration branch alone will not install the scheduler there. Inventory also requires an approved `research/ledger` ref containing the lab. A read-only inventory does not invoke Codex, start games, send notifications or promote experiments.

After infrastructure approval and one supervised validation, Phase 3 must provision a persistent isolated worker and store, connect an authenticated agent executor, enforce host-wide leases, deadlines, bounded retries and spend accounting, and test recovery across worker restarts. Verify branch rules, exact-commit promotion and notification delivery with a controlled run before activation. Keep separate identities for untrusted execution and verified publication. Paid model, NNUE-training and public Fishtest budgets start at zero. Only authorized maintainers can accept an upstream contribution. [Official contribution guidance](../CONTRIBUTING.md)

## Fishtest

The [submission and monitoring tools](fishtest/) prepare reviewed public runs on current official master. Read the [interface investigation](fishtest/interfaces.md) for account approval, duplicate checks, public budget limits, human form submission and result verification. Configure the operator's approved account and compute allocation only when public testing is authorized. Keep login credentials out of commands, manifests, logs and repository secrets used by untrusted jobs.
