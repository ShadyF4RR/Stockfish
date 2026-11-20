import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "scripts"))
import reconcile_promotion as p
from continuous.promotion_service import APP, CI, FORK, TARGET, GitHub

BASE, HEAD, TIP, CONTROL, GROUP = [c * 40 for c in "12345"]
RUN_IDS = {"stc": "6553f100" + "1" * 16, "ltc": "6553f101" + "2" * 16}


class Service(GitHub):
    def __init__(self):
        self.ruleset = json.loads((LAB / "config/github/integration-ruleset.json").read_text())
        self.review = {"id": 9, "state": "APPROVED", "commit_id": HEAD, "submitted_at": "2023-01-01T00:00:00Z",
                       "user": {"login": "ShadyF4RR", "type": "User"},
                       "body": "Register paired-score-fixed-mixture-v1 for first LTC " + HEAD}
        branch = lambda ref, sha: {"repo": {"full_name": FORK}, "ref": ref, "sha": sha}
        self.pr = {"number": 12, "head": branch("experiment/E1", HEAD), "base": branch(TARGET, TIP),
                   "html_url": "https://github.com/" + FORK + "/pull/12", "user": {"login": "implementer"},
                   "merged": False, "state": "open", "draft": False, "mergeable": True}
        self.changed_control = False
        self.ci_sha = GROUP
        self.writes = []
        self.open_pr = True

    def repo(self, path, *, method="GET", data=None):
        if method != "GET":
            self.writes.append((method, path))
            if path == "pulls":
                return self.pr
            raise AssertionError((path, method))
        if path.startswith("rules/branches/"):
            return [{"type": "merge_queue", "ruleset_id": 7}]
        if path == "rulesets/7/history?per_page=1":
            return [{"version_id": 15}]
        if path == "rulesets/7":
            return self.ruleset
        if path.startswith("pulls?"):
            return [self.pr] if self.open_pr else []
        if path == "pulls/12":
            return self.pr
        if path.startswith("pulls/12/reviews?"):
            return [self.review]
        if path.startswith("collaborators/"):
            return {"permission": "write"}
        if path.startswith("compare/"):
            return {"status": "ahead", "merge_base_commit": {"sha": BASE}}
        if path.startswith("actions/workflows/research-ci.yml/runs?"):
            return {"total_count": 1, "workflow_runs": [{"event": "merge_group", "head_sha": self.ci_sha, "path": CI,
                "run_number": 7, "run_attempt": 1, "status": "completed", "conclusion": "success",
                "repository": {"full_name": FORK}, "id": 8, "html_url": "CI"}]}
        if path.startswith("actions/runs/8/attempts/1/jobs"):
            return {"total_count": 1, "jobs": [{"head_sha": self.ci_sha, "name": "research-ci", "conclusion": "success"}]}
        if path == "git/commits/" + GROUP:
            return {"parents": [{"sha": TIP}, {"sha": HEAD}]}
        raise AssertionError(path)

    def graphql(self, query, variables):
        if query.startswith("mutation"):
            assert variables["head"] == HEAD
            self.writes.append(("mutation", "enqueue"))
            return {"enqueuePullRequest": {"mergeQueueEntry": {"id": 17}}}
        return {"repository": {"pullRequest": {"id": 12, "headRefOid": HEAD, "reviewDecision": "APPROVED",
                                              "isInMergeQueue": False}}}

    def tip(self, branch):
        if branch == TARGET:
            return GROUP if self.pr["merged"] else TIP
        return HEAD

    def tree(self, sha):
        is_new = sha in (HEAD, GROUP)
        same = ("100644", "a" * 40)
        tree = {k: same for k in (CI, p.GROUP_WORKFLOW, "research-lab/scripts", "research-lab/tests", "research-lab/continuous",
                                                   "research-lab/config", "research-lab/fishtest", "tests", "scripts")}
        tree.update({"src": ("040000", "new" if is_new else "old"),
                     "src/search.cpp": ("100644", "new" if is_new else "old")})
        if sha == HEAD and self.changed_control:
            tree[CI] = ("100644", "changed")
        return tree

    def receipt(self, eid, record, pr):
        self.writes.append(("receipt", record["landed_sha"]))


