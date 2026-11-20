import copy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "continuous/coordinator_queue.py"
spec = importlib.util.spec_from_file_location("continuous_queue", path)
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def request(action, **values):
    return SimpleNamespace(action=action, rehearsal="123", **values)


def planner(job="one"):
    return request("plan", baseline="a"*40, job=job)


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(queue.CONFIG)
        self.state = queue.bootstrap("123", NOW, self.cfg)

    def test_real_git_race_prevents_second_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = root / "fork.git"
            subprocess.run(["git", "init", "--bare", remote], check=True, capture_output=True)
            clients = []
            for i in range(2):
                directory = root / str(i)
                directory.mkdir()
                clients.append(queue.QueueStore(directory, str(remote), "refs/heads/research/rehearsal/123/queue"))
            first, second = clients
            queue.transact(first, request("init"))
            initial = first.read()[0]
            queue.transact(second, request("init"))
            self.assertEqual(second.read()[0], initial)
            write = first.save
            winner = {}
            def race(tip, state, action):
                winner.update(queue.transact(second, planner("two")))
                write(tip, state, action)
            with patch.object(first, "save", side_effect=race):
                loser = queue.transact(first, planner("one"))
            self.assertEqual((winner["action"], loser["reason"]), ("run", "campaign_active"))
            self.assertEqual(queue.transact(second, planner("two"))["reason"], "job_already_claimed")
            result = dict(status="rejected", reason="local screen", usage=dict(queue.zero(), runner_minutes=4),
                          next_hypotheses=[dict(topic="reversible", path="src/evaluate.cpp",
                                                hypothesis="Measure the reversible discount in an endgame.")])
            finalize = request("finalize", id=winner["id"], lease=winner["lease"], payload=result)
            self.assertEqual(queue.transact(first, finalize)["next_proposals"], ["admitted"])
            finalsha = first.read()[0]
            self.assertTrue(queue.transact(second, finalize)["replayed"])
            self.assertEqual(first.read()[0], finalsha)
            following = queue.transact(second, planner("three"))
            self.assertEqual(following["topic"], "reversible")
            self.assertIn(winner["id"], {h["id"] for h in following["history"]})
            self.assertEqual(len(list(queue.attempts(second.read()[1]))), 2)

    def test_existing_git_transport_without_new_token(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "fork.git"
            directory = root / "client"
            directory.mkdir()
            subprocess.run(["git", "init", "--bare", target], check=True, capture_output=True)
            # This trusted throwaway checkout's Git configuration stands in for
            # the operator's already-authorized HTTPS transport. No token is supplied.
            cfg = root / "approved-config"
            cfg.write_text(f'[url "{target}"]\n  insteadOf = https://github.com/example/fork.git\n')
            store = queue.QueueStore(directory, "https://github.com/example/fork.git", "refs/heads/research/queue",
                                     auth_config=cfg)
            queue.transact(store, SimpleNamespace(action="init", rehearsal=None))
            args = SimpleNamespace(action="cloud-claim", baseline="b"*40, job="hosted1", rehearsal=None,
                                   confirm_hosted_subscription_task=True)
            plan = queue.transact(store, args)
            self.assertEqual(plan["provider"], "subscription_cloud_task")
            self.assertEqual(queue.transact(store, args)["reason"], "job_already_claimed")
            self.assertEqual(store.read()[1]["items"][0]["status"], "running")

    def test_expired_spend_stays_charged_and_lease_is_fenced(self):
        first = queue.plan(self.state, planner(), NOW, self.cfg)
        after = NOW + timedelta(minutes=self.cfg["lease_minutes"] + 1)
        second = queue.plan(self.state, planner("retry"), after, self.cfg)
        self.assertEqual(second["attempt"], 2)
        self.assertNotEqual(first["lease"], second["lease"])
        with self.assertRaisesRegex(ValueError, "obsolete lease"):
            queue.transition(self.state, request("checkpoint", id=first["id"], lease=first["lease"],
                                                 stage="review", payload={}), after, self.cfg)
        result = dict(status="failed", reason="missing API receipt")
        queue.transition(self.state, request("finalize", id=second["id"], lease=second["lease"], payload=result),
                         after, self.cfg)
        accounting = queue.accounting(self.state, after)
        self.assertEqual(accounting["lifetime"]["runner_minutes"], 2*second["budget"]["runner_minutes"])
        self.assertFalse(accounting["reserved"]["runner_minutes"])
        discovery = queue.plan(self.state, planner("discover"), after, self.cfg)
        self.assertEqual(discovery["action"], "research")
        self.assertEqual(discovery["budget"]["cost_usd"], 0)

    def test_admission_and_unknown_or_excess_usage(self):
        for i, expected in enumerate(["admitted", "admitted", "deferred"]):
            p = dict(topic=f"topic-{i}", path="src/search.cpp", hypothesis=f"Change one coefficient numbered {i}")
            self.assertEqual(queue.admit(self.state, p, NOW, self.cfg), expected)
            self.assertEqual(queue.admit(self.state, p, NOW, self.cfg), "duplicate")
        self.assertEqual(queue.count_today(self.state, NOW, "run"), 3)
        with self.assertRaises(ValueError):
            queue.admit(self.state, dict(p, path="../evil"), NOW, self.cfg)
        attempt = queue.plan(self.state, planner(), NOW + timedelta(days=1), self.cfg)
        self.assertEqual(self.state["deferred"], [])
        receipt = dict(status="failed", reason="unplanned cost", usage=dict(queue.zero(), cost_usd=0.000001))
        queue.transition(self.state, request("finalize", id=attempt["id"], lease=attempt["lease"], payload=receipt),
                         NOW + timedelta(days=1), self.cfg)
        self.assertEqual(queue.plan(self.state, planner("next"), NOW + timedelta(days=1), self.cfg)["reason"], "paused")

    def test_production_caps_mandatory_and_checkpoint_replay(self):
        s = queue.bootstrap(None, NOW, self.cfg)
        args = planner()
        self.assertEqual(queue.plan(s, args, NOW, self.cfg)["reason"], "subscription_dispatch_unavailable")
        self.assertEqual(list(queue.attempts(s)), [])
        args.action = "cloud-claim"
        args.confirm_hosted_subscription_task = False
        with self.assertRaisesRegex(ValueError, "operator-authorized"):
            queue.plan(s, args, NOW, self.cfg)
        args.confirm_hosted_subscription_task = True
        self.cfg["daily_budget"]["runner_minutes"] = self.cfg["runner_minutes_per_attempt"]
        self.cfg["lifetime_budget"]["runner_minutes"] = self.cfg["runner_minutes_per_attempt"]
        run = queue.plan(s, args, NOW, self.cfg)
        self.assertEqual(run["provider"], "subscription_cloud_task")
        self.assertEqual(run["budget"], dict(queue.zero(), runner_minutes=self.cfg["runner_minutes_per_attempt"]))
        ck = request("checkpoint", id=run["id"], lease=run["lease"], stage="build", payload={"bench": 17})
        queue.transition(s, ck, NOW, self.cfg)
        self.assertTrue(queue.transition(s, ck, NOW, self.cfg)["replayed"])
        ck.payload = {"bench": 18}
        with self.assertRaisesRegex(ValueError, "conflicting checkpoint"):
            queue.transition(s, ck, NOW, self.cfg)
        queue.transition(s, request("finalize", id=run["id"], lease=run["lease"],
                                    payload=dict(status="failed", reason="retry", retryable=True)), NOW, self.cfg)
        args.job = "second"
        self.assertEqual(queue.plan(s, args, NOW, self.cfg)["reason"], "daily_budget")
        self.assertEqual(queue.plan(s, args, NOW + timedelta(days=1), self.cfg)["reason"], "lifetime_budget")


if __name__ == "__main__":
    unittest.main()
