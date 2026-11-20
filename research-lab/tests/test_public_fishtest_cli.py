"""Public CLI behavior checked using synthetic responses matching the official API schema."""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'fishtest'))
import cli
import official_results as official
import public_runs

ID = '0123456789abcdef01234567'
HOST = official.HOST


def run_data(mode='sprt'):
    args = {'resolved_base': 'a' * 40, 'resolved_new': 'b' * 40, 'username': 'contributor',
            'tests_repo': 'https://github.com/contributor/Stockfish', 'num_games': 4,
            'new_tag': 'candidate', 'tc': '10+0.1'}
    if mode == 'sprt':
        args['sprt'] = dict(elo0=0, elo1=2, state='accepted', llr=3.12,
                            lower_bound=-2.94, upper_bound=2.94, elo_model='normalized')
    elif mode == 'spsa':
        args['spsa'] = {'iter': 2, 'num_iter': 2, 'params': [{'name': 'constant'}]}
    return dict(_id=ID, args=args, approved=True, finished=True, deleted=False, failed=False,
                workers=0, results=dict(wins=1, draws=2, losses=1, pentanomial=[0, 1, 0, 1, 0]))


def command(args, responses):
    out = io.StringIO()
    with patch.object(sys, 'argv', ['cli.py', *args]), redirect_stdout(out), \
         patch.object(public_runs, 'fetch_json', side_effect=responses) as get:
        code = cli.main()
    return code, json.loads(out.getvalue()), [call.args[0] for call in get.call_args_list]


class PublicFishtestCLI(unittest.TestCase):
    def test_discovery_uses_real_routes_and_preserves_pagination(self):
        row = run_data()
        _, result, paths = command(['completed', '--page', '7', '--limit', '1'], [{ID: row}])
        self.assertEqual(paths, ['/api/finished_runs?page=7'])
        self.assertEqual(result['returned'], 1)
        self.assertEqual(result['runs'][0]['decision'], 'accepted')
        row.update(finished=False, approved=False)
        row['args']['sprt']['state'] = ''
        _, result, paths = command(['active'], [{ID: row}])
        self.assertEqual(paths, ['/api/active_runs'])
        self.assertEqual(result['runs'][0]['status'], 'pending_approval')

    def test_sprt_result_uses_one_coherent_official_snapshot(self):
        row = run_data()
        second = copy.deepcopy(row)
        second['elo'] = dict(elo=1.3, ci=[0.1, 2.5], LOS=0.96)
        _, result, paths = command(['results', f'{HOST}/tests/view/{ID}'], [row, second])
        self.assertEqual(paths, ['/api/get_run/' + ID, '/api/get_elo/' + ID])
        self.assertEqual(result['elo']['interval'], [0.1, 2.5])
        self.assertEqual(result['sprt']['llr'], 3.12)
        second['_id'] = 'f' * 24
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            command(['results', ID], [row, second])

    def test_fixed_game_calculator_and_spsa(self):
        row = run_data('fixed_games')
        _, result, paths = command(['results', ID], [row, {'elo': 0, 'ci': [-30, 30], 'LOS': 0.5}])
        self.assertEqual(paths[-1], '/api/calc_elo?LL=0&LD=1&DDWL=0&WD=1&WW=0')
        self.assertTrue(official.FIXED_ELO.fullmatch(paths[-1]))
        self.assertIsNone(official.FIXED_ELO.fullmatch(paths[-1] + '&other=1'))
        self.assertEqual(result['decision'], 'fixed_sample_completed')
        del row['results']['pentanomial']
        _, _, paths = command(['results', ID], [row, {'elo': 0, 'ci': [-30, 30]}])
        self.assertEqual(paths[-1], '/api/calc_elo?W=1&D=2&L=1')
        _, result, paths = command(['results', ID], [run_data('spsa')])
        self.assertEqual(paths, ['/api/get_run/' + ID])
        self.assertFalse(result['elo']['available'])
        self.assertEqual(result['spsa']['iteration'], 2)

    def test_missing_games_status_and_failed_or_stopped_results(self):
        row = run_data()
        row.update(finished=False, workers=1)
        row['results'] = dict(wins=0, draws=0, losses=0, pentanomial=[0]*5)
        row['args']['sprt']['state'] = ''
        _, result, paths = command(['results', ID], [row])
        self.assertEqual(result['status'], 'running')
        self.assertFalse(result['elo']['available'])
        self.assertEqual(len(paths), 1)
        row.update(finished=True)
        self.assertEqual(command(['status', ID], [row])[1]['decision'], 'inconclusive')
        row.update(failed=True)
        self.assertEqual(command(['status', ID], [row])[1]['decision'], 'failed')

    def test_manual_handoff_is_single_use_and_cannot_write_to_fishtest(self):
        row = dict(id='Demo', baseline_commit='a'*40, candidate_commit='b'*40)
        packet = dict(stage='stc', username='operator', repo='https://github.com/fork/Stockfish',
                      base_sha=row['baseline_commit'], candidate_sha=row['candidate_commit'],
                      base_signature=23, candidate_signature=24, info='Small change')
        auth = dict(stage='stc', approved_server_cap_and_current_rules=True, hours=1,
                    related_review=None, public_checks=dict(finished_pages_scanned=1, similar_description_or_branch=[]))
        row[cli.PUBLIC] = dict(status='reserved', packet=packet, authorizations=[auth])
        config = {'submission_mode': 'human_official_form', 'automatic_public_submission': False}
        with patch.dict(cli.ledger.CONFIG, fishtest=config), patch.object(cli, 'verify_review'), \
             patch.object(cli, 'gate_public_slot', return_value={'similar_description_or_branch': []}), \
             patch.object(cli, 'reconcile_reservation'), patch.object(cli.ledger, 'ledger_lock'), \
             patch.object(cli.ledger, 'load_record', return_value=row), patch.object(cli.ledger, 'save') as save:
            code, result, paths = command(['submit', 'Demo'], [])
            self.assertEqual(code, 3)
            save.assert_called_once()
            self.assertFalse(result['submitted'])
            self.assertEqual(result['form_fields']['test-branch'], row['candidate_commit'])
            self.assertEqual(paths, [])
            with self.assertRaisesRegex(ValueError, 'already issued'):
                command(['submit', 'Demo'], [])
            config['automatic_public_submission'] = True
            with self.assertRaisesRegex(ValueError, 'only human_official_form'):
                command(['submit', 'Demo'], [])
