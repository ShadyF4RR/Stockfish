import copy
import importlib.util
from pathlib import Path
import unittest
import tempfile

path = Path(__file__).resolve().parents[1] / "adversarial/report.py"
spec = importlib.util.spec_from_file_location("adversarial_report", path)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
OPPONENTS = ["stockfish", "independent-a", "independent-b"]
CLOCKS = ["1+0.01", "3+0.03"]


def fixture():
    positions, runs = {}, []
    for split in report.SPLITS:
        positions[split] = {}
        for n in range(3):
            i = split + str(n)
            positions[split][i] = dict(family=split+str(n//2), group="quiet", fen="fixture-"+i)
        for opp in OPPONENTS:
            for clock in CLOCKS:
                for arm in ("baseline", "candidate"):
                    games = []
                    for i, entry in positions[split].items():
                        for color in ("white", "black"):
                            if opp == "stockfish":
                                result = "1/2-1/2"
                            elif arm == "baseline" and i.endswith("2"):
                                result = "1/2-1/2"
                            else:
                                result = "1-0" if color == "white" else "0-1"
                            games.append(dict(position_id=i, **entry, engine_color=color, result=result, termination="normal",
                                              engine_score=report.from_white(result, color)))
                    runs.append(dict(split=split, opponent=opp, time_control=clock, arm=arm, games=games))
    return positions, runs


class AdversarialStatistics(unittest.TestCase):
    def test_raw_counts_family_weights_and_independent_units(self):
        pos, runs = fixture()
        out = report.summarize(runs, OPPONENTS, CLOCKS, pos)
        self.assertEqual(out["total_games"], 144)
        row = out["splits"]["holdout"]["practical_aggregate"]
        self.assertEqual(row["baseline_wdl"], [16, 8, 0])
        self.assertEqual(row["candidate_wdl"], [24, 0, 0])
        self.assertEqual(row["win_delta"]["effect"], .5)
        self.assertEqual(row["win_delta"]["group_count"], 2)
        self.assertEqual(row["win_delta"]["decision"], "inconclusive")
        self.assertEqual(row["score_delta"]["effect"], .25)
        self.assertEqual(out["splits"]["holdout"]["objective"]["aggregate"]["score_vs_even"]["effect"], 0)

    def test_invalid_or_incomplete_cells(self):
        pos, runs = fixture()
        for invalid in (runs[:-1], runs+[runs[0]]):
            with self.assertRaises(ValueError):
                report.summarize(invalid, OPPONENTS, CLOCKS, pos)
        wrong = copy.deepcopy(runs)
        wrong[0]["games"][0]["engine_score"] = 1
        with self.assertRaisesRegex(ValueError, "perspective"):
            report.summarize(wrong, OPPONENTS, CLOCKS, pos)
        wrong[0]["games"] = wrong[0]["games"][1:]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            report.summarize(wrong, OPPONENTS, CLOCKS, pos)
        pos["holdout"]["holdout0"]["family"] = "discovery0"
        with self.assertRaisesRegex(ValueError, "overlap"):
            report.summarize(runs, OPPONENTS, CLOCKS, pos)

    def test_forfeits_yield_completion_bounds_and_remove_entire_matched_opening(self):
        pos, runs = fixture()
        for run in runs:
            if (run["split"], run["opponent"], run["arm"]) == ("holdout", "independent-a", "candidate"):
                run["games"][0]["termination"] = "time forfeit"
        data = report.incomplete_contrasts(runs, OPPONENTS, CLOCKS, pos)["holdout"]
        cell = next(c for c in data["cells"] if c["opponent"] == "independent-a")
        self.assertEqual(cell["normal_complete_openings"], 2)
        self.assertEqual(cell["candidate_abnormal"], 1)
        self.assertEqual(cell["candidate_wdl"], [6, 0, 0])
        self.assertEqual(cell["normal_pair_candidate_wdl"], [4, 0, 0])
        self.assertLess(cell["bounds"]["win"][0], cell["bounds"]["win"][1])
        self.assertLessEqual(data["practical"]["win"]["sample_range"][0], 0.5)
        self.assertEqual(data["practical"]["win"]["uncertainty_envelope"], [-1, 1])
        self.assertEqual(data["objective"]["score"]["sample_range"], [0, 0])

    def test_pgn_identity_color_clock_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            fen = "4k3/8/8/8/8/8/8/4K3 w - - 2 7"
            body = (f'[Round "1"]\n[White "vs_fruit"]\n[Black "arm_candidate"]\n'
                    f'[FEN "{fen}"]\n[Result "0-1"]\n[Termination "normal"]\n[TimeControl "1+0.01"]\n\n'
                    'fixture 0-1\n')
            pgn = out / "test.pgn"
            pgn.write_text(body)
            game = dict(round=1, engine_color="black", fen=fen, termination="normal", result="0-1")
            run = dict(pgn=pgn.name, pgn_sha256=report.digest(pgn), arm="candidate", opponent="fruit",
                       time_control="1+0.01", games=[game])
            report.verify_pgn(out, run)
            with self.assertRaisesRegex(ValueError, "clock"):
                report.verify_pgn(out, dict(run, time_control="3+0.03"))
            with self.assertRaisesRegex(ValueError, "checksum"):
                report.verify_pgn(out, dict(run, pgn_sha256="0"*64))
            game["fen"] = fen.replace("2 7", "0 7")
            with self.assertRaisesRegex(ValueError, "observation"):
                report.verify_pgn(out, run)

    def test_perfect_win_ceiling_has_nonzero_interval(self):
        pos, runs = fixture()
        for run in runs:
            for g in run["games"]:
                g.update(result="1-0" if g["engine_color"] == "white" else "0-1", engine_score=1)
        out = report.summarize(runs, OPPONENTS, CLOCKS, pos)
        practical = out["splits"]["holdout"]["practical_aggregate"]
        self.assertEqual(practical["baseline_win_headroom"], 0)
        self.assertEqual(practical["win_delta"]["effect"], 0)
        self.assertLess(practical["win_delta"]["interval"][0], 0)
        self.assertGreater(practical["win_delta"]["interval"][1], 0)
        self.assertIsNone(out["splits"]["holdout"]["objective"]["aggregate"]["score_vs_even"]["logistic_elo"])


if __name__ == "__main__":
    unittest.main()
