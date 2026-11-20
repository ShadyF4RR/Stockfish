from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'continuous'))
import cloud_candidate as driver


class CloudCandidateRequestTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'public-fork'
        self.repo.mkdir()
        self.git('init', '--quiet', '--initial-branch=research/baseline')
        self.git('config', 'user.name', 'Control test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.repo / 'src').mkdir()
        (self.repo / 'src/search.cpp').write_text('int margin = 4;\n')
        self.git('add', 'src')
        self.git('commit', '--quiet', '-m', 'baseline')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('switch', '--quiet', '-c', 'experiment/EXP-TEST-001')
        (self.repo / 'src/search.cpp').write_text('int margin = 5;\n')
        self.git('commit', '--quiet', '-am', 'candidate')
        self.req = {'experiment': 'EXP-TEST-001', 'baseline': self.base, 'candidate': self.git('rev-parse', 'HEAD')}

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True).strip()

    def test_stages_exact_public_parent_and_child(self):
        out = self.root / 'staged'
        evidence = driver.stage(self.req, out, fork=str(self.repo))
        self.assertEqual(evidence['source_paths'], ['src/search.cpp'])
        self.assertEqual(driver.git(out / 'baseline', 'rev-parse', 'HEAD'), self.base)
        self.assertEqual(driver.git(out / 'candidate', 'rev-parse', 'HEAD'), self.req['candidate'])

    def test_public_branch_mismatch_and_malicious_input(self):
        with self.assertRaisesRegex(ValueError, 'published candidate branch'):
            driver.stage(dict(self.req, candidate='a' * 40), self.root / 'staged', fork=str(self.repo))
        for unsafe in ('--upload-pack=bad', 'EXP-TEST/../../master', 'EXP-TEST\nrefs/heads/master'):
            with self.subTest(unsafe=unsafe), self.assertRaisesRegex(ValueError, 'experiment ID'):
                driver.checked(dict(self.req, experiment=unsafe))
        self.assertFalse((self.root / 'staged' / 'candidate').exists())

    def test_infrastructure_and_nondirect_descendants_block_before_checkout(self):
        (self.repo / 'README.md').write_text('untrusted candidate change')
        self.git('add', 'README.md')
        self.git('commit', '--quiet', '--amend', '--no-edit')
        with self.assertRaisesRegex(ValueError, 'existing engine source'):
            driver.stage(dict(self.req, candidate=self.git('rev-parse', 'HEAD')),
                         self.root / 'unsafe', fork=str(self.repo))
        self.git('commit', '--allow-empty', '--quiet', '-m', 'second commit')
        with self.assertRaisesRegex(ValueError, 'one distinct isolated commit'):
            driver.stage(dict(self.req, candidate=self.git('rev-parse', 'HEAD')),
                         self.root / 'grandchild', fork=str(self.repo))
