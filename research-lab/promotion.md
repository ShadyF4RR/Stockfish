# Promotion and protected branches

Run `research-lab/scripts/promote.sh research-lab/experiments/<id>.json` in a
**trusted checkout of the reviewed lab** with its local `research/integration`
branch synchronized to the fork. Prerequisites are Bash, Python 3.8+ and
Git 2.38+. The checker reads policy from its own checkout. Candidate code,
comments, benchmark logs and experiment reports are untrusted data.

The preflight resolves both full commit hashes, requires a nonempty engine
patch and inspects paths and modes. Ordinary experiments can change regular
engine source under `src/`. Changes to the build system, tests, workflow or lab
require a separate infrastructure review. Git computes the prospective merge
tree locally. A conflict or a merged engine source tree different from the
tested candidate requires resolving the difference and retesting.

The record must have status `screened` and populated `evidence.correctness`,
`benchmark`, `review` and `statistical_decision` fields. In `evidence.fishtest`,
use `stc` and `ltc` for the corresponding official test view URLs. Where
upstream guidance makes a stage inapplicable, the value can be an object
with a `not_applicable` string citing that guidance. At least one distinct
official run is required for a functional Elo patch. A populated field or a
URL establishes only that a claim was supplied.

Exit 1 indicates a blocked or invalid record. Exit 2 indicates invalid CLI
usage. Exit 3 prints a JSON preflight result requiring independent verification.
`--help` exits 0. Phase 1 has no merge, push or submission command and needs no
GitHub credentials. The merge-tree computation may write unreachable objects
to the local Git object store. Policy is JSON-compatible YAML.

## Evidence required before enabling writes in Phase 3

- Register candidate, comparator, time controls, openings, compute allocation
  and statistical decision rule before testing. Retain rejected and inconclusive
  tests, including retries. Obtain project authorization and a compute allocation
  before requesting any public Fishtest work.
- Verify official run data independently, including both engine revisions, bench,
  matched settings, completed SPRT outcome and applicability of STC and LTC.
  Require enough evidence for a one-sided 95% lower bound of at least +3 Elo on
  the project's declared rating scale. Use inference valid for the actual
  stopping rule. A fixed-sample interval after optional stopping, local screen,
  speed result, or accepted SPRT with different bounds cannot establish this
  project target by itself. The +3 target is a fork policy.
- Verify signed or service-authenticated provenance for both builds and test
  artifacts. Confirm passing correctness, repeatable nodes signature,
  comparable runtime, independent approval of the latest patch, and resolution
  of material regressions. The reviewer must be different from the implementer.
- Use a protected GitHub merge queue or equivalent mechanism that runs the
  trusted required checks against the **exact integration commit** being
  promoted. Validate workflow identity (repository and approved workflow
  definition), check provider, commit SHA and conclusion against the service.
  A matching check name alone cannot authenticate a workflow. If integration
  advances, invalidate the result and re-evaluate the new merge candidate.
- Once all gates pass, write an immutable versioned record, update the
  cumulative performance report, and notify the owner of the landed SHA and
  measured uncertainty. Reconcile partial failures by commit and experiment ID.
  Keep the fork integration writer separate from any upstream PR credential.
  Never give an integration worker permission to merge upstream.
- Submit functional changes upstream only with official Fishtest result links,
  new bench and a technical explanation following the source revision's
  CONTRIBUTING.md. Apply its AUTHORS and formatting guidance. Record the PR and
  monitor maintainer feedback on the same experiment branch.

## Fork administration

Before opening the lab to unattended writers, a fork administrator must apply
and read back rules for the **exact names** `research/baseline` and
`research/integration`. A local branch or a committed CODEOWNERS file alone
does not install server rules.

- Freeze the baseline at the chosen SHA. Block force updates and deletions.
  Restrict writes or lock this branch, including for automation. Make future
  baseline refreshes explicit maintainer actions.
- On integration require pull requests, at least one independent approval,
  dismissal of stale reviews, approval of the latest push, conversation
  resolution, and the `research-ci` check. Bind the check to GitHub Actions
  and the approved workflow via a ruleset-required workflow where available.
  Enable code-owner review, disallow force updates and deletions, and prevent
  bypass by the promotion identity. Protect the fork's default branch and
  trusted lab workflow definitions before giving any service write access.
- Introduce CODEOWNERS on each target that accepts lab changes. The initial
  owner is the fork account. If that account authors the change, use a separate
  authorized human code owner for approval. Branch protections and CODEOWNERS
  apply from the PR's base branch. Install the reviewed infrastructure on the
  integration branch before enabling automation.
- Keep worker jobs ephemeral, unprivileged and secret-free. Keep the scheduler
  and verifier on reviewed code. Limit a future GitHub App installation to the
  fork and the permissions needed for experimental branches and PRs. Keep
  branch administration, policy edits, and paid-compute approval with humans.
  Check rule enforcement with a disposable invalid promotion attempt before
  allowing unattended operation.

The [baseline](config/github/baseline-ruleset.json) and [integration](config/github/integration-ruleset.json) payloads implement the fork branch rules through the repository rulesets API. They have no bypass actors. The integration check is associated with the GitHub Actions app. These definitions require administrator access to install. List current rules before creating them, preserve existing protections, and read back the created rulesets and protected branches before releasing a writer. Keep trusted workflow identity and exact-commit verification in the separate promotion verifier. The initial infrastructure PR observed both remote branches as unprotected.
