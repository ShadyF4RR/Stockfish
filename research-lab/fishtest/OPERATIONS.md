# Operating the shared Fishtest integration

Use the reviewed control checkout for every lab role and experiment. Public queries accept a run ID or official run page where shown. To invoke the commands in the table from the repository root, define this shell function for the session.

```bash
ft() { python3 research-lab/fishtest/cli.py "$@"; }
```

| Task | Command or action | Result |
|---|---|---|
| Inspect the public queue or history | `ft active` or `ft completed --page 1` | Anonymous public listings. Increment the page to inspect further history. |
| Inspect a public run | `ft status RUN_ID` or `ft results RUN_ID` | Current lifecycle or outcomes, with uncertainty when supported. These commands also accept an official run URL. |
| Prepare the candidate | `ft prepare EXP-ID --baseline BASE_BUILD --candidate HEAD_BUILD --match SCREEN_DIR --book OPENING_FILE --username ShadyF4RR --rationale "screen findings" --explanation "patch mechanism"` | Records an STC packet after checking local evidence and public refs. Use real evidence and paths. |
| Inspect a lab experiment | `ft stage EXP-ID` or `ft show EXP-ID` | Current stage and next action, or full recorded state. |
| Reserve the one public slot | `ft reserve EXP-ID --hours APPROVED_HOURS --authorization PUBLIC_REFERENCE --duplicate-review "history checked and findings" --approved-server-cap-and-rules` | Verifies the stage-specific allocation and duplicate checks. Include `--related-review` if applicable. |
| Hand off for human submission | `ft submit EXP-ID` | On success, returns exit 3 and the official form instructions for the reserved packet. The owner signs in as `ShadyF4RR`, checks all packet fields, and selects **Submit test** once. [Official form](https://tests.stockfishchess.org/tests/run) |
| Record the resulting run | `ft attach EXP-ID OFFICIAL_RUN_ID_OR_VIEW_URL` | Reads the public run, validates resolved commits and settings and records its URL. Attach even if the run has already finished. |
| Refresh an attached experiment | `ft refresh EXP-ID` or `ft monitor EXP-ID --polls 6 --interval 300` | Persists progress or the next eligible stage. Network failures retain the prior state for reconciliation. |
| Continue to LTC | `ft stage EXP-ID`, then `ft reserve`, `ft submit` and `ft attach` with the same experiment ID and a new stage authorization | A verified STC pass prepares the LTC packet. The reserved LTC stage receives its own Fishtest run ID. |
| Prepare the upstream PR | `ft upstream-pr EXP-ID > /tmp/stockfish-upstream-pr.md` | Exports a draft after qualifying official STC and LTC passes. Review the evidence and upstream contribution rules before publication. |

A timeout during human submission leaves the reservation occupied. Search pending and completed history for the actual submission. Attach the matching URL. After the operator establishes that no submission took place, the documented release procedure can reclaim the reservation. [Ledger, input files and reconciliation](README.md) · [Official procedures and API formats](interfaces.md)

## Accounts, settings and deployment

| Setting | Value and scope |
|---|---|
| Fishtest account | `ShadyF4RR` is the owner's approved public login. The owner authenticates in the official browser form. **The integration needs zero Fishtest secrets in the repository or Actions.** No password, cookies, CSRF token, worker credentials or personal API token go into this workflow. |
| Public compute | In `research-lab/config/agents.yaml`, `public_fishtest_username` identifies the allowed submitter. The owner approves `public_fishtest_budget_hours` and `public_fishtest_max_games_approved.stc` or `.ltc` for the intended stage. Zero authorizes no public compute. At the reviewed server revision each new SPRT has a ceiling of 800,000 games. The human confirms that ceiling and the budget for each reservation. |
| Coordinator | Give the interactive coordinator an authenticated Git identity with **Contents: write** on this fork and permission to append to `research/ledger`. Use its Git credential manager or an authorized SSH identity. Synchronize the dedicated ledger before state changes. The CLI verifies the fork and reads back each fast-forward publication before it hands out a public submission slot. Keep ledger updates on the fork. A rejected non-fast-forward update needs fresh state and reconciliation. |
| GitHub Actions | Deploy the reviewed `fishtest-monitor.yml`, the shared module and its tests on the fork's protected default branch. Enable GitHub Actions for the fork, preserve the workflow's explicit job-level token permissions and make sure the `research/ledger` branch exists. The monitor uses the automatically issued `GITHUB_TOKEN` with `contents: write` to persist verified ledger updates. It requires **zero manually provisioned GitHub secrets**. |
| Branch controls | Protect the default branch's workflow and lab code from engine-branch edits. Protect the ledger from arbitrary writers, deletion and force-push. The repository rules must allow designated writers to append the authorized ledger commits. The token's Contents scope is repository-wide, so retain the monitor's fixed destination ref, clean data validation and non-force push. |
| Scheduler activation | After review and deployment, set the repository **Actions variable** `FISHTEST_MONITOR` to `enabled`. The monitor runs at minutes 23 and 53 each hour (UTC) and also supports manual workflow dispatch from the trusted default branch. Start with manual dispatch and its default `dry_run=true`. Verify the observed status or empty-queue report. Scheduled runs apply updates, and a manual dispatch with `dry_run=false` can apply them too. An attached public run is required to verify the full result transition. |

A GitHub Actions monitoring schedule can read an attached official run without allocating chess compute. Preparing the next packet leaves the public allocation and human form step in force. [Monitor deployment and recovery](monitor.md)

## Verification

On November 20, 2026 the dedicated GitHub-hosted check ran the shared CLI against production. It read the active listing, page one of completed tests, individual status and the official Elo/SPRT response. Historical run `6af9bd9d8e7fb2e78bc1d8ac` returned 85,344 games, an accepted SPRT and an Elo estimate of 1.29. This was a previously completed public test. No login, new submission or public chess compute was used. The local Codex network returned HTTP 403 for the same service. Production verification therefore applies to the GitHub-hosted read path. [Successful live probe](https://github.com/ShadyF4RR/Stockfish/actions/runs/44820757732) · [Official test](https://tests.stockfishchess.org/tests/view/6af9bd9d8e7fb2e78bc1d8ac)
