"""Worker regression tests for pins, source boundaries and real admission/bench parsing."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

module = Path(__file__).resolve().parents[1] / "continuous/validate_candidate.py"
spec = importlib.util.spec_from_file_location("validation_worker", module)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)
        subprocess.run(["git", "init", "--quiet", self.tree], check=True)
        self.code = self.tree / "src/search.cpp"
        self.code.parent.mkdir()
        self.code.write_bytes(b"namespace Stockfish {\n}\n")
        self.base = self.commit("base\n\nBench: 1234567")

    def commit(self, message):
        subprocess.run(["git", "-C", self.tree, "add", "."], check=True)
        subprocess.run(["git", "-C", self.tree, "-c", "user.name=test", "-c", "user.email=t@localhost",
                        "commit", "-qm", message], check=True)
        return worker.head(self.tree)

    def test_rehearsal_exact_edit(self):
        self.code.write_bytes(worker.MARKER + self.code.read_bytes())
        cand = self.commit("rehearsal")
        self.assertEqual(worker.verify_bench_base(self.tree), 1234567)
        self.assertEqual(worker.check_source(self.tree, self.base, cand, True), ["src/search.cpp"])
        with self.assertRaises(ValueError):
            worker.check_source(self.tree, self.base, self.base, True)

    def test_real_edit_cannot_skip_match(self):
        self.code.write_bytes(worker.MARKER + self.code.read_bytes() + b"int change = 1;\n")
        cand = self.commit("code")
        with self.assertRaises(ValueError):
            worker.check_source(self.tree, self.base, cand, True)
        self.assertEqual(worker.check_source(self.tree, self.base, cand, False), ["src/search.cpp"])

    def test_control_files_rejected(self):
        (self.tree / "src/Makefile").write_text("all:\n\ttrue\n")
        with self.assertRaises(ValueError):
            worker.check_source(self.tree, self.base, self.commit("build-file"), False)


if __name__ == "__main__":
    unittest.main()
