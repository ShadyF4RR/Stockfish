import hashlib
import io
import json
import os
from collections import Counter
from pathlib import Path
import re
import subprocess
import unittest

try:
    import chess.pgn
except ImportError:
    chess = None


CORPUS = Path(__file__).resolve().parents[1] / "adversarial" / "positions.json"
ENTRIES = json.loads(CORPUS.read_text(encoding="utf-8"))["entries"]
FROZEN = {
    "discovery": "c141e818758c5a9f51b98a331171b83dbdf332980e802c765c75b7916fea6507",
    "holdout": "9a19221a1336fc131e37f4a866487da9ed57d64f8e7705f16109e8bc29972884",
}


class AdversarialPositionsTest(unittest.TestCase):
    def test_versioned_splits_and_balance(self):
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        self.assertEqual((corpus["schema_version"], corpus["version"]), (1, "v1"))
        self.assertEqual(len(ENTRIES), 24)
        self.assertEqual(len({entry["id"] for entry in ENTRIES}), len(ENTRIES))
        self.assertEqual(len({entry["family"] for entry in ENTRIES}), len(ENTRIES))
        roots = {tuple(entry["fen"].split()[:4]) for entry in ENTRIES}
        self.assertEqual(len(roots), len(ENTRIES))
        for split, digest in FROZEN.items():
            entries = [entry for entry in ENTRIES if entry["split"] == split]
            encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("ascii")
            self.assertEqual(hashlib.sha256(encoded).hexdigest(), digest, split)
            balance = Counter((e["group"], e["fen"].split()[1]) for e in entries)
            self.assertEqual(balance, {(g, c): 2 for g in ("quiet", "general", "tactical")
                                       for c in ("w", "b")})

    @unittest.skipUnless(chess, "requires chess==1.11.2 for SAN/UCI legal replay")
    def test_replay_san_uci_and_cross_split_transpositions(self):
        routes = {}
        for entry in ENTRIES:
            with self.subTest(entry=entry["id"]):
                game = chess.pgn.read_game(io.StringIO(entry["pgn"]))
                self.assertIsNotNone(game)
                self.assertFalse(game.errors)
                self.assertEqual(game.headers["Result"], "*")
                self.assertEqual([m.uci() for m in game.mainline_moves()], entry["moves"])
                board = chess.Board()
                states = []
                for uci in entry["moves"]:
                    move = chess.Move.from_uci(uci)
                    self.assertIn(move, board.legal_moves)
                    board.push(move)
                    states.append(tuple(board.fen().split()[:4]))
                self.assertEqual(board.fen(), entry["fen"])
                self.assertTrue(board.is_valid())
                self.assertFalse(board.is_game_over())
                routes[entry["id"]] = states
        for entry in ENTRIES:
            opposite = (e for e in ENTRIES if e["split"] != entry["split"])
            for other in opposite:
                self.assertNotIn(routes[entry["id"]][-1], routes[other["id"]],
                                 (entry["id"], other["id"]))

    @unittest.skipUnless(os.environ.get("STOCKFISH_TEST_ENGINE"), "requires a Stockfish executable")
    def test_stockfish_reaches_every_root(self):
        commands = []
        for entry in ENTRIES:
            commands.extend(("position startpos moves " + " ".join(entry["moves"]), "d"))
        result = subprocess.run([os.environ["STOCKFISH_TEST_ENGINE"]],
                                input="\n".join(commands + ["quit", ""]),
                                capture_output=True, text=True, timeout=45, check=True)
        self.assertEqual(re.findall(r"^Fen: (.+)$", result.stdout, re.MULTILINE),
                         [entry["fen"] for entry in ENTRIES])


if __name__ == "__main__":
    unittest.main()
