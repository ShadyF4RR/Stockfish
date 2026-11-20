"""Synthetic snapshots shaped like official Fishtest Run schema v24; no jobs submitted."""
import copy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fishtest"))
import official_results as official

RUN_ID = "c" * 24


def fixture(stage="stc"):
    base, candidate = "b" * 40, "c" * 40
    tc = official.STANDARD[stage]
    options = "Hash=16" if stage == "stc" else "Hash=64"
    low, high = (0, 2) if stage == "stc" else (0.5, 2.5)
    repo = "https://github.com/example/Stockfish"
    packet = dict(stage=stage, base_sha=base, candidate_sha=candidate, base_signature=123,
                  candidate_signature=456, repo=repo, tc=tc, threads=1, base_options=options,
                  new_options=options, book="opening.epd", book_depth=8, elo0=low, elo1=high,
                  elo_model="normalized", alpha=0.05, beta=0.05, username="tester", server_sprt_game_limit=800000)
    sprt = dict(elo0=low, elo1=high, elo_model="normalized", alpha=0.05, beta=0.05,
                lower_bound=-math.log(19), upper_bound=math.log(19), llr=2.95, state="accepted")
    args = dict(resolved_base=base, resolved_new=candidate, tc=tc, new_tc=tc, threads=1,
                base_signature="123", new_signature="456",
                base_options=options, new_options=options, book="opening.epd", book_depth="8",
                tests_repo=repo, adjudication=True, username="tester", sprt=sprt, num_games=800000)
    run = dict(_id=RUN_ID, approved=True, finished=True, failed=False, deleted=False,
               is_green=True, args=args, results=dict(wins=21739, losses=21384, draws=42221,
                                                      pentanomial=[81, 8861, 24428, 9226, 76]))
    return packet, run


class OfficialResultTest(unittest.TestCase):
    def test_verified_stages_and_official_intervals(self):
        for stage in ("stc", "ltc"):
            packet, run = fixture(stage)
            elo_run = dict(run, elo={"elo": 1.4, "ci": [0.2, 2.7]})
            with patch.object(official, "fetch_json", side_effect=[run, elo_run]) as read:
                result = official.inspect_run(official.HOST + "/tests/view/" + RUN_ID, packet)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["ci"], [0.2, 2.7])
            self.assertEqual(result["games"], 85344)
            self.assertEqual(read.call_args_list[1].args[0], "/api/get_elo/" + RUN_ID)

    def test_pending_rejected_stopped_deleted_unapproved_and_failed(self):
        for flags, state, expected in [
            ({"finished": False, "is_green": False}, "", "active"),
            ({"finished": False, "approved": False, "is_green": False}, "", "active"),
            ({"is_green": False}, "", "inconclusive"),
            ({"is_green": False}, "rejected", "failed"),
            ({"approved": False}, "accepted", "inconclusive"),
            ({"deleted": True}, "accepted", "inconclusive"),
            ({"failed": True}, "accepted", "failed"),
        ]:
            packet, run = fixture()
            run.update(flags)
            run["args"]["sprt"]["state"] = state
            with self.subTest(flags=flags, state=state):
                self.assertEqual(official.summarize(RUN_ID, packet, run)["status"], expected)

    def test_unverified_or_inconsistent_results_cannot_pass(self):
        packet, original = fixture()
        for route, value in [
            (["_id"], "0" * 24), (["args", "resolved_new"], "0" * 40), (["args", "base_signature"], "321"),
            (["args", "new_tc"], "60+0.6"), (["args", "new_options"], "Hash=1024"),
            (["args", "tests_repo"], "https://github.com/other/Stockfish"), (["args", "arch_filter"], "bmi2"),
            (["args", "num_games"], 10000), (["args", "username"], "other"), (["args", "book_depth"], "0"), (["args", "threads"], True),
            (["args", "sprt", "elo0"], -99), (["args", "sprt", "llr"], float("nan")),
            (["results", "wins"], 999), (["results", "pentanomial"], []), (["finished"], "true"),
        ]:
            run = copy.deepcopy(original)
            target = run
            for key in route[:-1]:
                target = target[key]
            target[route[-1]] = value
            with self.subTest(route=route), self.assertRaises(ValueError):
                official.summarize(RUN_ID, packet, run)
        with self.assertRaises(ValueError):
            official.summarize(RUN_ID, dict(packet, stage="ltc"), original)
        with patch.object(official, "fetch_json", side_effect=[original, ValueError("offline")]):
            with self.assertRaises(ValueError):
                official.inspect_run(RUN_ID, packet)

    def test_filtered_history_get(self):
        path = "/api/finished_runs?page=10&username=Ann+%26+Jo&timestamp=1795200000"
        with patch("urllib.request.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.geturl.return_value = official.HOST + path
            response.headers.get_content_type.return_value = "application/json"
            response.read.return_value = b"{}"
            self.assertEqual(official.fetch_json(path), {})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.full_url, official.HOST + path)
        for invalid in (path + "&success_only=1", path.replace("page=10", "page=11"),
                        path.replace("Ann+%26+Jo", ""), path.replace("Ann+%26+Jo", "invalid%2"),
                        path.replace("1795200000", "179520000")):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                official.fetch_json(invalid)

    def test_endpoints_and_missing_identity(self):
        for path in ["https://other.example/", "//other.example/api/active_runs",
                     "/api/stop_run", "/api/active_runs?redirect=https://other.example"]:
            with self.assertRaises(ValueError):
                official.fetch_json(path)
        for ref in ["http://tests.stockfishchess.org/tests/view/" + RUN_ID, RUN_ID + "?fake", "../" + RUN_ID]:
            with self.assertRaises(ValueError):
                official.canonical_id(ref)
        with patch.object(official, "fetch_json", return_value={"finished": True}):
            with self.assertRaises(ValueError):
                official.inspect_run(RUN_ID, fixture()[0])


if __name__ == "__main__":
    unittest.main()
