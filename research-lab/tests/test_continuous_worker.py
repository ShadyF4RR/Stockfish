"""Worker regression tests for pins, source boundaries and real admission/bench parsing."""
import importlib.util
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time
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

    def test_unprivileged_payload_can_traverse_sparse_mount_parents(self):
        # Hosted Bubblewrap sets up mounts as root, then setpriv drops the UID.
        # A same-UID local namespace can also check the bits needed by that UID.
        bwrap = shutil.which("bwrap")
        if not bwrap or subprocess.run([bwrap, "--unshare-user", "--ro-bind", "/", "/", "--", "/bin/true"],
                                      capture_output=True).returncode:
            self.skipTest("requires a usable Bubblewrap user namespace")
        (self.tree / "nets").mkdir()
        sandbox = worker.Sandbox(self.tree, 1, sorted(os.sched_getaffinity(0))[:1], 512, False,
                                 time.monotonic() + 15)
        check = """
import os, subprocess, sys
for parent in ("/etc", "/sys", "/sys/devices", "/sys/devices/system", "/sys/fs",
               "/opt", "/opt/fastchess", "/opt/fastchess/bin"):
    assert os.stat(parent).st_mode & 0o777 == 0o755, parent
assert not os.path.exists("/etc/gitconfig")
assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
assert os.environ["GIT_CONFIG_GLOBAL"] == "/dev/null"
assert subprocess.check_output(["git", "-C", "/source", "rev-parse", "HEAD"], text=True).strip() == sys.argv[1]
"""
        sandbox.run(["python3", "-c", check, self.base], ["--ro-bind", str(self.tree), "/source"],
                    self.tree / "namespace.log")

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
