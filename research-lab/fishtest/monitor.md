# Scheduled monitoring of attached public runs

The GitHub Actions workflow polls the official Fishtest read API twice per hour, at minutes 23 and 53 UTC. It monitors the single public run already attached to an experiment through the lab CLI. A pending proposal, a prepared stage, a reservation without a run ID, or a completed experiment consumes no public query. A scheduled invocation checks only the existing ledger. A successful STC refresh prepares the LTC packet. A successful LTC refresh verifies STC again and invokes the same review and PR-draft gates as the interactive CLI. The scheduler has no public submission or upstream publication operation.

## Enable on the fork

1. Merge the reviewed monitor code and workflow onto the fork's protected default branch. Protect edits to workflows, the trusted Python modules, and the approved fork configuration. GitHub runs scheduled workflows from that branch.
2. Provision the fork branch `research/ledger` from the reviewed infrastructure. It must contain `research-lab/experiments/`. Publish the coordinator's attached run record to that branch. A local checkout on another worker is invisible to this monitor until it has been pushed.
3. Allow this job's GitHub Actions token `contents: write` on the fork. Its only destination is `refs/heads/research/ledger`. The repository's branch rules must allow that ordinary fast-forward update, protect the default, integration and baseline refs, and reject force-pushes/deletions. GitHub's workflow token is repository-scoped. Enforce branch restrictions using server rules appropriate to the fork. Keep PR write permission and branch administration off for this job.
4. Set the repository Actions variable `FISHTEST_MONITOR` to `enabled`. Run **Monitor attached Fishtest runs** on the default branch with **Observe without updating the ledger** checked, then inspect its JSON summary. Scheduled runs publish observations automatically after enablement. Remove the variable to turn off future runs. This opt-in is separate from the research queue scheduler and compute allocations.

The workflow loads code from its triggering trusted default-branch SHA. The ledger is fetched as Git objects into a temporary bare repository. Its Python files, hooks and configuration are never checked out or executed. The worker accepts regular JSON blobs whose filenames match their record IDs. Its only writes are changed experiment JSON records. The ephemeral job token is supplied as `FISHTEST_LEDGER_TOKEN` for Git HTTPS authentication, scoped to the configured fork URL. No Fishtest credential or API key is needed.

## Polling, races and recovery

Each invocation has a seven-minute job timeout. It exits after one ledger scan and performs at most one retry against a newer ledger tip after a conflicting Git push. If a collaborator changed or closed the run during polling, the retry re-evaluates the new record. A protected branch rejecting an update fails the job. GitHub Actions serializes concurrent monitor jobs; cooperating CLI writers still need to pull/reconcile their own checkout before pushing.

An unchanged, healthy official snapshot causes no Git commit. A changed run or state transition is committed on the ledger. A network failure or identity/configuration mismatch retains the last verified result, records a stale observation and fails that attempted poll for visibility. Future attempts automatically back off from 30 minutes through 1, 2, 4, 8 and 16 hours per run, capped at 16 hours. A verified refresh clears the stale marker. A one-off outage can be checked immediately with the interactive CLI. Review a persistent mismatch against the registered packet before altering the ledger.

Run `git log -- research-lab/experiments/<id>.json` from a current ledger checkout to see durable history. The read-only equivalent of the scheduled invocation for a public fork ledger is:

```sh
GITHUB_REPOSITORY=ShadyF4RR/Stockfish python3 -I research-lab/fishtest/scheduled_monitor.py --dry-run
```

The offline test `python3 research-lab/tests/test_scheduled_monitor.py` creates a temporary bare fork, attaches a synthetic official response, polls with and without persistence, and races a real concurrent update. It also verifies that a stale result preserves the previous observation and that backoff suppresses a duplicate read. It does not require GitHub credentials or public test traffic.
