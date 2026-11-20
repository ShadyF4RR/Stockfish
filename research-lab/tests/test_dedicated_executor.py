"""Dedicated executor protocol fixtures. These do not attest a physical worker."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

MOD = Path(__file__).resolve().parents[1] / "dedicated-worker/executor.py"
spec = importlib.util.spec_from_file_location("dedicated_executor", MOD)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class DedicatedExecutor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.request = dict(experiment="EXP-TEST-001", baseline="a"*40, candidate="b"*40,
                            pairs=16, seed=1234, time_control="4+0.04")
        self.path = self.root / "request.json"

    def test_request_integrity_and_bounds(self):
        self.path.write_text(json.dumps(self.request))
        self.assertEqual(worker.read_request(self.path), (self.request, hashlib.sha256(self.path.read_bytes()).hexdigest()))
        for changes in ({"seed": True}, {"seed": 0}, {"seed": 2**32}, {"pairs": 51},
                        {"pairs": True}, {"candidate": self.request["baseline"]},
                        {"time_control": "3600+90"}, {"worker": "pc"}):
            with self.subTest(changes=changes):
                self.path.write_text(json.dumps(dict(self.request, **changes)))
                with self.assertRaises(ValueError):
                    worker.read_request(self.path)
        self.path.write_text(json.dumps(self.request).replace('"seed":', '"seed": 1, "seed":'))
        with self.assertRaises(ValueError):
            worker.read_request(self.path)

    def test_nested_memory_max(self):
        root = self.root / "cg"
        child = root / "slice/unit"
        child.mkdir(parents=True)
        membership = self.root / "self"
        membership.write_text("0::/slice/unit\n")
        (root / "slice/memory.max").write_text(str(10 * 1024**3))
        (child / "memory.max").write_text("max")
        self.assertEqual(worker.memory_max(root, membership), 10240)
        (child / "memory.max").write_text(str(6 * 1024**3))
        self.assertEqual(worker.memory_max(root, membership), 6144)
        membership.write_text("0::/../\n")
        with self.assertRaises(ValueError):
            worker.memory_max(root, membership)

    def test_layouts_cover_thread_tradeoffs_within_all_caps(self):
        cfg = json.loads((worker.LAB / "config/testing.yaml").read_text())["local_match"]
        limits = dict(usable_mib=10240, cpu_ids=list(range(8)))
        for primary_pairs in (1, 2, 16, 50):
            layouts = worker.layouts(limits, cfg, 108*1024**2, primary_pairs)
            self.assertEqual(sorted(set(row["threads"] for row in layouts)), [1, 2, 4])
            self.assertEqual(len(layouts), 4)
            self.assertLessEqual(len(layouts)*2*worker.CALIBRATION_PAIRS, 16)
            for plan in layouts:
                self.assertLessEqual(plan["concurrency"], min(4, 2*primary_pairs))
                self.assertLessEqual(plan["concurrency"]*plan["threads"], len(limits["cpu_ids"]))
                self.assertLessEqual(worker.allocation(limits, cfg, 108*1024**2, plan["threads"],
                                                       plan["concurrency"])["peak_budget_mib"], limits["usable_mib"])
        self.assertEqual(worker.layouts(dict(cpu_ids=[2], usable_mib=3000), cfg, 108*1024**2, 2),
                         [dict(threads=1, concurrency=1)])

    def test_outcome_blind_disjoint_seeded_book_partitions(self):
        src = self.root / "all"
        rows = [f"4k3/8/8/8/8/8/{row}/4K3 w - - 0 1" for row in
                ("P7", "1P6", "2P5", "3P4", "4P3", "5P2", "6P1", "7P")]
        src.write_text("\n".join(rows)+"\n")
        cal, main = self.root / "cal", self.root / "main"
        worker.books(src, cal, main, 21, 4)
        raw = (cal.read_bytes(), main.read_bytes())
        self.assertEqual(len(cal.read_text().splitlines()), 2)
        self.assertEqual(len(main.read_text().splitlines()), 4)
        self.assertFalse(set(cal.read_text().splitlines()) & set(main.read_text().splitlines()))
        src.write_text("\n".join(reversed(rows))+"\n")
        worker.books(src, cal, main, 21, 4)
        self.assertEqual(raw, (cal.read_bytes(), main.read_bytes()))
        with self.assertRaises(ValueError):
            worker.books(src, cal, main, 21, 9)

    def test_non_windows_surface_rejected_for_pc(self):
        with (mock.patch.object(worker.os, "getuid", return_value=1000),
              mock.patch.object(worker, "memory_max", return_value=8192),
              mock.patch.object(worker.platform, "release", return_value="6.8.0-generic")):
            with self.assertRaisesRegex(ValueError, "WSL2"):
                worker.admit("pc", {})

    def test_archived_pgn_revalidation_and_corruption(self):
        source = worker.LAB / "results/phase1-throughput/2games-1threads"
        pgn = self.root / "games.pgn"
        pgn.write_bytes((source / "games.pgn").read_bytes())
        original = pgn.read_text()
        book = self.root / "subset.epd"
        fens = re.findall(r'^\[FEN "([^"]+)"\]$', original, re.M)
        book.write_text("\n".join(sorted(set(fens)))+"\n")
        record = json.loads((source / "match.json").read_text())
        # Fixture changes the book binding to the two actually observed positions.
        record["book_sha256"] = worker.sha256(book)
        worker.write_json(self.root / "match.json", record)
        args = (self.root, book, record["settings"], 2, dict(threads=1, concurrency=2),
                [record["baseline"], record["candidate"]], record["fastchess_sha256"])
        self.assertEqual(worker.verify_batch(*args)["games"], 4)
        for old, replacement in (('TimeControl "4+0.04"', 'TimeControl "40+0.4"'),
                                 ("normal", "time forfeit"), (fens[0], fens[0][:-1]+"7")):
            with self.subTest(old=old):
                pgn.write_text(original.replace(old, replacement))
                # Even an updated PGN digest cannot hide a different clock, root or termination.
                record["pgn_sha256"] = worker.sha256(pgn)
                worker.write_json(self.root / "match.json", record)
                with self.assertRaises(ValueError):
                    worker.verify_batch(*args)

    def test_partial_or_forfeit_results_count_for_budget(self):
        pgn = self.root / "games.pgn"
        pgn.write_text('[Result "1-0"]\n[Termination "time forfeit"]\n\n1. e4 1-0\n\n[Result "*"]\n')
        self.assertEqual(worker.counted_games(pgn), 1)


if __name__ == "__main__":
    unittest.main()
