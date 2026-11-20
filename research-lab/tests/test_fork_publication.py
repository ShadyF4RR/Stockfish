"""Exercise the real Git ref used as a shared compare-and-append transaction."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'fishtest'))
from fork_ledger import publish_append


BRANCH = 'research/ledger'


def run(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, text=True, capture_output=True, check=True,
                          env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}).stdout.strip()


class ForkPublication(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.remote = root / 'fork.git'
        run(root, 'init', '--bare', '--initial-branch=' + BRANCH, str(self.remote))
        self.a, self.b = root / 'a', root / 'b'
        run(root, 'clone', str(self.remote), str(self.a))
        self.identity(self.a)
        self.append(self.a, 'seed')
        run(self.a, 'push', 'origin', BRANCH)
        run(root, 'clone', str(self.remote), str(self.b))
        self.identity(self.b)

    def identity(self, repo):
        run(repo, 'config', 'user.name', 'Unit Test Coordinator')
        run(repo, 'config', 'user.email', 'test@example.invalid')

    def append(self, repo, marker):
        p = repo / 'claim'
        p.write_text(marker)
        run(repo, 'add', '--', 'claim')
        run(repo, 'commit', '-m', marker)

    def publish(self, repo, append):
        return publish_append(lambda *args: run(repo, *args), str(self.remote), BRANCH, append)

    def test_racing_coordinators_publish_only_one_handoff(self):
        winner = []
        def race():
            self.append(self.a, 'unique handoff A')
            winner.append(self.publish(self.b, lambda: self.append(self.b, 'unique handoff B')))
        with self.assertRaisesRegex(ValueError, 'publication unconfirmed'):
            self.publish(self.a, race)
        self.assertEqual(winner, [run(self.remote, 'rev-parse', 'refs/heads/' + BRANCH)])
        self.assertEqual(run(self.remote, 'show', 'HEAD:claim'), 'unique handoff B')
        self.assertEqual(run(self.a, 'show', 'HEAD:claim'), 'unique handoff A')
        with self.assertRaisesRegex(ValueError, 'differs from fork'):
            self.publish(self.a, lambda: self.fail('must not change divergent local state'))

    def test_missing_ref_and_push_redirect_block_before_append(self):
        run(self.remote, 'update-ref', '-d', 'refs/heads/' + BRANCH)
        with self.assertRaisesRegex(ValueError, 'cannot verify'):
            self.publish(self.a, lambda: self.fail('missing remote ref'))
        run(self.a, 'remote', 'set-url', '--push', 'origin', str(self.b))
        with self.assertRaisesRegex(ValueError, 'configured fork'):
            self.publish(self.a, lambda: self.fail('unexpected destination'))

    def test_success_requires_readback(self):
        def uncertain(*args):
            reply = run(self.a, *args)
            if args[0] == 'push':
                raise subprocess.TimeoutExpired(['git', 'push'], 60)
            return reply
        with self.assertRaisesRegex(ValueError, 'publication unconfirmed'):
            publish_append(uncertain, str(self.remote), BRANCH, lambda: self.append(self.a, 'uncertain'))
        self.assertEqual(run(self.remote, 'show', 'HEAD:claim'), 'uncertain')
