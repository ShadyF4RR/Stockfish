"""Fetch authoritative fork state; only the constants below identify write destinations."""
import base64
import json
import re
import subprocess
from urllib.parse import quote, urlencode

FORK = "ShadyF4RR/Stockfish"
TARGET = "research/integration"
LEDGER = "research/ledger"
CHECK = "research-ci"
GATE = "research-promotion"
APP = 15368
CI = ".github/workflows/research-ci.yml"


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


class PendingCI(ValueError):
    """An exact group check may still finish within this run's bounded wait."""


class GitHub:
    def api(self, path, *, method="GET", data=None):
        cmd = ["gh", "api", "--hostname", "github.com", "--method", method, path]
        if data is not None:
            cmd.extend(["--input", "-"])
        result = subprocess.run(cmd, input=json.dumps(data) if data is not None else None,
                                capture_output=True, text=True, timeout=45)
        require(result.returncode == 0, f"GitHub refused {method} {path}: {result.stderr.strip()[:500]}")
        return json.loads(result.stdout)

    def repo(self, path, **kwargs):
        return self.api(f"repos/{FORK}/{path}", **kwargs)

    def pages(self, path):
        sep = "&" if "?" in path else "?"
        for n in range(1, 11):
            items = self.repo(path + sep + urlencode({"per_page": 100, "page": n}))
            require(isinstance(items, list), "unexpected paginated response")
            yield from items
            if len(items) < 100:
                return
        raise ValueError("GitHub pagination limit exceeded")

    def tip(self, branch):
        return self.repo("git/ref/heads/" + quote(branch, safe="/"))["object"]["sha"]

    def tree(self, sha):
        # Resolve commits explicitly. Git tree API accepts a tree SHA.
        tree_sha = self.repo(f"git/commits/{sha}")["tree"]["sha"]
        tree = self.repo(f"git/trees/{tree_sha}?recursive=1")
        require(tree["truncated"] is False, "incomplete Git tree")
        return {entry["path"]: (entry["mode"], entry["sha"]) for entry in tree["tree"]}

    def contents(self, path, ref):
        value = self.repo(f"contents/{path}?" + urlencode({"ref": ref}))
        require(value["type"] == "file" and value["encoding"] == "base64", "expected ordinary GitHub file")
        return base64.b64decode(value["content"]).decode()

    def rules(self, certificate=None, *, audit=False):
        active = self.repo("rules/branches/" + quote(TARGET, safe=""))
        for rule_id in {r.get("ruleset_id") for r in active if r.get("type") == "merge_queue"}:
            if type(rule_id) != int:
                continue
            history = f"rulesets/{rule_id}/history?per_page=1"
            before = self.repo(history)[0]["version_id"] if audit else None
            ruleset = self.repo(f"rulesets/{rule_id}")
            if ruleset["enforcement"] != "active":
                continue
            if "bypass_actors" in ruleset:
                if ruleset["bypass_actors"] != []:
                    continue
            elif audit or not certificate or certificate != {
                    "ruleset_id": rule_id, "version_id": self.repo(history)[0]["version_id"]}:
                # GitHub hides bypass actors from tokens without ruleset-write access.
                # An operator can certify an immutable ruleset version with a separate token.
                continue
            rules = {r["type"]: r.get("parameters", {}) for r in ruleset["rules"]}
            queue = rules.get("merge_queue", {})
            reviews = rules.get("pull_request", {})
            checks = rules.get("required_status_checks", {})
            contexts = {(c["context"], c.get("integration_id")) for c in checks.get("required_status_checks", [])}
            if (queue.get("merge_method") == "MERGE" and queue.get("max_entries_to_merge") == 1
                    and queue.get("max_entries_to_build") == 1 and queue.get("grouping_strategy") == "ALLGREEN"
                    and {"deletion", "non_fast_forward"} <= rules.keys()
                    and reviews.get("required_approving_review_count", 0) >= 1
                    and all(reviews.get(k) is True for k in ("dismiss_stale_reviews_on_push", "require_last_push_approval",
                            "require_code_owner_review", "required_review_thread_resolution"))
                    and checks.get("strict_required_status_checks_policy") is True
                    and {(CHECK, APP), (GATE, APP)} <= contexts):
                if audit:
                    after = self.repo(history)[0]["version_id"]
                    require(type(after) is int and after == before, "ruleset changed during audit")
                    return {"ruleset_id": rule_id, "version_id": after}
                return
        raise ValueError("a verified no-bypass single-entry merge queue with both Actions checks is required")

    def prs(self, branch):
        return list(self.pages("pulls?" + urlencode({"state": "all", "base": TARGET,
                                                     "head": FORK.split("/")[0] + ":" + branch})))

    def approved(self, pr, sha, allow, implementers):
        reviews = list(self.pages(f"pulls/{pr['number']}/reviews"))
        latest = {}
        for r in sorted(reviews, key=lambda item: item["id"]):
            if r["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                latest[r["user"]["login"].casefold()] = r
        allowed = {s.casefold() for s in allow} - implementers - {pr["user"]["login"].casefold()}
        approved = []
        for login, r in latest.items():
            if (login in allowed and r["state"] == "APPROVED" and r["commit_id"] == sha
                    and r["user"]["type"] == "User" and r["submitted_at"]
                    and f"Register paired-score-fixed-mixture-v1 for first LTC {sha}" in r["body"].splitlines()):
                permission = self.repo("collaborators/" + quote(login, safe="") + "/permission")["permission"]
                if permission in ("admin", "write", "maintain"):
                    approved.append(r)
        require(approved, "independent authorized human approval of this exact head is missing")
        return min(approved, key=lambda r: r["submitted_at"])

    def graphql(self, query, variables):
        response = self.api("graphql", method="POST", data={"query": query, "variables": variables})
        require(not response.get("errors"), "GitHub GraphQL operation refused")
        return response["data"]

    def queue(self, pr, sha):
        query = """query($n:Int!){repository(owner:"ShadyF4RR",name:"Stockfish"){
          pullRequest(number:$n){id headRefOid reviewDecision isInMergeQueue}}}"""
        live = self.graphql(query, {"n": pr["number"]})["repository"]["pullRequest"]
        require(live["headRefOid"] == sha and live["reviewDecision"] == "APPROVED",
                "PR head moved or GitHub's aggregate review is not approved")
        if live["isInMergeQueue"]:
            return "already_queued"
        mutation = """mutation($id:ID!, $head:GitObjectID!){enqueuePullRequest(
          input:{pullRequestId:$id,expectedHeadOid:$head}){mergeQueueEntry{id}}}"""
        entry = self.graphql(mutation, {"id": live["id"], "head": sha})["enqueuePullRequest"]["mergeQueueEntry"]
        require(entry["id"], "enqueue not confirmed")
        return "queued"

    def trusted_ci(self, sha):
        runs = self.repo("actions/workflows/research-ci.yml/runs?" +
                         urlencode({"event": "merge_group", "head_sha": sha, "per_page": 100}))
        if runs["total_count"] == 0:
            raise PendingCI("exact merge-group CI has not started")
        require(runs["total_count"] <= 100, "ambiguous exact merge-group CI")
        run = max(runs["workflow_runs"], key=lambda r: (r["run_number"], r["run_attempt"]))
        require(run["event"] == "merge_group" and run["head_sha"] == sha and run["path"] == CI
                and run["repository"]["full_name"] == FORK, "exact merge-group CI identity mismatch")
        if run["status"] in ("queued", "in_progress", "pending", "waiting", "requested"):
            raise PendingCI("exact merge-group CI is still running")
        require(run["status"] == "completed" and run["conclusion"] == "success", "exact merge-group CI failed")
        jobs = self.repo(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
        require(jobs["total_count"] <= 100 and any(j["name"] == CHECK and j["head_sha"] == sha
                and j["conclusion"] == "success" for j in jobs["jobs"]), "trusted correctness/bench job missing")
        return run["html_url"]

    def receipt(self, experiment, record, pr):
        # Two files in one ordinary fast-forward transaction. A lost response
        # is retried by checking the immutable receipt in the current ledger.
        tip = self.tip(LEDGER)
        entries = self.tree(tip)
        path = f"research-lab/results/{experiment}/promotion-{record['landed_sha']}.json"
        report = "research-lab/reports/promoted-experiments.md"
        if path in entries:
            old = json.loads(self.contents(path, tip))
            require(all(old[k] == record[k] for k in ("id", "base", "candidate", "landed_sha", "pr")),
                    "promotion receipt collision")
        else:
            summary = self.contents(report, tip) if report in entries else "# Confirmed integration promotions\n\n"
            summary += (f"- {experiment} — {record['landed_sha']} — LTC one-sided lower bound "
                        f"{record['lower_elo']:.3f} logistic Elo. {record['pr']}\n")
            root = self.repo(f"git/commits/{tip}")["tree"]["sha"]
            tree = self.repo("git/trees", method="POST", data={"base_tree": root, "tree": [
                {"type": "blob", "mode": "100644", "path": path, "content": json.dumps(record, indent=2) + "\n"},
                {"type": "blob", "mode": "100644", "path": report, "content": summary}]})
            commit = self.repo("git/commits", method="POST", data={"message": f"Record promotion {experiment}",
                               "tree": tree["sha"], "parents": [tip]})["sha"]
            self.repo("git/refs/heads/" + LEDGER, method="PATCH", data={"sha": commit, "force": False})
            require(json.loads(self.contents(path, self.tip(LEDGER))) == record, "ledger publication unconfirmed")
        marker = f"<!-- verified-integration:{record['landed_sha']} -->"
        thread = f"issues/{pr['number']}/comments"
        actor = self.graphql("query { viewer { login } }", {})["viewer"]["login"]
        require(actor, "authenticated notification author is unknown")
        if not any(marker in c["body"] and c["user"]["login"] == actor for c in self.pages(thread)):
            self.repo(thread, method="POST", data={"body": f"{marker}\n@ShadyF4RR {experiment} landed on "
                      f"`{TARGET}` at `{record['landed_sha']}`. The one-sided sequential LTC lower bound "
                      f"is {record['lower_elo']:.3f} logistic Elo. [CI]({record['ci']})"})
