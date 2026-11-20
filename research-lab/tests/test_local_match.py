"""Regression checks for paired screening and admission without running engines."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from local_match import START, opening_key, results
from worker_resources import MIB, allocation


class MatchTests(unittest.TestCase):
    def test_en_passant_book_and_pgn_identity(self):
        no_capture = "r1b1kbnr/pp1nq3/2pp1pp1/4p2p/2PPP2B/2N2N2/PP2BPPP/R2QK2R w KQkq h6"
        self.assertEqual(opening_key(no_capture), no_capture.replace(" h6", " -"))
        legal = "7k/8/8/5pP1/8/8/8/K7 w - f6"
        self.assertEqual(opening_key(legal), legal)
        pinned = "7k/8/8/r4pPK/8/8/8/8 w - f6"
        self.assertEqual(opening_key(pinned), pinned.replace(" f6", " -"))

    def test_admit_both_engines_and_manager(self):
        cfg = {"hash_mib": 16, "minimum_engine_mib": 384}
        limits = {"usable_mib": 12000, "cpu_ids": list(range(6))}
        plan = allocation(limits, cfg, 133 * MIB, 2)
        self.assertEqual(plan["concurrency"], 3)
        self.assertEqual(plan["peak_budget_mib"], 7 * plan["process_limit_mib"])
        low_mem = dict(limits, usable_mib=plan["peak_budget_mib"] - 1)
        with self.assertRaises(ValueError):
            allocation(low_mem, cfg, 133 * MIB, 2, 3)
        with self.assertRaises(ValueError):
            allocation(limits, cfg, 133 * MIB, 2, 4)

    def test_complete_color_reversed_pair_required(self):
        def game(white, result):
            black = "baseline" if white == "candidate" else "candidate"
            return (f'[Round "1"]\n[White "{white}"]\n[Black "{black}"]\n'
                    f'[Result "{result}"]\n[Termination "normal"]\n\n{result}\n\n')

        first = game("candidate", "1-0")
        second = game("baseline", "1/2-1/2")
        book = {" ".join(START.split()[:4])}
        with tempfile.TemporaryDirectory() as tmp:
            pgn = Path(tmp) / "games.pgn"
            pgn.write_text(second + first)
            summary = results(pgn, 1, book)
            self.assertEqual((summary["wins"], summary["draws"], summary["losses"]), (1, 1, 0))
            self.assertEqual(summary["pentanomial"], [0, 0, 0, 1, 0])
            self.assertEqual(summary["decision"], "inconclusive")
            self.assertEqual(summary["score_ci95"], [0, 1])
            for invalid in (first, first + first, first + second.replace("normal", "time forfeit")):
                pgn.write_text(invalid)
                with self.assertRaises(ValueError):
                    results(pgn, 1, book)


if __name__ == "__main__":
    unittest.main()
