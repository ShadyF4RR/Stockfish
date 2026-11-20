"""Candidate edits are deterministic and publication can only create the intended ref."""

from pathlib import Path
import importlib.util
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "continuous/candidate.py"
spec = importlib.util.spec_from_file_location("candidate", FILE)
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


class CandidateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base_repo = self.root / "input"
        self.base_repo.mkdir()
        self.git(self.base_repo, "init", "-b", "master")
        self.path = "src/search.cpp"
        self.file = self.base_repo / self.path
        self.file.parent.mkdir()
        self.file.write_text("#include <algorithm>\nint depth = 12;\n")
        (self.file.parent / "alias.cpp").symlink_to("search.cpp")
        self.git(self.base_repo, "add", ".")
        self.git(self.base_repo, "-c", "user.name=Test", "-c", "user.email=example@test.invalid", "commit", "-m", "baseline")
        self.base = self.git(self.base_repo, "rev-parse", "HEAD")
        self.id = "EXP-CONT-000017"
        self.edits = [{"path": self.path, "old": "12", "new": "13"}]
        self.ref = candidate.branch_ref(self.id, None)

    @staticmethod
    def git(repo, *args):
        return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True).stdout.strip()

    def build(self, repo=None, edits=None, path=None):
        return candidate.reconstruct(repo or self.base_repo, self.base, self.id, path or self.path,
                                     self.edits if edits is None else edits)

    def test_rebuild_without_checkout_and_compare_and_create(self):
        other = self.root / "writer.git"
        self.git(self.root, "clone", "--bare", str(self.base_repo), str(other))
        result = self.build()
        self.assertEqual(result, self.build(other))
        self.assertEqual(self.git(self.base_repo, "status", "--short"), "")
        self.assertEqual(self.git(other, "rev-parse", result + "^"), self.base)
        self.assertEqual(self.git(other, "diff", "--name-only", self.base, result), self.path)
        candidate.create_local_ref(self.base_repo, self.ref, result)
        candidate.create_local_ref(self.base_repo, self.ref, result)
        with self.assertRaisesRegex(ValueError, "another commit"):
            candidate.create_local_ref(self.base_repo, self.ref, self.base)

    def test_nonforce_remote_publication_and_ambiguous_retry(self):
        remote = self.root / "remote.git"
        self.git(self.root, "clone", "--bare", str(self.base_repo), str(remote))
        tested = self.build()
        with patch.object(candidate, "FORK", str(remote)):
            candidate.publish(self.base_repo, self.base, self.ref, tested, "fake-for-local-fixture")
            candidate.publish(self.base_repo, self.base, self.ref, tested, "fake-for-local-fixture")
            self.assertEqual(self.git(remote, "rev-parse", self.ref), tested)
            # Merely pushing without force would fast-forward this preexisting branch.
            self.git(remote, "update-ref", self.ref, self.base, tested)
            with self.assertRaisesRegex(ValueError, "unconfirmed"):
                candidate.publish(self.base_repo, self.base, self.ref, tested, "fake-for-local-fixture")
            self.assertEqual(self.git(remote, "rev-parse", self.ref), self.base)

    def test_rejects_ambiguous_oversized_and_out_of_scope_edits(self):
        for edits in (
            [dict(self.edits[0], path="src/other.cpp")],
            [dict(self.edits[0], old="e")],
            [dict(self.edits[0], old="absent")],
            [dict(self.edits[0], new="13\n#include <cstdio>")],
            [dict(self.edits[0], new="a" * 4097)],
            self.edits + [dict(self.edits[0], old="depth = 12")],
            [dict(self.edits[0], new="\x00")],
        ):
            with self.subTest(edits=repr(edits)[:90]), self.assertRaises(ValueError):
                self.build(edits=edits)
        for path in ("src/alias.cpp", "src/../src/search.cpp", "src/Makefile", "src/universal/new.cpp"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.build(path=path, edits=[dict(self.edits[0], path=path)])

    def test_packet_and_rehearsal_boundaries(self):
        packet = self.root / "packet.json"
        packet.write_text(json.dumps({"edits": self.edits}))
        self.assertEqual(candidate.read_packet(packet), self.edits)
        symlink = self.root / "packet-link"
        symlink.symlink_to(packet)
        with self.assertRaises(OSError):
            candidate.read_packet(symlink)
        packet.write_text('{"edits":[],"edits":[]}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            candidate.read_packet(packet)
        self.assertEqual(candidate.branch_ref("R123-" + self.id, "123"),
                         "refs/heads/research/rehearsal/123/experiment/R123-" + self.id)
        for identifier, run in (("R123-" + self.id, "124"), ("R123-" + self.id, None), (self.id, "123"),
                                ("../main", None)):
            with self.assertRaises(ValueError):
                candidate.branch_ref(identifier, run)


if __name__ == "__main__":
    unittest.main()
