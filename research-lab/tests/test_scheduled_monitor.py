"""Exercise the scheduled monitor with a real bare Git remote and synthetic official snapshots."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fishtest"))
import scheduled_monitor as monitor
from test_official_results import fixture, RUN_ID


class ScheduledMonitorTest(unittest.TestCase):
    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", *args], cwd=cwd or self.operator, stderr=subprocess.PIPE, text=True).strip()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.operator = self.root / "operator"
        self.remote = self.root / "fork.git"
        self.git("init", str(self.operator), "-b", "research/ledger", cwd=self.root)
        self.git("init", "--bare", str(self.remote))
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.identifier = "Example"
        self.path = Path(monitor.DIRECTORY) / (self.identifier + ".json")
        (self.operator / self.path.parent).mkdir(parents=True)
        p, self.run = fixture()
        local = {k: p[k] for k in ("base_sha", "candidate_sha", "base_signature", "candidate_signature", "username")}
        record = dict(id=self.identifier, status="screened", hypothesis="Synthetic fixture",
                      baseline_commit=local["base_sha"], candidate_commit=local["candidate_sha"], evidence={})
        packet = monitor.cli.make_packet(record, local, "stc")
        # The official response must match every registered packet field.
        self.run["args"].update(book=packet["book"], tests_repo=packet["repo"])
        self.run["elo"] = dict(elo=1.4, ci=[0.2, 2.7])
        record[monitor.cli.PUBLIC] = dict(status="active", packet=packet, local=local,
                                         runs={"stc": {"run_id": RUN_ID}}, explanation="Fixture")
        (self.operator / self.path).write_text(json.dumps(record))
        (self.operator / "research-lab/fishtest").mkdir()
        (self.operator / "research-lab/fishtest/cli.py").write_text("raise AssertionError('ledger code ran')")
        self.git("add", ".")
        self.git("commit", "-m", "Fixture")
        self.git("push", str(self.remote), "HEAD:" + monitor.REF)
        bare = self.root / "reader.git"
        bare.mkdir()
        self.store = monitor.LedgerStore(bare, str(self.remote), None)

    def record(self):
        return json.loads(self.git("show", f"{monitor.REF}:{self.path}", cwd=self.remote))

    def test_dry_run_racing_update_and_durable_terminal_progress(self):
        original = self.git("rev-parse", monitor.REF, cwd=self.remote)
        with patch("official_results.fetch_json", return_value=self.run):
            self.assertEqual(monitor.poll(self.store, dry_run=True)["updated"], [self.identifier])
            self.assertEqual(self.git("rev-parse", monitor.REF, cwd=self.remote), original)
            write = self.store.write
            attempts = []
            def race(tip, records):
                if not attempts:
                    data = self.record()
                    data["operator_note"] = "Retain this concurrent update"
                    (self.operator / self.path).write_text(json.dumps(data))
                    self.git("commit", "-am", "Concurrent ledger update")
                    self.git("push", str(self.remote), "HEAD:" + monitor.REF)
                attempts.append(tip)
                return write(tip, records)
            with patch.object(self.store, "write", side_effect=race):
                report = monitor.poll(self.store)
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0], attempts[1])
        self.assertEqual(self.git("rev-parse", monitor.REF, cwd=self.remote), report["committed"])
        data = self.record()
        self.assertEqual(data["operator_note"], "Retain this concurrent update")
        self.assertEqual(data[monitor.cli.PUBLIC]["status"], "prepared")
        self.assertEqual(data[monitor.cli.PUBLIC]["packet"]["stage"], "ltc")
        self.assertEqual(data[monitor.cli.PUBLIC]["runs"]["stc"]["status"], "passed")
        self.assertEqual(monitor.poll(self.store)["updated"], [])
        self.assertEqual(self.git("rev-parse", monitor.REF, cwd=self.remote), report["committed"])

    def test_stale_record_keeps_last_observation_and_backs_off(self):
        with patch("official_results.fetch_json", side_effect=ValueError("unavailable")) as get:
            self.assertEqual(monitor.poll(self.store)["newly_stale"], [self.identifier])
            self.assertEqual(monitor.poll(self.store)["updated"], [])
            self.assertEqual(get.call_count, 1)
        state = self.record()[monitor.cli.PUBLIC]
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["runs"]["stc"], {"run_id": RUN_ID})
        self.assertTrue(state["monitor"]["stale"])
        self.assertEqual(state["monitor"]["consecutive_failures"], 1)


if __name__ == "__main__":
    unittest.main()
