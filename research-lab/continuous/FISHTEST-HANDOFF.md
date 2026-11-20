# Preparing an STC packet after a local screen

The trusted final GitHub job runs the following command after publication of the candidate branch. It consumes the coordinator plan, two independent Codex task outputs and the worker evidence directory.

```sh
python3 -I research-lab/continuous/handoff.py plan.json implement.json review.json evidence/
```

The command returns a JSON decision. A rehearsal, a review-branch run, a performance hold, a rejected screen or an inconclusive screen yields `skipped`. Production writes require the fork’s `master` version of the `experiment-scheduler.yml` workflow. A changed official master yields `rebase_required`. Missing independently verified Codex Cloud task bindings yield `awaiting_cloud_tasks`. Invalid evidence exits 1. The earlier local measurements remain in the continuous queue in each case.

For an actual subscription-task handoff, the owner must first identify the approved Codex Cloud environment and verify that the two separate Codex tasks produced these exact outputs. Keep that evidence on the protected control revision. The binding file is `research-lab/config/codex-task-handoffs/<experiment-id>.json`. It contains the experiment ID, full source commit, configured approved environment ID, and an `implement` and `review` object, each with its verified task ID and `result_sha256`. The latter hashes the entire parsed role result serialized as UTF-8 JSON with sorted keys, ASCII escapes, and no whitespace between keys/values. Each role must identify a different subscription task, the same source and edits, and zero USD. The review identifies the exact candidate commit. Current configuration leaves the approved environment unset. The tool does not dispatch a task, handle subscription credentials, or use a paid or local model.

For an eligible production screen, the final job obtains `WORKER_ARTIFACT_ID`, `WORKER_ARTIFACT_DIGEST` and `WORKER_ARTIFACT_NAME` from its validation job. The fixed fork API must confirm the exact artifact, repository, run ID, attempt-specific name, SHA and branch. The publisher checks the downloaded archive digest and the result manifest inside it. It hashes the individual build, validation, benchmark, PGN, match and opening files and recomputes the local Stage C decision. The actual executables remain on the isolated worker.

The publisher reconstructs the candidate from the original pinned source and edit, checks the public fork branch and official master, then prepares the standard short-time-control packet using the existing lab code. It reads and writes Git objects in an isolated bare checkout. A successful non-force update and read-back from the fixed `research/ledger` ref is required. On first use the initial parent is the trusted control commit. An identical handoff returns `already_prepared`; a conflicting ID fails closed.

The GitHub token needs Actions artifact read and fork ledger contents write. Pass the ledger token as `FISHTEST_LEDGER_TOKEN`. The optional `GH_TOKEN` is used for artifact reads. The handoff creates a `screened` experiment record with a `prepared` STC packet. It reserves no public compute and creates no public run. Subsequent manual reservation, forum permissions, official Fishtest human submission, monitoring and the separate integration-promotion gates remain governed by the [Fishtest procedure](../fishtest/README.md).
