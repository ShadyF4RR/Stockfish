#!/usr/bin/env python3
"""Reconcile verified strength experiments through the fork's protected merge queue."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlencode

LAB = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(LAB), str(LAB / "fishtest")]
from continuous.promotion_service import CI, FORK, LEDGER, TARGET, GitHub, PendingCI, require
import cli as ft

POLICY = json.loads((LAB / "config/promotion.yaml").read_text())
SHA = re.compile("[a-f0-9]{40}")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
GROUP = re.compile(r"(?:refs/heads/)?gh-readonly-queue/research/integration/pr-([1-9][0-9]*)-[a-f0-9]+")
GROUP_WORKFLOW = ".github/workflows/promotion-group.yml"


def lower_elo(penta, confidence):
    """Invert a fixed mixture of nonnegative test supermartingales over opening pairs."""
    require(len(penta) == 5 and all(type(n) is int and n >= 0 for n in penta)
            and sum(penta) > 0 and 0 < confidence < 1, "invalid paired confidence sequence inputs")
    fractions = (.0125, .025, .05, .1, .2, .4, .8, .95)
    threshold = -math.log1p(-confidence)
    lo, hi = 0.0, 1.0
    for _ in range(55):
        q = (lo + hi) / 2
        wealth = [sum(n * math.log1p(bet * (i / 4 - q)) for i, n in enumerate(penta))
                  for bet in fractions]
        peak = max(wealth)
        log_e = peak + math.log(sum(math.exp(x - peak) for x in wealth) / len(wealth))
        if log_e >= threshold:
            lo = q
        else:
            hi = q
    return 400 * math.log10(lo / (1 - lo)) if 0 < lo < 1 else None


def official(record, approval, inspect=None, listing=None):
    inspect = inspect or ft.inspect_run
    listing = listing or ft.fetch_json
    state = record["public_testing"]
    require(state["status"] == "pr_prepared", "official STC and LTC are incomplete")
    ft.verify_review(record)
    require(ft.same_pins(record, state["local"]), "record differs from registered test revisions")
    account = ft.ledger.CONFIG["public_fishtest_username"]
    require(state["local"]["username"] == account, "public account changed")
    require(all((sum(1 for a in state["authorizations"] if a["stage"] == stage and not a.get("released")) == 1)
                for stage in ("stc", "ltc")), "require a single unreleased registration per stage")
    reports = {}
    for stage in ("stc", "ltc"):
        packet = ft.make_packet(record, state["local"], stage)
        report = inspect(state["runs"][stage]["run_id"], packet)
        require(report["status"] == "passed", "official " + stage + " failed or unfinished")
        reports[stage] = report
    ltc = reports["ltc"]
    reviewed = datetime.fromisoformat(approval["submitted_at"].replace("Z", "+00:00")).timestamp()
    require(reviewed < int(ltc["run_id"][:8], 16), "independent approval must predate the LTC registration")
    # The single preregistered experiment may use only its first matching LTC.
    # The immutable review time is authored by GitHub; BSON run IDs carry server creation time.
    matching = set()
    for page in range(1, 11):
        rows = listing("/api/finished_runs?" + urlencode({"page": page, "username": account,
                                                         "timestamp": int(reviewed) - 86400}))
        for run_id, run in rows.items():
            args = run["args"]
            require(args["username"] == account, "official account filter returned other owner")
            if ((args["resolved_base"], args["resolved_new"], args["tc"]) ==
                    (record["baseline_commit"], record["candidate_commit"], "60+0.6")):
                matching.add(run_id)
        if len(rows) < 50:
            break
    else:
        raise ValueError("incomplete official registration history")
    require(matching == {ltc["run_id"]}, "multiple or missing official LTC runs")
    bound = lower_elo(ltc["pentanomial"], POLICY["minimum_one_sided_confidence"])
    require(bound is not None and bound >= POLICY["project_target_elo"],
            f"sequential lower bound {bound} below project threshold")
    return {"lower_elo": bound, "confidence": POLICY["minimum_one_sided_confidence"],
            "method": "paired-score-fixed-mixture-v1", "runs": {k: {f: r[f] for f in
            ("url", "official_snapshot_sha256", "games", "pentanomial", "elo")} for k, r in reports.items()}}


def eligible_source(service, record, target, control, proposed=None):
    base, head = record["baseline_commit"], record["candidate_commit"]
    require(all(isinstance(s, str) and SHA.fullmatch(s) for s in (base, head, target, control))
            and base != head, "invalid or identical Git pins")
    relation = service.repo(f"compare/{base}...{head}?per_page=1")
    require(relation["status"] == "ahead" and relation["merge_base_commit"]["sha"] == base,
            "candidate is not a descendant of its registered baseline")
    b, h, t, c = [service.tree(s) for s in (base, head, target, control)]
    changed = {p for p in b.keys() | h.keys() if b.get(p) != h.get(p)
               and (h.get(p) or b[p])[0] != "040000"}
    require(changed and all(p.startswith("src/") and p != "src/Makefile" and
            all(tree.get(p, ("100644",))[0] == "100644" for tree in (b, h)) for p in changed),
            "candidate changes infrastructure, modes or non-engine paths")
    require(t["src"] == b["src"], "integration engine differs from tested comparator; retest required")
    for p in (CI, GROUP_WORKFLOW, "research-lab/scripts", "research-lab/tests", "research-lab/continuous",
              "research-lab/config", "research-lab/fishtest", "tests", "scripts"):
        require(t[p] == c[p], f"trusted CI or helper differs at {p}")
    if proposed:
        g = service.tree(proposed)
        differences = {p for p in g.keys() | t.keys() if g.get(p) != t.get(p)
                       and (g.get(p) or t[p])[0] != "040000"}
        require(g["src"] == h["src"] and differences == changed, "queue tree is not exactly the tested engine patch")


def identify(pr, record):
    require(pr["head"]["repo"]["full_name"] == pr["base"]["repo"]["full_name"] == FORK
            and pr["base"]["ref"] == TARGET and pr["head"]["ref"] == "experiment/" + record["id"]
            and pr["head"]["sha"] == record["candidate_commit"], "PR fork, branches or SHA mismatch")


def resolve(service, record, control, write=False, group=None, retry_ci=False):
    eid = record["id"]
    require(ID.fullmatch(eid) and record["status"] == "screened", "a screened registered experiment is required")
    base, head = record["baseline_commit"], record["candidate_commit"]
    certificate = POLICY.get("audited_ruleset_version")
    if os.environ.get("PROMOTION_RULESET_CERTIFICATE"):
        certificate = json.loads(os.environ["PROMOTION_RULESET_CERTIFICATE"])
        require(isinstance(certificate, dict) and set(certificate) == {"ruleset_id", "version_id"}
                and all(type(value) is int and value > 0 for value in certificate.values()), "invalid ruleset certificate")
    service.rules(certificate)
    branch = "experiment/" + eid
    prs = service.prs(branch)
    require(len(prs) <= 1, "ambiguous integration PR history")
    pr = service.repo(f"pulls/{prs[0]['number']}") if prs else None
    tip = service.tip(TARGET)
    proposed = group["head_sha"] if group else None
    if pr:
        identify(pr, record)
        require(pr["state"] == "open" or pr["merged"], "integration PR already closed without merging")
        if pr["merged"]:
            require(not group, "already merged")
            proposed = pr["merge_commit_sha"]
            merge = service.repo("git/commits/" + proposed)
            require(len(merge["parents"]) == 2, "expected protected queue merge commit")
            tip = merge["parents"][0]["sha"]
            current = service.tip(TARGET)
            require(current == proposed or service.repo(f"compare/{proposed}...{current}?per_page=1")
                    ["merge_base_commit"]["sha"] == proposed, "landed commit is absent from integration")
    if group:
        ref = GROUP.fullmatch(group["head_ref"])
        require(ref and pr and int(ref[1]) == pr["number"] and group["base_ref"] == "refs/heads/" + TARGET
                and group["base_sha"] == tip and SHA.fullmatch(proposed), "queue event or base mismatch")
    eligible_source(service, record, tip, control, proposed)
    if not pr:
        require(not group and service.tip(branch) == head, "live candidate ref moved or missing")
        if not write:
            return {"id": eid, "status": "integration_pr_needed"}
        pr = service.repo("pulls", method="POST", data={"base": TARGET, "head": branch,
            "title": f"Verify and integrate {eid}", "body": "Independent engine review required before LTC. "
            f"Compare `{base}` and `{head}`. Inspect correctness, benchmarks, one-variable scope and material regressions. If approved, include this exact line in the review:\n\n"
            f"Register paired-score-fixed-mixture-v1 for first LTC {head}"})
        identify(pr, record)
        return {"id": eid, "status": "awaiting_approval", "pr": pr["html_url"]}
    implementers = {str(r["evidence"].get("build_configuration", {}).get("implementer", "")).casefold()
                    for r in record["reports"] if r["role"] == "implementation"}
    if not group and not pr["merged"] and record.get("public_testing", {}).get("status") != "pr_prepared":
        return {"id": eid, "pr": pr["html_url"], "status": "awaiting_registered_official_evidence"}
    approval = service.approved(pr, head, POLICY["independent_github_reviewers"], implementers)
    data = official(record, approval)
    receipt = {**data, "id": eid, "base": base, "candidate": head, "pr": pr["html_url"],
               "review_id": approval["id"], "reviewer": approval["user"]["login"]}
    if proposed:
        # The two merge_group workflows can finish in either order.
        for i in range(37 if retry_ci else 1):
            try:
                ci = service.trusted_ci(proposed)
                break
            except PendingCI:
                if not retry_ci or i == 36:
                    raise
                time.sleep(55)
        if group:
            return {**receipt, "status": "verified_merge_group", "group_sha": proposed, "ci": ci}
        receipt.update(landed_sha=proposed, ci=ci, merged_at=pr["merged_at"])
        if write:
            service.receipt(eid, receipt, pr)
        return {**receipt, "status": "confirmed"}
    require(pr["mergeable"] is True and not pr["draft"] and service.tip(branch) == head,
            "candidate moved, is a draft, or cannot merge cleanly")
    return {**receipt, "status": service.queue(pr, head) if write else "eligible_for_queue"}


def process(args):
    require(POLICY["fork"] == FORK and POLICY["integration_branch"] == TARGET
            and POLICY["automatic_upstream_submission"] is False, "invalid promotion destinations")
    control = os.environ.get("PROMOTION_CONTROL_SHA", "")
    require(SHA.fullmatch(control) and subprocess.check_output(["git", "-C", str(LAB.parent), "rev-parse", "HEAD"],
            text=True).strip() == control, "run only at the configured trusted control SHA")
    active = POLICY["automatic_integration_merge"] is True and os.environ.get("RESEARCH_PROMOTION_ENABLED") == "1"
    require(not (args.write or args.action == "merge-group") or active, "integration automation is disabled")
    service = GitHub()
    if args.action == "audit-rules":
        print(json.dumps(service.rules(audit=True), indent=2))
        return 0
    ledger = service.tip(LEDGER)
    group = None
    if args.action == "pr-head":
        require(os.environ.get("GITHUB_EVENT_NAME") == "pull_request" and os.environ.get("GITHUB_REPOSITORY") == FORK,
                "fork integration PR event required")
        pr = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())["pull_request"]
        eid = pr["head"]["ref"].removeprefix("experiment/")
        require(ID.fullmatch(eid), "invalid experiment branch")
        record = json.loads(service.contents(f"research-lab/experiments/{eid}.json", ledger))
        identify(pr, record)
        eligible_source(service, record, service.tip(TARGET), control)
        print(json.dumps({"status": "verified_engine_scope", "candidate": record["candidate_commit"]}))
        return 0
    if args.action == "merge-group":
        require(os.environ.get("GITHUB_EVENT_NAME") == "merge_group" and os.environ.get("GITHUB_REPOSITORY") == FORK,
                "trusted merge_group Actions event required")
        group = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())["merge_group"]
        ref = GROUP.fullmatch(group["head_ref"])
        require(ref and os.environ["GITHUB_SHA"] == group["head_sha"], "queue ref or event SHA mismatch")
        pr = service.repo("pulls/" + ref[1])
        require(pr["head"]["ref"].startswith("experiment/"), "queue head is not an experiment")
        ids = [pr["head"]["ref"].removeprefix("experiment/")]
    elif args.action == "scan":
        pattern = re.compile(r"research-lab/experiments/([A-Za-z0-9_-]+)\.json")
        ids = sorted(m[1] for p in service.tree(ledger) if (m := pattern.fullmatch(p)))
        if ids:
            start = (int(time.time() // 14400) * 12) % len(ids)
            ids = (ids[start:] + ids[:start])[:12]
    else:
        ids = [args.id]
    rows = []
    for eid in ids:
        require(isinstance(eid, str) and ID.fullmatch(eid), "invalid experiment id")
        record = json.loads(service.contents(f"research-lab/experiments/{eid}.json", ledger))
        require(record["id"] == eid, "ledger identity mismatch")
        if args.action == "scan" and record.get("status") != "screened":
            continue
        try:
            rows.append(resolve(service, record, control, args.write, group, retry_ci=group is not None))
        except (ValueError, KeyError, TypeError) as exc:
            if args.action != "scan":
                raise
            rows.append({"id": eid, "status": "blocked", "reason": str(exc)})
    print(json.dumps(rows, indent=2))
    return 1 if any(r["status"] == "blocked" for r in rows) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("reconcile", "scan", "merge-group", "pr-head", "audit-rules"))
    parser.add_argument("id", nargs="?")
    parser.add_argument("--write", action="store_true", help="open/enqueue eligible fork PRs or publish confirmed receipts")
    args = parser.parse_args()
    try:
        sys.exit(process(args))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(1, f"promotion blocked: {exc}\n")
