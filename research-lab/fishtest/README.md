# Public Fishtest handoff

The CLI prepares standard functional STC and LTC SPRTs, reserves the lab's public slot and reads results from the official service. Submission goes through the owner's authenticated Fishtest web form and moderator approval. The CLI uses public GET endpoints and public Git refs. [Interface investigation and sources](interfaces.md)

## Prepare a reviewed local screen

Run the CLI on a single persistent Linux coordinator with Python 3.9+, Git and network access to GitHub and Fishtest. Use the approved `research/ledger` checkout. Store approved CI/local-worker engine builds and their manifests outside the candidate's control. Keep the experiment record and its exact baseline, candidate and reviewed explanation in the ledger. Publish the tested candidate to the fork under its assigned `experiment/ID` branch. The baseline must equal the current official master. On upstream changes create a newly pinned comparison, repeat local validation and screening and retain the superseded evidence.

```bash
ft=research-lab/fishtest/cli.py
python3 "$ft" prepare EXP-ID --baseline /builds/base --candidate /builds/head \
  --match /results/screen --book /books/screen.epd --username PUBLIC_ACCOUNT_NAME \
  --rationale "Evidence supporting a public test and limitations of the local screen" \
  --explanation "Technical explanation of the isolated engine change"
python3 "$ft" show EXP-ID
```

Before preparing, record the implementer identity in the implementation handoff's `evidence.build_configuration.implementer`. Submit a reviewer handoff for the same candidate. Its `evidence.review` object has `reviewer` (independent reviewer identity), `verdict: "approved"`, `reviewed_sha` (the exact candidate) and `unresolved_defects: []`. The coordinator compares recorded identities case-insensitively. Local role handoffs are attestations. Human review of their provenance and authenticated GitHub review are separate controls. Include inspected paths and findings for human review.

The CLI checks the binaries against the manifests, both commits, passing UCI/perft/reproducibility, repeated bench signatures and comparable build settings. It verifies the book digest and recomputes the candidate's paired game results and screening decision from the PGN. A rejected or incomplete screen blocks preparation. An inconclusive screen requires a coordinator's written rationale for public escalation. A local self-match never qualifies. The ledger stores the resulting packet. Review the official form's current defaults against that packet.

## Reserve and submit one stage

The owner configures the public account name, a positive cumulative hour allocation, and the actual server-enforced maximum games in `public_fishtest_max_games_approved` for each authorized stage in `config/agents.yaml`. Both stage ceilings and hours start at zero. An hours-only budget cannot reserve a public run. Keep passwords, cookies, CSRF tokens and private messages out of all CLI arguments and records. Use a public-safe approval reference. The human operator must have permission to use the account and compute. Check equivalent ideas, historical runs, alternate branch names and maintainers' current guidance. The CLI checks the active listing, up to ten recent 50-run history pages and local stable patch IDs. By default it checks three history pages. Review similar descriptions and record findings with `--related-review` when applicable.

```bash
python3 "$ft" reserve EXP-ID --hours APPROVED_HOURS --authorization APPROVAL_REFERENCE \
  --duplicate-review "Scope of historical search and findings" --approved-server-cap-and-rules
```

Every reservation claims its hours against the total allocation. Record an allocation compatible with the server's actual contract. The inspected server sets an 800,000-game ceiling per standard SPRT. It does not enforce a custom CPU-hour deadline through this form. Keep the slot unreserved if the approved budget cannot cover the official configuration. One shared coordinator and ledger checkout are required. The Git common-directory lock serializes local transactions. Independent clones need a shared transaction service before use. The CLI cannot prevent an unrelated person from submitting manually outside this lab.

After a successful reservation, sign in to the official form with the configured account. Select the packet's standard stage. Use the full base SHA as the base ref and the exact candidate SHA as the test ref, and confirm both signatures, test repo, opening book, threads, time controls, engine options, SPRT bounds and summary. Check official master and fork branch again just before clicking Submit. A changed pin requires fresh local evidence. A moderator independently approves the queued run. [Official form](https://tests.stockfishchess.org/tests/run)

```bash
python3 "$ft" attach EXP-ID OFFICIAL_RUN_ID_OR_VIEW_URL
python3 "$ft" refresh EXP-ID
python3 "$ft" monitor EXP-ID --polls 6 --interval 300
```

Register even a run which has already finished. An interrupted reservation persists. Check Fishtest and attach any submitted run before doing anything else. If no submission took place, use `release EXP-ID --never-submitted --reason "Reconciliation evidence"` only when the operator can attest that the form was never submitted. Release checks active runs and pages through the account history since one day before reservation. Incomplete pagination blocks release. Fishtest excludes deleted jobs from this listing, so a lost response or possible deletion must retain the slot until the submitted run is identified. A failed read leaves the slot occupied. A released, unsubmitted reservation returns its allocated hours. Started stages keep their full allocation charged.

Each successful observation records the URL, commit hashes, signed-off settings, actual statistical state, game results and available uncertainty. A passed STC automatically prepares the LTC packet. Reserving LTC checks current refs, duplicates, available budget and STC again. A failed or inconclusive official result ends the sequence. A passing LTC fetches STC again and prepares the upstream PR body in the versioned record. Export it with the command below. The human reviews the text and upstream contribution requirements before submission.

```bash
python3 "$ft" upstream-pr EXP-ID > /tmp/upstream-pr.md
```

`monitor` exits on a stage transition, error or its polling limit (1–24 reads, 60–3600 seconds apart). Retrying `refresh` reconciles the current state after a read failure. Service changes, blocked network access and incompatible data fail closed. Result recording does not grant the project's separate +3 Elo integration gate. This CLI has no upstream write or merge operation.

Every state change is committed to the owner's ledger checkout with the existing ledger transaction. Push that branch through the approved fork workflow to replicate it across workers. Existing pending ledger changes, an occupied slot or failed required evidence checks stop the command. The CLI exits 0 on success, 1 on a blocked operation and 2 for invalid arguments.