class Promotion(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        review = {"verdict": "approved", "reviewed_sha": HEAD, "reviewer": "independent", "unresolved_defects": []}
        self.record = {"id": "E1", "hypothesis": "example", "status": "screened",
            "baseline_commit": BASE, "candidate_commit": HEAD, "evidence": {"review": review},
            "reports": [{"role": "implementation", "candidate_commit": HEAD,
                         "evidence": {"build_configuration": {"implementer": "implementer"}}},
                        {"role": "reviewer", "candidate_commit": HEAD, "evidence": {"review": review}}],
            "public_testing": {"status": "pr_prepared", "local": {"base_sha": BASE, "candidate_sha": HEAD,
                "username": "ShadyF4RR"}, "authorizations": [{"stage": s} for s in RUN_IDS],
                "runs": {s: {"run_id": ref} for s, ref in RUN_IDS.items()}}}
        self.penta = [0, 10, 1500, 1200, 30]
        self.group = {"head_ref": "refs/heads/gh-readonly-queue/research/integration/pr-12-a123",
                      "base_ref": "refs/heads/" + TARGET, "base_sha": TIP, "head_sha": GROUP}
        def inspect(ref, packet):
            return {"status": "passed", "run_id": ref, "pentanomial": self.penta, "elo": 10.0,
                    "games": 2 * sum(self.penta), "official_snapshot_sha256": "f" * 64, "url": "official"}
        self.enter = patch.object(p.ft, "inspect_run", side_effect=inspect)
        self.enter.start()
        self.addCleanup(self.enter.stop)
        self.history = {RUN_IDS["ltc"]: {"args": {"tc": "60+0.6", "resolved_base": BASE, "resolved_new": HEAD,
                                                "username": "ShadyF4RR"}}}
        listing = patch.object(p.ft, "fetch_json", side_effect=lambda path: self.history)
        listing.start()
        self.addCleanup(listing.stop)
        # Use the coordinator's naming production code; fixture IDs follow the existing ledger format.
        branch = patch.object(p.ft.ledger, "branch", return_value="experiment/E1")
        branch.start()
        self.addCleanup(branch.stop)

    def run_gate(self, **kwargs):
        return p.resolve(self.service, self.record, CONTROL, **kwargs)

    def test_authorized_queue_then_readonly_group_and_land(self):
        self.assertEqual(self.run_gate(write=True)["status"], "queued")
        self.assertEqual(self.service.writes, [("mutation", "enqueue")])
        self.assertEqual(self.run_gate(group=self.group)["status"], "verified_merge_group")
        self.service.pr.update(merged=True, state="closed", merged_at="2023-11-18", merge_commit_sha=GROUP)
        self.assertEqual(self.run_gate(write=True)["status"], "confirmed")
        self.assertEqual(self.service.writes[-1], ("receipt", GROUP))

    def test_read_only_and_missing_pr(self):
        self.assertEqual(self.run_gate()["status"], "eligible_for_queue")
        self.service.open_pr = False
        self.assertEqual(self.run_gate()["status"], "integration_pr_needed")
        self.assertEqual(self.service.writes, [])
        self.assertEqual(self.run_gate(write=True)["status"], "awaiting_approval")
        self.assertEqual(self.service.writes, [("POST", "pulls")])

    def test_stale_head_and_unapproved_reviewer(self):
        self.service.pr["head"]["sha"] = TIP
        with self.assertRaisesRegex(ValueError, "SHA"): self.run_gate(write=True)
        self.service.pr["head"]["sha"] = HEAD
        self.service.review["user"]["login"] = "implementer"
        with self.assertRaisesRegex(ValueError, "independent"): self.run_gate(write=True)
        self.assertFalse(self.service.writes)

    def test_readback_rejects_lookalike_ci_and_queue_base_changed(self):
        self.service.ci_sha = HEAD
        with self.assertRaisesRegex(ValueError, "CI"): self.run_gate(group=self.group)
        self.group["base_sha"] = BASE
        with self.assertRaisesRegex(ValueError, "queue event"): self.run_gate(group=self.group)

    def test_untrusted_workflow_and_bypass_refused(self):
        self.service.changed_control = True
        with self.assertRaisesRegex(ValueError, "infrastructure"): self.run_gate(write=True)
        self.service.ruleset["bypass_actors"] = [{"actor_id": 7}]
        with self.assertRaisesRegex(ValueError, "no-bypass"): self.run_gate(write=True)
        self.assertFalse(self.service.writes)

    def test_small_or_repeated_official_result_is_insufficient(self):
        self.penta = [0, 0, 2, 2, 0]
        with self.assertRaisesRegex(ValueError, "lower bound"): self.run_gate(write=True)
        self.history["6553ff00" + "2" * 16] = next(iter(self.history.values()))
        with self.assertRaisesRegex(ValueError, "multiple"): self.run_gate(write=True)
        self.assertFalse(self.service.writes)

    def test_real_registration_must_precede_official_test(self):
        self.service.review["submitted_at"] = "2025-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "predate"): self.run_gate(write=True)

    def test_readonly_ruleset_audit_certificate(self):
        certificate = self.service.rules(audit=True)
        self.assertEqual(certificate, {"ruleset_id": 7, "version_id": 15})
        del self.service.ruleset["bypass_actors"]
        self.service.rules(certificate)
        with self.assertRaisesRegex(ValueError, "no-bypass"):
            self.service.rules({"ruleset_id": 7, "version_id": 14})
        with self.assertRaisesRegex(ValueError, "no-bypass"): self.service.rules(audit=True)

    def test_atomic_receipt_recovers_from_lost_push_confirmation(self):
        from unittest.mock import Mock
        service = Mock()
        record = {"id": "E1", "landed_sha": GROUP, "base": BASE, "candidate": HEAD,
                  "pr": "https://github.com/ShadyF4RR/Stockfish/pull/12", "ci": "ci", "lower_elo": 4.5}
        path = f"research-lab/results/E1/promotion-{GROUP}.json"
        ledger, staged, comments = {}, {}, []
        service.tree.side_effect = lambda tip: dict(ledger)
        service.tip.return_value = TIP
        service.contents.side_effect = lambda key, tip: ledger[key]
        service.pages.side_effect = lambda route: list(comments)
        service.graphql.return_value = {"viewer": {"login": "trusted-app[bot]"}}
        def request(route, method="GET", data=None):
            if route.startswith("git/commits/"): return {"tree": {"sha": TIP}}
            if route == "git/trees":
                staged.update({item["path"]: item["content"] for item in data["tree"]})
                return {"sha": HEAD}
            if route == "git/commits":
                self.assertEqual(data["parents"], [TIP])
                return {"sha": HEAD}
            if route == "git/refs/heads/research/ledger":
                self.assertIs(data["force"], False)
                ledger.update(staged)
                raise ValueError("connection lost after service accepted transaction")
            if route == "issues/12/comments":
                comments.append({"body": data["body"], "user": {"login": "trusted-app[bot]"}})
                return {"id": 10}
            raise AssertionError(route)
        service.repo.side_effect = request
        with self.assertRaisesRegex(ValueError, "connection lost"):
            GitHub.receipt(service, "E1", record, self.service.pr)
        self.assertEqual(json.loads(ledger[path]), record)
        self.assertIn(GROUP, ledger["research-lab/reports/promoted-experiments.md"])
        GitHub.receipt(service, "E1", record, self.service.pr)
        GitHub.receipt(service, "E1", record, self.service.pr)
        self.assertEqual(len(comments), 1)

    def test_target_bound(self):
        self.assertLess(p.lower_elo([1, 100, 1000, 100, 1], .95), 0)
        self.assertGreater(p.lower_elo(self.penta, .95), 3)
        with self.assertRaises(ValueError): p.lower_elo([1.5] * 5, .95)
