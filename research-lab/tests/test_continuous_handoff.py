"""Synthetic local screens and real local Git refs; this suite never submits a public job."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'continuous'))
import handoff as h


class Handoff(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo, self.remote = self.root / 'control', self.root / 'fork.git'
        self.git('init', '-q', '-b', 'master', str(self.repo))
        self.git('init', '--bare', '-q', str(self.remote))
        self.git('-C', str(self.repo), 'config', 'user.name', 'Fixture')
        self.git('-C', str(self.repo), 'config', 'user.email', 'test@example.invalid')
        (self.repo / 'src').mkdir()
        (self.repo / 'src/search.cpp').write_text('int depth = 3;\n')
        exp = self.repo / 'research-lab/experiments'
        exp.mkdir(parents=True)
        (exp / 'README').touch()
        self.git('-C', str(self.repo), 'add', '.')
        self.git('-C', str(self.repo), 'commit', '-qm', 'Fixture control')
        self.git('-C', str(self.repo), 'push', str(self.remote), 'HEAD:refs/heads/master')
        self.base = self.git('-C', str(self.repo), 'rev-parse', 'HEAD')
        self.plan = dict(action='run', rehearsal=None, source_commit=self.base, id='EXP-CONT-000001',
                         topic='depth', hypothesis='Change one search depth.', path='src/search.cpp')
        self.edits = [{'path': self.plan['path'], 'old': '= 3;', 'new': '= 4;'}]
        self.head = h.candidate.reconstruct(self.repo, self.base, self.plan['id'], self.plan['path'], self.edits)
        role = dict(adapter='subscription_cloud_task', experiment_id=self.plan['id'], source_commit=self.base,
                    task_sha256='c' * 64, edits_sha256=h.stable_hash(self.edits), candidate_commit=self.head)
        self.impl = dict(edits=self.edits, provenance=dict(role, role='implement', response_id='local_'+'1'*32),
                         usage={'cost_usd': 0})
        self.review = dict(approved=True, reason='Reviewed the pinned edit.', usage={'cost_usd': 0},
                           provenance=dict(role, role='review', response_id='local_'+'2'*32))
        self.artifact = dict(id='123', sha256='0'*64)
        self.out = self.root / 'worker'
        self.out.mkdir()
        self.env = dict(GITHUB_REF='refs/heads/master', GITHUB_WORKFLOW_REF=h.WORKFLOW)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE, text=True).strip()

    def stage(self, name, data):
        f = self.out / name
        f.parent.mkdir(exist_ok=True)
        f.write_text(data if isinstance(data,str) else json.dumps(data))

    def worker(self):
        builds = {}
        for side, sha in (('baseline', self.base), ('candidate', self.head)):
            pin = dict(commit=sha, binary_sha256=hashlib.sha256(sha.encode()).hexdigest())
            builds[side] = dict(**pin, build_flags={'ARCH':'avx2'}, hardware={'machine':'x64'},
                                compiler='fixture', arch='x86', compiler_binary_sha256='d'*64, networks={})
            self.stage(side+'/build.json', builds[side])
            self.stage(side+'/benchmark.json', dict(**pin, signature=123456, command=['stockfish','bench'],
                                                 runs=[{'nodes':123456,'nps':1000}]*2))
            self.stage(side+'/validation.json', dict(**pin, uci='passed', perft='passed', reprosearch='passed'))
        book, pgn = [], []
        for i in range(16):
            pawn_file = i % 8
            pawn_rank = (str(pawn_file) if pawn_file else '') + 'P' + (str(7-pawn_file) if pawn_file < 7 else '')
            ranks = ['4k3', '8', '8', '8', '8', '8', '8', '4K3']
            ranks[6 - i // 8] = pawn_rank
            position = '/'.join(ranks) + ' w - - 0 1'
            book.append(position)
            for w,b,score in (('candidate','baseline','1-0'),('baseline','candidate','0-1')):
                pgn.append(f'[Round "{i+1}"]\n[White "{w}"]\n[Black "{b}"]\n[Result "{score}"]\n'
                           f'[FEN "{position}"]\n[Termination "normal"]\n\n{score}\n\n')
        self.stage('match/openings.epd','\n'.join(book))
        self.stage('match/games.pgn',''.join(pgn))
        from local_match import results, sha256
        measured = results(self.out/'match/games.pgn', 16, {' '.join(f.split()[:4]) for f in book})
        self.stage('match/match.json', dict(**builds, **measured, stage='C', status='screened', settings={'pairs':16},
                                           pgn_sha256=sha256(self.out/'match/games.pgn'),
                                           book_sha256=sha256(self.out/'match/openings.epd')))
        worker = dict(status='screened', decision='escalate', rehearsal=False, baseline_sha=self.base,
                      candidate_sha=self.head, source_paths=[self.plan['path']], benchmark={'baseline':123456,'candidate':123456},
                      games=32, compute={'wall_seconds':1}, evidence_sha256={name:sha256(self.out/name) for name in h.FILES})
        self.stage('result.json', worker)
        return worker

    def test_real_git_packet_replay_and_collision(self):
        worker = self.worker()
        with patch.object(h,'LAB',self.repo/'research-lab'), patch.object(h.ft,'LAB',self.repo/'research-lab'):
            record = h.screen_record(self.plan,self.impl,self.review,self.out,worker,self.artifact)
        self.assertEqual(record[h.ft.PUBLIC]['packet']['stage'],'stc')
        self.assertEqual(record[h.ft.PUBLIC]['status'],'prepared')
        self.assertEqual(record[h.ft.PUBLIC]['runs'],{})
        store_path = self.root / 'bare'
        store_path.mkdir()
        store = h.LedgerStore(store_path,str(self.remote),None)
        status, tip = h.publish(store,record,self.repo,self.base)
        self.assertEqual(status,'prepared')
        self.assertEqual(h.publish(store,record,self.repo,self.base),('already_prepared',tip))
        conflict = copy.deepcopy(record)
        conflict['continuous_handoff']['artifact']['id'] = '999'
        with self.assertRaisesRegex(ValueError,'different handoff'):
            h.publish(store,conflict,self.repo,self.base)
        self.assertEqual(self.git('-C',str(self.remote),'rev-parse',h.REF),tip)
        self.assertEqual(self.git('-C',str(self.remote),'rev-parse','refs/heads/master'), self.base)

    def test_rehearsal_stale_master_review_identity_and_changed_bytes(self):
        self.assertEqual(h.handoff(dict(self.plan,rehearsal='123'),{},{},self.out,self.env)['status'],'skipped')
        self.assertEqual(h.handoff(self.plan,{},{},self.out,{})['status'],'skipped')
        worker = self.worker()
        with patch.object(h,'LAB',self.repo/'research-lab'), patch.object(h.ft,'LAB',self.repo/'research-lab'), \
             patch.object(h,'verify_artifact',return_value=self.artifact), patch.object(h.ft,'public_ref',return_value='1'*40):
            self.assertEqual(h.handoff(self.plan,self.impl,self.review,self.out,self.env)['status'],'awaiting_cloud_tasks')
            with patch.object(h, 'approved_tasks', return_value=True):
                self.assertEqual(h.handoff(self.plan,self.impl,self.review,self.out,self.env)['status'],'rebase_required')
            review = copy.deepcopy(self.review)
            review['provenance']['response_id'] = self.impl['provenance']['response_id']
            with self.assertRaisesRegex(ValueError,'coincide'):
                h.screen_record(self.plan,self.impl,review,self.out,worker,self.artifact)
            self.stage('candidate/validation.json',{})
            with self.assertRaisesRegex(ValueError,'digest mismatch'):
                h.screen_record(self.plan,self.impl,self.review,self.out,worker,self.artifact)

    def test_cloud_task_bindings_require_reviewed_control_commit(self):
        controlled = self.repo / "research-lab"
        bind = self.repo / h.TASK_BINDINGS / (self.plan["id"] + ".json")
        bind.parent.mkdir(parents=True)
        (controlled / "config/continuous.json").write_text(json.dumps({"subscription_cloud": {
            "approved_environment_id": "fixture-cloud-env"}}))
        binding = dict(environment_id="fixture-cloud-env", experiment_id=self.plan["id"],
                       source_commit=self.base)
        for role, response in (("implement", self.impl), ("review", self.review)):
            binding[role] = {"task_id": response["provenance"]["response_id"],
                             "result_sha256": h.stable_hash(response)}
        bind.write_text(json.dumps(binding))
        with patch.object(h, "LAB", controlled):
            self.assertFalse(h.approved_tasks(self.plan, self.impl, self.review))
            self.git("-C", str(self.repo), "add", "research-lab/config")
            self.git("-C", str(self.repo), "commit", "-qm", "Pin synthetic imported task outputs")
            self.assertTrue(h.approved_tasks(self.plan, self.impl, self.review))
            self.assertFalse(h.approved_tasks(self.plan, self.impl, dict(self.review, reason="Changed")))

    def test_official_archive_binding(self):
        workflow = dict(repository=h.REPO,run_id='123',run_attempt='1',sha=self.base,workflow_ref=h.WORKFLOW)
        result = dict(workflow=workflow, games=32)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('result.json',json.dumps(result))
        raw = stream.getvalue()
        sha = hashlib.sha256(raw).hexdigest()
        artifact = dict(id=42,name='evidence-first-1',expired=False,size_in_bytes=len(raw),digest='sha256:'+sha,
                        workflow_run=dict(id=123,head_sha=self.base,head_branch='master',
                                          repository_id=1465801587,head_repository_id=1465801587))
        env = dict(**{'GITHUB_'+k.upper():v for k,v in workflow.items()}, GITHUB_WORKFLOW_SHA=self.base,
                   GH_TOKEN='fixture',WORKER_ARTIFACT_ID='42',WORKER_ARTIFACT_NAME=artifact['name'],WORKER_ARTIFACT_DIGEST=sha)
        def mock_gh(cmd,**kwargs):
            if cmd[-1].endswith('/zip'):
                kwargs['stdout'].write(raw)
            return subprocess.CompletedProcess(cmd,0,stdout=json.dumps(artifact).encode())
        with patch.object(h.candidate,'oid',return_value=self.base),patch.object(h.subprocess,'run',side_effect=mock_gh):
            self.assertEqual(h.verify_artifact(result,env)['sha256'],sha)
            with self.assertRaisesRegex(ValueError,'local result differs'):
                h.verify_artifact(dict(result,games=99),env)
            artifact['workflow_run']['id'] += 1
            with self.assertRaisesRegex(ValueError,'metadata differs'):
                h.verify_artifact(result,env)


if __name__ == '__main__':
    unittest.main()
