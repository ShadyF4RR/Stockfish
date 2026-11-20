# Fork integration promotion

The four-hour reconciler reads screened records from `research/ledger`.
The continuous worker's verified handoff prepares their STC packets. The
existing Fishtest CLI manages approved reservations, human form submission,
official result refresh and upstream PR drafting. An upstream draft is
available after passing STC and LTC with `python3 research-lab/fishtest/cli.py
upstream-pr EXP-ID`. These promotion workflows only write to the fork.

## Activation

1. Install the reviewed verifier, CI scripts, tests and group workflow on the
   default and integration branches. Review the human GitHub reviewer list and
   set `automatic_integration_merge` to true in that reviewed checkout before
   enabling queue-only protection. Leave the runtime variable disabled during
   this installation. Apply the integration ruleset in
   `config/github/integration-ruleset.json` as a fork administrator. GitHub
   must support merge queues for this repository. Preserve the protected
   baseline, restrict ledger writes to trusted coordinators, and keep the
   integration writer off all bypass lists. The single-entry queue uses merge
   commits and requires both Actions checks. Administrative changes to those
   protections require human review.
2. Using a separate authenticated fork administrator and immutable reviewed
   checkout, run the ruleset audit below. It reads the server's full list of
   bypass actors and versions before and after the audit. Save the returned
   object in the operator-managed repository variable
   `RESEARCH_PROMOTION_RULESET_CERTIFICATE`. Alternatively set
   `audited_ruleset_version` in a reviewed policy deployment.
   GitHub omits the bypass list for ruleset readers. Verification accepts
   an empty live list or the exact independently audited server version.
   Changes to that version require another audit. Keep administrator
   credentials out of Actions.

   ```sh
   PROMOTION_CONTROL_SHA="$(git rev-parse HEAD)" python3 research-lab/scripts/reconcile_promotion.py audit-rules
   ```

3. Set repository variables `RESEARCH_PROMOTION_CONTROL_SHA` to the full
   reviewed commit and `RESEARCH_PROMOTION_ENABLED=1`. Both opt-ins must
   agree to enable enqueue or publication. Use a dedicated, non-bypass fork
   App token as `RESEARCH_PROMOTION_TOKEN` (contents and pull requests write,
   Actions read, access to fork rules and collaborator permissions). A native
   Actions token restricted to reads verifies merge groups. The controlled
   writer requires no ability to change repo rules or workflows. Verify that
   both identities can read rule history, official public runs and CI. If
   token minting or rotation is required, provision the secret through the
   owner's approved mechanism.
4. Use `reconcile EXP-ID` to inspect a single record. Add `--write` for the
   configured fork publisher, or enable the scheduled `scan --write` job on
   the reviewed default branch. The bounded scan rotates through up to twelve
   records every four hours. Creating a PR early gives a human reviewer time
   to preregister the test. Keep the PR branch intact through verification.

## Admission and confirmation

An eligible human reviews the **exact** engine change, UCI/bench evidence,
regressions and test design before its LTC run exists. A GitHub approval of
that commit must include the following line, with the full candidate SHA.

```text
Register paired-score-fixed-mixture-v1 for first LTC FULL_CANDIDATE_SHA
```

This preregisters the first matching official LTC for the pair and affirms
that material regressions have been resolved. The service verifies the
reviewer's allowlist entry, fork permission, identity distinct from declared
implementers and PR author, exact review SHA, latest review state, aggregate
GitHub approval and live head. The active rules also require latest-push
approval and resolved review threads. Stale, duplicate, late or withdrawn
evidence blocks promotion. Evaluate checks using a disposable valid candidate
and a deliberately invalid one before enabling unattended writes.

Both official STC and LTC are fetched and validated against the original
settings, revisions, benches and submitting account. The reconciler checks
one reservation per stage and the account's completed LTC history for the
registered pair. A missing page, incompatible result or insufficient
statistical evidence blocks promotion. Deleted runs are absent from the public
listing. The registered protocol requires the authorized testing account to
retain failed and superseded runs and never select a winner from retries.

The project lower bound uses paired candidate scores 0, ¼, ½, ¾, 1. To test
a null mean score at most *q*, each pair multiplies a test process by
`1 + λ(score − q)`. Eight positive weights λ, fixed in the verifier before
testing, give eight nonnegative processes. Their equally weighted mixture
remains a test supermartingale when each pair's conditional expected score is
at most *q*. Ville's inequality controls the one-sided false-positive rate
despite optional stopping. Inverting the test at the declared confidence and
converting with `400 log10(q/(1−q))` gives a lower bound in logistic Elo for
the LTC conditions. The fork requires that bound to reach +3. The guarantee
is per preregistered experiment under exchangeable randomized opening-pair
sampling, not a familywise guarantee across a research portfolio.

The controller compares complete Git trees. Integration's engine must match
the tested baseline, the prospective group's engine must match the tested
candidate, and the group's path changes must equal the original engine patch.
An advancing integration branch requires fresh validation. The group workflow
runs the verifier from the trusted SHA. It reads the merge-group event and
waits for the separate trusted correctness/benchmark workflow, checking its
path, repository, event, SHA, latest attempt and successful job. The final
merge decision belongs to GitHub's protected queue. There is no direct
integration ref update, force push or baseline write.

A later scan verifies the landed merge SHA, its exact CI and its presence on
integration. It atomically appends a versioned JSON receipt and cumulative
report to the fork ledger via an ordinary fast-forward, reads back the
receipt, then mentions the owner on the fork PR. Existing receipts and
notification markers make retries safe. An unexpected SHA, missing rules,
expired evidence, unavailable network, detached credential or GitHub refusal
stops the transition. The CLI exits nonzero and records the reason. A scan
continues handling independent records when a candidate is blocked.

[GitHub ruleset API](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset) · [GitHub merge queues](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue) · [Public Fishtest procedures](../fishtest/OPERATIONS.md)
