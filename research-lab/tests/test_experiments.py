"""Run with python3 research-lab/tests/test_experiments.py; uses only a temporary repository."""
import concurrent.futures
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

LAB = Path(__file__).resolve().parents[1]


class LedgerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "ledger"
        self.root.mkdir()
        (self.root / "research-lab/scripts").mkdir(parents=True)
        self.queue = self.root / "research-lab/experiments"
        self.queue.mkdir()
        (self.queue / "README").touch()
        self.cli = self.root / "research-lab/scripts/experiment.py"
        shutil.copyfile(LAB / "scripts/experiment.py", self.cli)
        shutil.copytree(LAB / "config", self.root / "research-lab/config")
        self.git("init", "-b", "research/ledger")
        self.git("config", "user.name", "Ledger test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "seed")
        self.git("branch", "research/baseline")

    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", *args], cwd=cwd or self.root, text=True, stderr=subprocess.PIPE).strip()

    def lab(self, *args, ok=True):
        result = subprocess.run(["python3", self.cli, *args], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return result.stdout.strip()

    def propose(self, name, **kwargs):
        return self.lab("propose", name, name, "search", "hypothesis " + name, **kwargs)

    def test_concurrent_admission_and_recovery(self):
        # Four simultaneous proposals must produce exactly three unique, committed records.
        names = ["first", "second", "third", "fourth"]
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = list(pool.map(lambda n: subprocess.run(
                ["python3", self.cli, "propose", n, n, "search", "hypothesis " + n],
                capture_output=True).returncode, names))
        self.assertEqual(sorted(results), [0, 0, 0, 1])
        a, b, c = [row["id"] for row in json.loads(self.lab("list"))]
        tree = Path(self.lab("start", a))
        self.lab("start", b)
        self.lab("start", c, ok=False)
        self.git("worktree", "remove", str(tree))
        self.assertEqual(Path(self.lab("start", a)), tree)
        self.lab("finish", a, "failed", "compiler failure")
        self.lab("start", c)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_uppercase_seed_performance_role_and_casefolded_identity(self):
        seed = {"id": "EXP-COMPRESSION-001", "topic": "compression-runtime",
                "hypothesis": "Compare the archived and unpacked fixture bytes"}
        self.lab("propose", seed["id"], seed["topic"], "performance", seed["hypothesis"])
        self.propose(seed["id"].lower(), ok=False)
        proposal = json.loads(self.lab("show", seed["id"]))
        payload = self.root.parent / "provenance.json"
        payload.write_text(json.dumps({"baseline_commit": proposal["baseline_commit"],
                                       "candidate_commit": None, "summary": "artifact inventory",
                                       "evidence": {"artifacts": ["example"]}}))
        self.lab("report", seed["id"], "performance", str(payload))
        stored = json.loads(self.lab("show", seed["id"]))
        self.assertEqual(stored["status"], "proposed")
        self.assertEqual(stored["responsible_agent"], "evaluation")
        self.assertEqual(stored["reports"][-1]["role"], "testing")
        tree = self.lab("start", seed["id"])
        self.assertEqual(self.git("branch", "--show-current", cwd=tree), "experiment/" + seed["id"])
        self.assertEqual(json.loads(self.lab("show", seed["id"]))["id"], seed["id"])

    def test_documented_packaging_outcome(self):
        self.propose("packaging")
        self.lab("finish", "packaging", "documented", "measurement", ok=False)
        proposal = json.loads(self.lab("show", "packaging"))
        payload = self.root.parent / "artifact.json"
        payload.write_text(json.dumps({"baseline_commit": proposal["baseline_commit"],
                                       "candidate_commit": None, "summary": "synthetic observation",
                                       "evidence": {"artifacts": ["test fixture"], "compression": {"bytes": 16}}}))
        self.lab("report", "packaging", "performance", str(payload))
        self.lab("finish", "packaging", "documented", "only the archived artifact changed")
        record = json.loads(self.lab("show", "packaging"))
        self.assertEqual(record["status"], "documented")
        self.assertIsNone(record["evidence"]["elo_estimate"])

    def test_evidence_tied_to_commit_and_duplicate_guard(self):
        self.propose("one")
        self.lab("propose", "retry", "one", "search", "changed wording", ok=False)
        tree = Path(self.lab("start", "one"))
        payload = Path(self.root.parent) / "handoff.json"
        original = self.git("rev-parse", "HEAD", cwd=tree)
        payload.write_text(json.dumps({"candidate_commit": original, "summary": "measured", "evidence": {"games": 2}}))
        self.lab("report", "one", "testing", str(payload))
        (tree / "patch").touch()
        self.git("add", "patch", cwd=tree)
        self.git("commit", "-m", "candidate revision", cwd=tree)
        self.lab("report", "one", "testing", str(payload), ok=False)
        self.lab("finish", "one", "screened", "missing evidence", ok=False)
        self.lab("finish", "one", "rejected", "bad candidate")
        record = json.loads(self.lab("show", "one"))
        self.assertEqual(record["reports"][0]["candidate_commit"], original)
        self.assertIsNone(record["evidence"]["games"])
        self.assertEqual(record["source_changes"], ["patch"])
        self.lab("propose", "retry", "one", "search", "retry after rejection", ok=False)
        self.lab("propose", "retry", "one", "search", "retry after rejection", "--retry-reason", "changed mechanism")


if __name__ == "__main__":
    unittest.main()
