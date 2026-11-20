# Versioned experiment queue

Use a single coordinator checkout on a persistent POSIX filesystem with Git 2.31+
and Python 3.8+. Set a Git author identity. Run commands in the ledger checkout.
The script locates its own checkout, so running a copy from a candidate worktree
does not redirect it to the ledger. Exit code 0 means success, 1 means a failed
operation and 2 means invalid CLI syntax.

After infrastructure approval, the operator creates the ledger once from the
approved infrastructure commit (supply its exact SHA). This copies the versioned
queue and the documented `EXP-COMPRESSION-001` packaging results into the ledger. The local
`research/baseline` ref must already identify the reviewed engine baseline.

```sh
git worktree add -b research/ledger ../stockfish-ledger APPROVED_INFRASTRUCTURE_SHA
cd ../stockfish-ledger
lab=research-lab/scripts/experiment.py
python3 "$lab" list
```

IDs preserve letter case. Admission considers IDs and topic keys case-insensitively.
Documented packaging findings occupy no active coding slot.

Before proposing, review archived records and the hypothesis backlog. Give
variants of one idea the same stable topic key. The coordinator compares
concepts, while the CLI detects duplicate keys and whitespace/case-normalized
hypotheses. An archived retry requires `--retry-reason 'new evidence or mechanism'`.
These commands illustrate a later approved experiment.

```sh
python3 "$lab" propose sample-a topic-key search 'One falsifiable prediction'
python3 "$lab" start sample-a
python3 "$lab" show sample-a
python3 "$lab" report sample-a implementation /path/to/handoff.json
python3 "$lab" finish sample-a rejected 'Record the actual reason here'
```

`propose` pins the full baseline SHA. `start` reserves a coding slot and prints
an isolated worktree path in a sibling directory named after the ledger checkout
with a `-candidates` suffix. Its branch is `experiment/<id>`. Commit the
candidate patch there. A second `start` on the same ID recovers its worktree
after an interrupted checkout. POSIX file locking serializes ledger writers.
The daily quota includes all proposals admitted on that UTC date, even if
they have since been archived. Active jobs consume slots until closed. The
config sets the limits to two coding slots and three daily proposals.

A research handoff for a proposed experiment includes its exact `baseline_commit`
and a null `candidate_commit`. This records artifact identification or other
research without reserving a coding slot. After the worktree is created,
a handoff JSON object has a full `candidate_commit`, a nonempty `summary`,
and an `evidence` object. The latter accepts the following keys. Store measured
values with units, test conditions and artifact digests. Include the roles'
decisions, methods, uncertainty level and interval convention where applicable.

- `build_configuration`, `hardware`, `compiler`
- `correctness`, `benchmark`, `match_settings`, `games`
- `elo_estimate`, `elo_uncertainty`, `statistical_decision`
- `compute_usage`, `review`, `fishtest`
- `artifacts`, `hardware_review`, `compression`

The CLI verifies the reported commit against the clean candidate checkout,
records changed files relative to the pinned baseline, and retains every
handoff. Revising a candidate resets the current evidence while preserving
the previous commit's reports. A reported role is a label. Independent
review and official testing require external verification before promotion.
Use null for unavailable measures, explaining the reason in the report. Stage
logs and complete machine-readable evidence under `research-lab/results/<id>/`.

Use `documented` to archive a packaging-only finding with measured artifact and
compression evidence, an explanation, and no strength claim.
Archive a chess experiment with `failed`, `rejected`, `inconclusive`, or `screened` and a reason.
`screened` means eligible for further testing. A played screen requires at
least one full game pair together with a recorded build, correctness check,
benchmark, match configuration, statistical decision and compute usage.
A screened record can receive further handoffs on its exact commit, including
rigorous test results and independent review, and can then be closed as
failed, rejected or inconclusive. Promotion is a separate policy decision. Inspect past records with `show` or
`git log -- research-lab/experiments/<id>.json`.

Each successful mutation commits the affected JSON record on `research/ledger`.
The branch and raw evidence must be backed up or pushed to the fork before
discarding the worker. If a process dies between replacing a record and its
Git commit, inspect the single pending record and its diff; complete the
interrupted commit before retrying. The tool refuses to overwrite a dirty queue.
Retain candidate branches and evidence for reproducibility. The queue performs
no model invocation, submissions or remote updates. Paid API use, NNUE training
and public testing budgets start at zero. The scheduled worker, remote
coordination, limited retries and authenticated role execution belong to Phase 3.
