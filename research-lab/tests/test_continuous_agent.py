import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'research-lab' / 'continuous'))
import agent


class RehearsalAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()

    def plan(self):
        return dict(id='EXP-REHEARSAL', source_commit=self.sha, path='src/movepick.cpp',
                    hypothesis='Explicit validation rehearsal', history=[])

    def test_receipts_are_explicit_and_bind_exact_edit(self):
        task = self.plan()
        implementation = agent.execute('implement', task, ROOT)
        task['edits'] = implementation['edits']
        review = agent.execute('review', task, ROOT)
        research = agent.execute('research', task, ROOT)
        for receipt in (implementation, review, research):
            self.assertEqual(receipt['provenance']['adapter'], 'deterministic_rehearsal')
            self.assertIsNone(receipt['provenance']['model'])
            self.assertEqual(receipt['usage']['requests'], 0)
            self.assertEqual(receipt['usage']['cost_usd'], 0)
        self.assertEqual(implementation['provenance']['edits_sha256'], review['provenance']['edits_sha256'])
        self.assertTrue(review['approved'])
        self.assertEqual(research['path'], 'src/evaluate.cpp')
        task['edits'][0]['new'] = task['edits'][0]['new'].replace('rehearsal:', 'different:')
        self.assertFalse(agent.execute('review', task, ROOT)['approved'])

    def test_no_unmarked_provider_no_arbitrary_path_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, out = Path(tmp) / 'task.json', Path(tmp) / 'receipt.json'
            task.write_text(json.dumps(self.plan()))
            cmd = [sys.executable, agent.__file__, 'implement', str(task), str(ROOT), str(out)]
            blocked = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(blocked.returncode, 1)
            self.assertIn('model invocation disabled', blocked.stderr)
            self.assertFalse(out.exists())
            subprocess.run([*cmd, '--rehearsal'], check=True)
            original = hashlib.sha256(out.read_bytes()).digest()
            self.assertNotEqual(subprocess.run([*cmd, '--rehearsal'], capture_output=True).returncode, 0)
            self.assertEqual(original, hashlib.sha256(out.read_bytes()).digest())
        with self.assertRaisesRegex(ValueError, 'C\\+\\+ path'):
            agent.execute('implement', dict(self.plan(), path='src/../.git/config'), ROOT)
