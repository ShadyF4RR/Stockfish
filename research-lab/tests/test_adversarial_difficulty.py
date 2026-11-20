import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

LAB = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("difficulty", LAB / "adversarial/difficulty.py")
metric = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metric)


class Arithmetic(unittest.TestCase):
    def test_unexamined_moves_bound_scarcity(self):
        result = metric.scarcity(.68, [.68, .63, .52], 10, .05)
        self.assertEqual(result["viable_observed"], 2)
        self.assertEqual(result["fraction_outside_tolerance"], [.1, .8])
        self.assertEqual(metric.scarcity(.68, [.68, .63, .52], 3, .05)[
            "fraction_outside_tolerance"], [1/3, 1/3])

    def test_threshold_tie_and_reference_disagreement(self):
        self.assertFalse(metric.regret(.55, .50, .05)["error"])
        self.assertTrue(metric.regret(.551, .50, .05)["error"])
        self.assertEqual(metric.regret(.4, .6, .05)["regret_expected_score"], 0)
        self.assertLess(metric.regret(.4, .6, .05)["signed_expected_score_loss"], 0)

    def test_mixed_cp_models_block_comparison(self):
        native = {"conversion": "engine_wdl", "mate": None}
        fallback = {"conversion": "python_chess_sf16.1", "mate": None}
        mate = {"conversion": "mate_outcome", "mate": 2}
        metric.ensure_comparable([native, mate])
        with self.assertRaises(ValueError):
            metric.ensure_comparable([native, fallback])

    def test_bounded_index_and_empty_eligibility(self):
        self.assertEqual(metric.composite(.5, [0, 1], .5, 1), [.5, .75])
        self.assertEqual(metric.summarize([{"excluded": "terminal"}]), {"eligible_positions": 0})


@unittest.skipUnless(importlib.util.find_spec("chess"), "install chess==1.11.2 for UCI tests")
class ChessSemantics(unittest.TestCase):
    def test_white_and_black_mate_wdl_perspective(self):
        import chess
        import chess.engine as ce
        board = chess.Board()
        board.push_uci("e2e4")
        native = ce.PovWdl(ce.Wdl(100, 300, 600), chess.WHITE)
        view = metric.score_data({"score": ce.PovScore(ce.Cp(70), chess.WHITE),
                                  "wdl": native}, board)
        self.assertEqual(view["wdl"], [600, 300, 100])
        self.assertEqual(view["cp"], -70)
        self.assertEqual(view["expected_score"], .75)
        for score, value in ((ce.Mate(3), 1), (ce.Mate(-2), 0), (ce.MateGiven, 1), (ce.Mate(0), 0)):
            result = metric.score_data({"score": ce.PovScore(score, chess.BLACK)}, board)
            self.assertIsNone(result["cp"])
            self.assertEqual(result["expected_score"], value)
        fallback = metric.score_data({"score": ce.PovScore(ce.Cp(0), chess.BLACK)}, board)
        self.assertEqual(fallback["conversion"], "python_chess_sf16.1")
        self.assertEqual(fallback["expected_score"], .5)
        with self.assertRaises(ValueError):
            metric.score_data({"score": ce.PovScore(ce.Cp(100), chess.BLACK),
                               "lowerbound": True}, board)

    def test_double_forcing_event_counted_once(self):
        import chess
        board = chess.Board("7k/8/8/8/8/8/6p1/K6R b - - 0 1")
        actual = metric.pv_forcing(board, [chess.Move.from_uci("g2h1q")])
        self.assertEqual(actual["forcing_plies"], 1)
        self.assertTrue(actual["pv"][0]["capture"])
        self.assertTrue(actual["pv"][0]["promotion"])
        self.assertTrue(actual["pv"][0]["check"])

    def test_exact_depth_and_restricted_search(self):
        import chess
        import chess.engine as ce
        board = chess.Board()
        move = chess.Move.from_uci("e2e4")
        line = {"score": ce.PovScore(ce.Cp(11), chess.WHITE), "pv": [move], "depth": 5}
        class Engine:
            options = {}
            analyse = AsyncMock(return_value=line)
        row = asyncio.run(metric.search(Engine(), board, 5, 1, forced=move))
        self.assertEqual(row[0]["move"], "e2e4")
        self.assertEqual(Engine.analyse.await_args.kwargs["root_moves"], [move])
        with self.assertRaises(ValueError):
            asyncio.run(metric.search(Engine(), board, 6, 1))
        with self.assertRaises(ValueError):
            asyncio.run(metric.search(Engine(), board, 5, 1, forced=chess.Move.from_uci("d2d4")))

    def test_best_and_observed_move_rescored_independently_at_same_root(self):
        import chess
        board = chess.Board()
        row = {"id": "r", "split": "discovery", "family": "start",
               "group": "general", "fen": board.fen()}
        args = SimpleNamespace(depths=[3, 5], reference_depth=7, opponent_depth=2,
                               timeout=10, multipv=2, threshold=.05)
        ref_calls = []
        def line(move, value):
            return {"move": move, "pv": [move], "score": {
                "expected_score": value, "mate": None, "conversion": "engine_wdl"}}

        async def searches(engine, root, depth, timeout, width=1, forced=None):
            self.assertIs(root, board)
            if engine == "diagnostic":
                return [line("e2e4", .5 if depth == 3 else .6), line("d2d4", .59)]
            if engine == "reference":
                self.assertEqual(depth, 7)
                ref_calls.append(forced.uci() if forced else None)
                if forced is None:
                    return [line("e2e4", .9)]
                return [line(forced.uci(), .55 if forced.uci() == "e2e4" else .48)]
            self.assertEqual((engine, depth), ("opponent", 2))
            return [line("d2d4", .99)]

        with patch.object(metric, "search", searches):
            result = asyncio.run(metric.position(row, board, "diagnostic", "reference",
                                                 {"fam": "opponent"}, args))
        self.assertEqual(ref_calls, [None, "e2e4", "d2d4"])
        self.assertTrue(result["opponents"]["fam"]["error"])
        self.assertAlmostEqual(result["opponents"]["fam"]["regret_expected_score"], .07)
        self.assertAlmostEqual(result["instability"], .1)

    def test_split_and_move_history_integrity(self):
        import chess
        board = chess.Board()
        entry = {"id": "a", "split": "discovery", "moves": [], "fen": board.fen()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.json"
            path.write_text(json.dumps({"schema_version": 1, "entries": [
                entry, dict(entry, id="b", split="holdout")]}))
            self.assertEqual([r["id"] for r,b in metric.load_positions(path, "discovery", 10)], ["a"])
            path.write_text(json.dumps({"schema_version": 1, "entries": [dict(entry, moves=["e2e4"])]}))
            with self.assertRaises(ValueError):
                metric.load_positions(path, "discovery", 10)


if __name__ == "__main__":
    unittest.main()
