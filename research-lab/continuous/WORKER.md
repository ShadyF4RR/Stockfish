# Disposable engine validation

The coordinator supplies two exact fork checkouts on a GitHub-hosted Linux runner. It reserves the attempt's runner-minute budget before dispatch and enforces campaign concurrency in the workflow. The worker makes a private, standalone Git snapshot of each revision, checks that the candidate is a single child commit of the baseline and accepts changes only to existing regular C++ source files under `src/`. It runs both builds, both upstream correctness suites and two canonical benches per binary. The baseline must match its published signature from Git history.

```sh
python3 -B research-lab/continuous/validate_candidate.py \
  /path/to/base /path/to/candidate /new/evidence --fastchess-dir /path/to/fastchess
```

Use the approved controller checkout with Linux cgroup v2, x86-64 AVX2, GCC, GNU Make, Git, curl, Python 3.9+, Expect and Bubblewrap. Host-provided files must be readable by the runner. A production GitHub Actions job also requires passwordless sudo and a running systemd scope manager. The enclosing job has a 20-minute timeout. Build Fastchess in its clean checkout at `894616028492ae6114835195f14a899f6fa237d3` during trusted setup. The worker verifies the tracked revision and the opening book checksum. The authenticated checkout uses `persist-credentials: false`.

The controller fetches declared NNUE files from upstream over HTTPS and checks their SHA-256 name prefixes. To use an existing verified cache, pass `--nnue-dir DIRECTORY`. Builds and engine processes run in a private mount, PID and network namespace with an empty environment populated with public execution settings. Source, earlier engine manifests, test drivers and engine binaries are mounted read-only for the checks that consume them. Benchmarks use the same compiler, architecture and CPU affinity for both sides. No engine process receives a repository token, a host socket or a network route.

Hosted jobs reserve at least one vCPU and 1,536 MiB for the OS, and use at most two vCPUs and 5,120 MiB after checking cgroup limits and available memory. A kernel cgroup scope limits aggregate memory, CPU, process count and swap. The controller limits compiler parallelism, enforces CPU affinity, and gives all stages one 19-minute deadline. A supervised Codex Cloud replay respects the cloud worker's usual two-core and 4 GiB reserves, with affinity and per-process address-space limits inside Bubblewrap. Its enclosing cloud environment supplies the aggregate cgroup boundary.

## Results and decisions

On success, a correctness rehearsal needs exactly the predefined standalone comment insertion and the same published bench on both binaries. Run with `--rehearsal` for this check. The rehearsal uses zero match games.

For a research candidate the worker holds a median benchmark NPS ratio below 0.95 for independent investigation. That threshold is a screening safeguard on a shared worker. A candidate that clears it plays 16 predeclared opening pairs, with colors reversed, 4+0.04, a single search thread, the default seed and 16 MiB hash. The cap is 600 seconds or the remaining worker budget. A time forfeit, incomplete pair or timeout invalidates the screen. A valid result uses the paired decision returned by the lab's match driver. Small practical improvements will often be inconclusive.

The worker writes `result.json` with exact SHAs, source paths, per-side benchmark signatures, NPS ratio, status, decision, game count, budget, usage, GitHub workflow identifiers and a SHA-256 for every evidence file. The artifact contains both sides' build/validation/benchmark manifests and compact logs. A completed strength screen also includes `match/match.json`, `match/games.pgn`, `match/fastchess.log` and `match/openings.epd`. The host rehashes both binaries before discarding them.

A job that exits 0 may still report a performance hold, a rejection or an inconclusive result. Exit 1 records an invalid attempt or unmet dependency. Local JSON has no signing authority. The coordinator must verify the immutable artifact from the expected GitHub run, attempt, workflow ref, tested commits and successful job, then verify its internal digests. The Fishtest handoff requires separate independent review and authorization. A successful rehearsal provides no strength evidence.


## Validate a normal Codex Cloud experiment

Publish one isolated engine commit to `experiment/<ID>`. The fork's `research/baseline` ref must still match its parent. On the trusted control branch, edit `research-lab/config/cloud-candidate.json` with the experiment ID, baseline SHA and candidate SHA. Pushing that marker to `research/continuous-coordinator` or `master` starts **Codex Cloud candidate validation**. Once installed on the default branch the workflow also accepts manual dispatch with the same three explicit inputs.

The workflow loads the worker from the control commit, fetches the two named public fork refs as data, verifies the requested pins, parent and engine-only changed paths, and runs the comparison on a standard public-fork Ubuntu runner. Candidate pushes alone do not select their own workflow or authorize a run. The job has a read-only token, no saved Git credentials and a 28-minute job timeout including setup. Review `evidence/result.json` and the request artifact for the tested SHAs, decision and game count. An infrastructure error before validation may have only workflow logs. A successful job can still contain a performance hold, rejection or inconclusive result. Artifacts are retained for 30 days.
