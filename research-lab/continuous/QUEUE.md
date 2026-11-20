# Claim a research experiment from an existing hosted task

Use the normal signed-in Codex Cloud interface to start the task. The queue records a claim for that already-running task. The coordinator accepts the operator's explicit confirmation of the task and subscription. It cannot inspect subscription limits, extra-credit billing settings or the identity of the current hosted process. GitHub ownership of the queue determines who can change its records.

In the trusted lab checkout, use your existing authorization to push ordinary updates to the fork. The CLI reuses the checkout's Git credential helper/configuration for the fork HTTPS URL. The resulting queue branch stores JSON and immutable Git history. Keep ChatGPT credentials in the normal Codex interface. The queue never needs them. An existing scoped GitHub writer can also supply `QUEUE_GITHUB_TOKEN` through its secret store.

Initialize once, then claim from inside the authorized task. Substitute its stable task identifier and the verified 40-character engine baseline SHA.

```sh
python3 -I research-lab/continuous/coordinator_queue.py init
python3 -I research-lab/continuous/coordinator_queue.py cloud-claim \
  --job "$CLOUD_TASK_ID" --baseline "$BASELINE_SHA" \
  --confirm-hosted-subscription-task --out plan.json
```

Read `action` before doing work. `run` assigns one experiment. `research` assigns discovery of a new hypothesis. `idle` means no work was claimed. Use the returned `id` and `lease` through the task. Use `source_commit` as the engine baseline, review `history`, and save checkpoints before moving to the next stage. A repeated job ID cannot launch a second campaign. Every transition reads the latest queue tip and uses a non-force Git push.

```sh
python3 -I research-lab/continuous/coordinator_queue.py checkpoint \
  --id "$EXPERIMENT_ID" --lease "$LEASE" --stage review --details-file review.json
python3 -I research-lab/continuous/coordinator_queue.py finalize \
  --id "$EXPERIMENT_ID" --lease "$LEASE" --result result.json
python3 -I research-lab/continuous/coordinator_queue.py status
```

A result includes `status`, `reason`, and measured `usage`. Status is `screened`, `rejected`, `inconclusive`, or `failed` for an experiment; discovery can finish with `proposed`. Use `next_hypotheses` for at most three candidates with `topic`, `hypothesis` and one engine source `path`. `candidate_sha` and `artifacts` identify supporting evidence. Report the measured worker minutes. The `requests`, `input_tokens`, `output_tokens` and `cost_usd` meters refer to external model API usage and have hard zero limits. Subscription usage is accounted for by Codex. If a reservation cannot be settled from verified measurements, omit `usage`; the entire reservation is charged.

The production queue lives at `research/queue`. Its unattended `plan` command returns idle when a hosted task would be needed. GitHub Actions cannot use `cloud-claim`. For deterministic offline rehearsal, pass `--rehearsal NUMERIC_RUN_ID` **before** each subcommand and use `plan` for the isolated rehearsal ref. A rehearsal creates no subscription work and consumes no model allocation. Use `pause --reason TEXT` to halt new admissions to execution and `resume` to reopen available slots. A running task may still settle its claim during a pause.

For a bounded campaign the coordinator can use `cloud-claim --reserve-minutes N` to reserve its full expected worker budget up front, subject to all existing per-attempt, daily and lifetime ceilings. Include build, validation and round-trip reporting time in the reservation.
