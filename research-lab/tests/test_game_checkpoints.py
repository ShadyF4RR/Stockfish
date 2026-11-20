import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import checkpoint_games as journal
from local_match import opening_key


class GameCheckpoints(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.out = root / "results"
        self.out.mkdir()
        self.log = root / "starts"
        self.manager = root / "fixture"
        self.manager.write_text('''#!/usr/bin/python3
import pathlib, sys
p = pathlib.Path
white, black = ("baseline","candidate") if "-reverse" in sys.argv else ("candidate","baseline")
fen = p("book.epd").read_text().strip()
with p(%r).open("a") as f: print(fen,white,file=f)
p("game.pgn").write_text(f'[White "{white}"]\\n[Black "{black}"]\\n[Round "1"]\\n[FEN "{fen}"]\\n[TimeControl "4+0.04"]\\n[Termination "normal"]\\n[Result "1/2-1/2"]\\n\\n1. e4 e5 1/2-1/2\\n')
''' % str(self.log))
        self.manager.chmod(0o755)
        self.rows = ["4k3/8/8/8/8/8/4P3/4K3 w - - 0 1","4k3/5p2/8/8/8/8/8/4K3 b - - 0 1"]
        self.record = dict(settings=dict(pairs=2,seed=9,time_control="4+0.04",hash_mib=16,threads=1,concurrency=2),
                           candidate=dict(binary_sha256="a"),baseline=dict(binary_sha256="b"),
                           allocation=dict(concurrency=2),book_sha256="c",fastchess_sha256="d")

    def play(self):
        journal.play([str(self.manager),"-openings"],self.rows,self.out,self.record,time.monotonic()+5,
                     lambda plan: None,opening_key)

    def test_resume_untouched_slots_without_replaying_completed_games(self):
        original = journal.completed
        def stop_after_first_wave(*args):
            value = original(*args)
            if len(self.log.read_text().splitlines()) == 2:
                raise KeyboardInterrupt()
            return value
        with patch.object(journal,"completed",side_effect=stop_after_first_wave):
            with self.assertRaises(KeyboardInterrupt):
                self.play()
        self.play()
        self.play()
        self.assertEqual(len(set(self.log.read_text().splitlines())),4)
        self.assertEqual(len(self.log.read_text().splitlines()),4)
        self.assertEqual((self.out/"games.pgn").read_text().count('[Round "2"]'),2)

    def test_lost_pgn_never_restarts_a_game(self):
        self.play()
        (self.out/"game-001-candidate/game.pgn").unlink()
        with self.assertRaisesRegex(ValueError,"require reconciliation"):
            self.play()
        self.assertEqual(len(self.log.read_text().splitlines()),4)
        self.assertEqual((self.out/"games.pgn").read_text().count("[Round "),3)
