import copy
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'fishtest'))
import cli


class FishtestWorkflow(unittest.TestCase):
    def setUp(self):
        self.local = dict(base_sha='a' * 40, candidate_sha='b' * 40, base_signature=123,
                          candidate_signature=456, username='operator')
        review = dict(verdict='approved', reviewed_sha=self.local['candidate_sha'], reviewer='reviewer', unresolved_defects=[])
        self.record = dict(id='Example', hypothesis='Isolate one search constant.', status='screened',
                           baseline_commit=self.local['base_sha'], candidate_commit=self.local['candidate_sha'],
                           evidence={'review': review}, reports=[
                               dict(role='reviewer', candidate_commit=self.local['candidate_sha'], evidence={'review': review}),
                               dict(role='implementation', candidate_commit=self.local['candidate_sha'],
                                    evidence={'build_configuration': {'implementer': 'coder'}})])
        self.packet = cli.make_packet(self.record, self.local, 'stc')
        self.state = dict(status='reserved', packet=self.packet, runs={}, patch_id='patch',
                          explanation='Technical explanation.', local=self.local, authorizations=[])
        self.record[cli.PUBLIC] = self.state
        self.reserve_args = SimpleNamespace(hours=1.0, pages=1, related_review=None)

    def result(self, stage, status):
        return dict(status=status, run_id=stage[0] * 24, games=4, wdl=dict(wins=2, draws=2, losses=0),
                    url='https://tests.stockfishchess.org/tests/view/' + stage[0] * 24,
                    settings={'tc': '10+0.1' if stage == 'stc' else '60+0.6'},
                    sprt=dict(llr=3, lower_bound=-2.944, upper_bound=2.944))

    def test_accepted_sequence_prepares_ltc_and_pr_automatically(self):
        stc = self.result('stc', 'passed')
        ltc = self.result('ltc', 'passed')
        with patch.object(cli, 'inspect_run', side_effect=[stc, ltc, stc]):
            cli.update(self.record, SimpleNamespace(action='attach', run=stc['run_id']))
            self.assertEqual(self.state['status'], 'prepared')
            self.assertEqual(self.state['packet']['tc'], '60+0.6')
            self.assertNotIn('upstream_pr_body', self.state)
            self.state['status'] = 'reserved'
            cli.update(self.record, SimpleNamespace(action='attach', run=ltc['run_id']))
            self.assertEqual(self.state['status'], 'pr_prepared')
            for text in ('Bench: 456', stc['url'], ltc['url'], self.local['candidate_sha']):
                self.assertIn(text, self.state['upstream_pr_body'])

    def test_negative_terminal_result_does_not_advance(self):
        for status in ('failed', 'inconclusive'):
            record = copy.deepcopy(self.record)
            with patch.object(cli, 'inspect_run', return_value=self.result('stc', status)):
                cli.update(record, SimpleNamespace(action='attach', run='123'))
            self.assertEqual(record[cli.PUBLIC]['status'], status)
            self.assertEqual(record[cli.PUBLIC]['packet']['stage'], 'stc')
            self.assertNotIn('upstream_pr_body', record[cli.PUBLIC])

    def test_duplicate_and_single_public_slot(self):
        self.state['status'] = 'prepared'
        args = dict(username='different', tests_repo='https://github.com/X/Y', resolved_base='a' * 40,
                    resolved_new='b' * 40, tc='10+0.1')
        with patch.dict(cli.ledger.CONFIG, public_fishtest_budget_hours=5, public_fishtest_username='operator',
                        public_fishtest_max_games_approved={'stc': 800000}), \
             patch.object(cli, 'verify_refs'), patch.object(cli.ledger, 'records', return_value=[self.record]), \
             patch.object(cli, 'fetch_json', side_effect=[{}, {'duplicate': {'args': args}}]):
            with self.assertRaisesRegex(ValueError, 'equivalent test'):
                cli.gate_public_slot(self.record, self.packet, self.reserve_args)
            self.state['status'] = 'reserved'
            with self.assertRaisesRegex(ValueError, 'occupied'):
                cli.gate_public_slot(self.record, self.packet, self.reserve_args)

    def test_master_and_budget_blocks(self):
        with patch.object(cli, 'public_ref', return_value='c' * 40):
            with self.assertRaisesRegex(ValueError, 'master advanced'):
                cli.verify_refs(self.packet)
        with patch.dict(cli.ledger.CONFIG, public_fishtest_budget_hours=0, public_fishtest_username='operator',
                        public_fishtest_max_games_approved={'stc': 800000}):
            with self.assertRaisesRegex(ValueError, 'allocation'):
                cli.gate_public_slot(self.record, self.packet, self.reserve_args)

    def test_no_evidence_without_screen_and_review(self):
        for record in ({'status': 'proposed'}, {'status': 'screened', 'evidence': {}}):
            with self.assertRaises(ValueError):
                cli.local_evidence(record, None, None, None, None, 'test')

    def test_unsupported_budget_and_independence(self):
        with patch.dict(cli.ledger.CONFIG, public_fishtest_budget_hours=1, public_fishtest_username='operator',
                        public_fishtest_max_games_approved={'stc': 0}):
            with self.assertRaisesRegex(ValueError, 'server-enforced'):
                cli.gate_public_slot(self.record, self.packet, self.reserve_args)
        self.record['evidence']['review']['reviewer'] = 'CODER'
        with self.assertRaisesRegex(ValueError, 'distinct reviewer'):
            cli.verify_review(self.record)

    def test_release_rejects_incomplete_account_history(self):
        self.state['authorizations'] = [dict(time_utc='2026-11-20T12:00:00+00:00')]
        other = dict(args=dict(username='operator', resolved_base='x', resolved_new='y', tc='10+0.1'))
        full = {str(i): other for i in range(50)}
        with patch.object(cli, 'fetch_json', side_effect=[{}, full]) as fetch:
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                cli.reconcile_reservation(self.state, 1)
        self.assertIn('username=operator&timestamp=', fetch.call_args.args[0])
        with patch.object(cli, 'fetch_json', side_effect=[{}, full, {}]):
            cli.reconcile_reservation(self.state, 2)

    def test_locally_archived_record_never_advances(self):
        self.record['status'] = 'rejected'
        with patch.object(cli, 'inspect_run', return_value=self.result('stc', 'passed')):
            cli.update(self.record, SimpleNamespace(action='attach', run='123'))
        self.assertEqual(self.state['status'], 'archived_locally')
        self.assertEqual(self.state['packet']['stage'], 'stc')
