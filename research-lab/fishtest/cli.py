#!/usr/bin/env python3
"""Prepare and monitor standard Fishtest SPRTs. All service requests are read-only."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4
from urllib.parse import urlencode

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / 'scripts'))
import experiment as ledger
from local_evidence import local_evidence, require, verify_review
from official_results import fetch_json, inspect_run
from public_runs import listing, page_number, read_run
from fork_ledger import publish_append

OFFICIAL = 'https://github.com/official-stockfish/Stockfish'
FORK = 'https://github.com/' + json.loads((LAB / 'config/promotion.yaml').read_text())['fork']
FORM = 'https://tests.stockfishchess.org/tests/run'
PUBLIC = 'public_testing'
STAGES = ('stc', 'ltc')


def public_ref(repo, branch):
    ref = 'refs/heads/' + branch
    data = subprocess.check_output(['git', 'ls-remote', '--exit-code', repo + '.git', ref],
                                   text=True, timeout=30,
                                   env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    sha, name = data.strip().split()
    require(name == ref and re.fullmatch('[0-9a-f]{40}', sha), 'unexpected public ref result')
    return sha


def verify_refs(packet):
    require(public_ref(OFFICIAL, 'master') == packet['base_sha'],
            'official master advanced: rebase and repeat local evidence before any further public run')
    require(public_ref(FORK, packet['branch']) == packet['candidate_sha'],
            'published fork branch differs from the tested candidate')


def make_packet(record, evidence, stage):
    short = stage == 'stc'
    return {**evidence, 'id': record['id'], 'stage': stage, 'repo': FORK,
            'branch': ledger.branch(record), 'info': record['hypothesis'], 'form': FORM,
            'tc': '10+0.1' if short else '60+0.6', 'threads': 1,
            'base_options': 'Hash=16' if short else 'Hash=64',
            'new_options': 'Hash=16' if short else 'Hash=64',
            'book': 'UHO_Lichess_4852_v1.epd', 'book_depth': 8, 'adjudication': True,
            'elo_model': 'normalized', 'alpha': 0.05, 'beta': 0.05,
            'elo0': 0 if short else 0.5, 'elo1': 2 if short else 2.5,
            'server_sprt_game_limit': 800000}


def inspect_duplicates(packet, pages):
    own_active, duplicates, related = [], [], []
    active = fetch_json('/api/active_runs')
    scopes = [('active', active)]
    for page in range(1, pages + 1):
        data = fetch_json(f'/api/finished_runs?page={page}')
        scopes.append(('finished', data))
        if len(data) < 50:
            break
    for scope, runs in scopes:
        require(isinstance(runs, dict), 'unexpected public run listing')
        for run_id, run in runs.items():
            args = run['args']
            if scope == 'active' and (args['username'] == packet['username'] or
                                     args['tests_repo'].rstrip('/').casefold() == FORK.casefold()):
                own_active.append(run_id)
            same_tc = args['tc'] == packet['tc'] and args.get('new_tc', args['tc']) == packet['tc']
            if (args['resolved_base'], args['resolved_new']) == (packet['base_sha'], packet['candidate_sha']) and same_tc:
                duplicates.append(run_id)
            elif same_tc and (args.get('new_tag') == packet['branch'] or
                              args.get('info', '').strip().casefold() == packet['info'].strip().casefold()):
                related.append(run_id)
    return {'finished_pages_scanned': len(scopes) - 1, 'active_account_or_fork': own_active,
            'matching_pair_and_tc': duplicates, 'similar_description_or_branch': related}


def reconcile_reservation(state, pages):
    packet = state['packet']
    timestamp = datetime.fromisoformat(state['authorizations'][-1]['time_utc'])
    # Include a day before the claim to tolerate clock skew.
    since = int(timestamp.timestamp()) - 86400
    active = fetch_json('/api/active_runs')
    require(all(run['args']['username'] != packet['username'] and
                run['args']['tests_repo'].rstrip('/').casefold() != FORK.casefold()
                for run in active.values()), 'the account or fork has active runs')
    for page in range(1, pages + 1):
        query = urlencode({'page': page, 'username': packet['username'], 'timestamp': since})
        finished = fetch_json('/api/finished_runs?' + query)
        for run in finished.values():
            args = run['args']
            require(args['username'] == packet['username'], 'filtered account history returned another user')
            require((args['resolved_base'], args['resolved_new'], args['tc']) !=
                    (packet['base_sha'], packet['candidate_sha'], packet['tc']),
                    'possible submitted run found; reconcile and attach it')
        if len(finished) < 50:
            return
    raise ValueError('history scan incomplete; retain reservation until account history is reconciled')


def patch_id(base, head):
    diff = subprocess.check_output(['git', '-C', str(LAB.parent), 'diff', '--no-ext-diff', '--binary', base, head])
    return subprocess.check_output(['git', 'patch-id', '--stable'], input=diff).decode().split()[0]


def gate_public_slot(record, packet, args, *, already_reserved=False):
    require(ledger.CONFIG.get('public_fishtest_username') == packet['username'],
            'owner-approved public Fishtest account must match this packet')
    require(ledger.CONFIG.get('public_fishtest_max_games_approved', {}).get(packet['stage'], 0)
            >= packet['server_sprt_game_limit'], 'owner must approve the full server-enforced stage game ceiling')
    hours = ledger.CONFIG['public_fishtest_budget_hours']
    require(hours > 0 and 0 < args.hours <= hours, 'owner-approved public compute allocation is insufficient')
    verify_refs(packet)
    reserved_hours = 0
    for other in ledger.records():
        state = other.get(PUBLIC, {})
        reserved_hours += sum(a['hours'] for a in state.get('authorizations', []) if not a.get('released'))
        own_reservation = already_reserved and other['id'] == record['id'] and state.get('status') == 'reserved'
        require(own_reservation or state.get('status') not in ('reserved', 'active'),
                f"public slot already occupied by {other['id']}")
        if other['id'] != record['id'] and state.get('patch_id') == record[PUBLIC]['patch_id']:
            require(packet['stage'] not in state.get('runs', {}), 'equivalent patch already tested in ledger')
    require(reserved_hours + (0 if already_reserved else args.hours) <= hours, 'cumulative public allocations exceed approved budget')
    if packet['stage'] == 'ltc':
        stc = inspect_run(record[PUBLIC]['runs']['stc']['run_id'], make_packet(record, record[PUBLIC]['local'], 'stc'))
        require(stc['status'] == 'passed', 'official STC no longer passes')
    duplicate = inspect_duplicates(packet, args.pages)
    require(not duplicate['active_account_or_fork'] and not duplicate['matching_pair_and_tc'],
            f'public slot or equivalent test found: {duplicate}')
    require(not duplicate['similar_description_or_branch'] or args.related_review,
            f'review similar public runs and supply --related-review: {duplicate}')
    return duplicate


def upstream_body(record, state):
    lines = [record['hypothesis'], '', state['explanation'], '',
             f"Tested base `{state['packet']['base_sha']}` and candidate `{state['packet']['candidate_sha']}`.", '']
    for stage in STAGES:
        run = state['runs'][stage]
        wdl = run['wdl']
        lines += [f"{stage.upper()} passed at {run['settings']['tc']}. "
                  f"{run['games']} games, {wdl['wins']} wins, {wdl['draws']} draws, {wdl['losses']} losses. "
                  f"SPRT LLR {run['sprt']['llr']:.3f} ({run['sprt']['lower_bound']:.3f}, {run['sprt']['upper_bound']:.3f}). "
                  f"[Official result]({run['url']})", '']
    lines.append(f"Bench: {state['packet']['candidate_signature']}")
    return '\n'.join(lines) + '\n'


def advance(record, state):
    stage = state['packet']['stage']
    outcome = state['runs'][stage]
    if outcome['status'] != 'passed':
        state['status'] = outcome['status']
        return
    if record['status'] != 'screened' or not same_pins(record, state['packet']):
        state['status'] = 'archived_locally'
        return
    if stage == 'stc':
        state['packet'] = make_packet(record, state['local'], 'ltc')
        state['status'] = 'prepared'
    else:
        earlier = inspect_run(state['runs']['stc']['run_id'], make_packet(record, state['local'], 'stc'))
        state['runs']['stc'] = earlier
        if earlier['status'] != 'passed':
            state['status'] = 'stc_no_longer_passed'
            return
        verify_review(record)
        state['upstream_pr_body'] = upstream_body(record, state)
        state['status'] = 'pr_prepared'


def same_pins(record, packet):
    return (record.get('baseline_commit'), record.get('candidate_commit')) == (packet['base_sha'], packet['candidate_sha'])


def update(record, args):
    state = record.get(PUBLIC)
    if args.action == 'prepare':
        require(state is None, 'an official workflow already exists for this record')
        evidence = local_evidence(record, args.baseline, args.candidate, args.match, args.book, args.rationale)
        for sha in (evidence['base_sha'], evidence['candidate_sha']):
            require(re.fullmatch('[0-9a-f]{40}', sha), 'full SHA required')
        ledger.git('merge-base', '--is-ancestor', evidence['base_sha'], evidence['candidate_sha'])
        paths = ledger.git('diff', '--name-only', evidence['base_sha'], evidence['candidate_sha']).splitlines()
        require(paths and all(p.startswith('src/') and p != 'src/Makefile' for p in paths), 'expected a focused engine patch')
        account = ledger.CONFIG.get('public_fishtest_username')
        require(account and (args.username is None or account == args.username),
                'prepare requires the configured owner-approved public Fishtest username')
        evidence['username'] = account
        packet = make_packet(record, evidence, 'stc')
        verify_refs(packet)
        record[PUBLIC] = {'status': 'prepared', 'local': evidence, 'packet': packet, 'runs': {},
                          'patch_id': patch_id(evidence['base_sha'], evidence['candidate_sha']),
                          'explanation': args.explanation}
    elif args.action == 'reserve':
        require(state and state['status'] == 'prepared', 'stage has no reservable packet')
        require(same_pins(record, state['packet']), 'experiment pins changed since preparation')
        verify_review(record)
        checks = gate_public_slot(record, state['packet'], args)
        state['status'] = 'reserved'
        state.setdefault('authorizations', []).append({
            'time_utc': ledger.now(), 'stage': state['packet']['stage'], 'hours': args.hours,
            'authorization_reference': args.authorization, 'historical_equivalence_review': args.duplicate_review,
            'related_review': args.related_review, 'public_checks': checks,
            'approved_server_cap_and_current_rules': True})
    elif args.action == 'release':
        require(state and state['status'] == 'reserved', 'only an unsubmitted reservation can be released')
        require(args.never_submitted, 'an uncertain submission must retain its reservation')
        reconcile_reservation(state, args.pages)
        state['status'] = 'prepared'
        state['authorizations'][-1]['released'] = True
        state.setdefault('releases', []).append({'time_utc': ledger.now(), 'reconciliation': args.reason})
    else:
        require(state is not None, 'no public run workflow')
        packet = state['packet']
        stage = packet['stage']
        if args.action == 'attach':
            require(state['status'] == 'reserved', 'reserve and reconcile the manual form first')
            identifier = args.run
        else:
            require(state['status'] == 'active', 'no active public run to refresh')
            identifier = state['runs'][stage]['run_id']
        result = inspect_run(identifier, packet)
        state['runs'][stage] = result
        record['evidence']['fishtest'] = {key: run['url'] for key, run in state['runs'].items()}
        advance(record, state)
    return record[PUBLIC]


def stage_view(record, state):
    status, packet = state['status'], state['packet']
    if record['status'] != 'screened' or not same_pins(record, packet):
        next_action = 'reconcile archived record or changed pins'
    elif status == 'reserved':
        next_action = 'attach' if state['authorizations'][-1].get('handoff_issued_at') else 'submit'
    else:
        next_action = {'prepared': 'reserve', 'active': 'refresh', 'pr_prepared': 'upstream-pr'}.get(status)
    return {'experiment': record['id'], 'stage': packet['stage'], 'local_status': status,
            'base_sha': packet['base_sha'], 'candidate_sha': packet['candidate_sha'],
            'next_action': next_action, 'runs': {stage: {'url': run['url'], 'status': run['status']}
                                                for stage, run in state['runs'].items()}}


def submit_handoff(record):
    config = ledger.CONFIG.get('fishtest', {})
    require(config.get('submission_mode') == 'human_official_form' and
            config.get('automatic_public_submission') is False,
            'review the Fishtest configuration: only human_official_form with automatic_public_submission=false is supported')
    state = record.get(PUBLIC)
    require(state and state['status'] == 'reserved', 'an approved public reservation is required')
    packet = state['packet']
    verify_review(record)
    require(same_pins(record, packet), 'experiment pins changed since public reservation')
    authorization = state['authorizations'][-1]
    require(authorization.get('stage') == packet['stage'] and not authorization.get('released')
            and authorization.get('approved_server_cap_and_current_rules'), 'invalid stage authorization')
    require(not authorization.get('handoff_issued_at'),
            'human handoff already issued; attach the run, or reconcile a verified unsubmitted reservation')
    args = argparse.Namespace(hours=authorization['hours'], pages=authorization['public_checks']['finished_pages_scanned'],
                              related_review=authorization['related_review'])
    checks = gate_public_slot(record, packet, args, already_reserved=True)
    require(set(checks['similar_description_or_branch']) <= set(authorization['public_checks']['similar_description_or_branch']),
            'new similar runs require human reconciliation')
    reconcile_reservation(state, 10)
    authorization['handoff_issued_at'] = ledger.now()
    authorization['handoff_id'] = str(uuid4())
    return {'outcome': 'human_submission_required', 'submitted': False, 'run_id': None,
            'handoff_id': authorization['handoff_id'], 'form': FORM,
            'authorized_account': packet['username'], 'packet': packet,
            'form_fields': {'tests-repo': packet['repo'], 'base-branch': packet['base_sha'],
                            'test-branch': packet['candidate_sha'], 'base-signature': packet['base_signature'],
                            'test-signature': packet['candidate_signature'], 'run-info': packet['info']},
            'next_action': 'Sign in to the official form, verify settings and current refs, submit once, then attach its run ID.'}


def publish_record(record, event):
    def fork_git(*args):
        return subprocess.run(['git', *args], cwd=ledger.ROOT, text=True, capture_output=True, check=True,
                              timeout=60, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}).stdout.strip()

    return publish_append(fork_git, FORK, ledger.CONFIG['ledger_branch'], lambda: ledger.save(record, event))


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='action', required=True)
    for name in ('active', 'completed', 'status', 'results'):
        sub = commands.add_parser(name, help='read public official Fishtest data')
        if name in ('active', 'completed'):
            sub.add_argument('--limit', type=page_number, help='return the first N runs from this response')
        if name == 'completed':
            sub.add_argument('--page', type=page_number, default=1)
        if name in ('status', 'results'):
            sub.add_argument('run', help='public run ID or official HTTPS run URL')
    for name in ('prepare', 'reserve', 'submit', 'release', 'attach', 'refresh', 'monitor', 'show', 'stage', 'upstream-pr'):
        sub = commands.add_parser(name)
        sub.add_argument('id', type=ledger.slug)
        if name == 'prepare':
            for field in ('baseline', 'candidate', 'match', 'book'):
                sub.add_argument('--' + field, required=True, type=Path)
            sub.add_argument('--username', type=ledger.nonempty, help='defaults to the configured public account')
            for field in ('rationale', 'explanation'):
                sub.add_argument('--' + field, required=True, type=ledger.nonempty)
        if name in ('reserve', 'release'):
            sub.add_argument('--pages', type=int, choices=range(1, 11), default=3)
        if name == 'reserve':
            sub.add_argument('--hours', type=float, required=True)
            for field in ('authorization', 'duplicate-review'):
                sub.add_argument('--' + field, required=True, type=ledger.nonempty)
            sub.add_argument('--related-review', type=ledger.nonempty)
            sub.add_argument('--approved-server-cap-and-rules', action='store_true', required=True)
        if name == 'release':
            sub.add_argument('--never-submitted', action='store_true', required=True)
            sub.add_argument('--reason', required=True, type=ledger.nonempty)
        if name == 'attach':
            sub.add_argument('run')
        if name == 'monitor':
            sub.add_argument('--polls', type=int, choices=range(1, 25), default=6)
            sub.add_argument('--interval', type=int, default=300)
    return cli


def main():
    args = parser().parse_args()
    if args.action in ('active', 'completed'):
        report = listing(args.action == 'active', getattr(args, 'page', 1))
        if args.limit:
            report['runs'] = report['runs'][:args.limit]
        report['returned'] = len(report['runs'])
        print(json.dumps(report, indent=2))
        return
    if args.action in ('status', 'results'):
        print(json.dumps(read_run(args.run, args.action == 'results'), indent=2))
        return
    if args.action == 'submit':
        with ledger.ledger_lock():
            record = ledger.load_record(args.id)
            handoff = submit_handoff(record)
            handoff['published_ledger_sha'] = publish_record(record, 'Fishtest human form handoff issued')
            print(json.dumps(handoff, indent=2))
        return 3
    if args.action in ('show', 'stage', 'upstream-pr'):
        record = ledger.load_record(args.id)
        state = record.get(PUBLIC)
        require(state, 'no prepared packet')
        if args.action == 'upstream-pr':
            require(state['status'] == 'pr_prepared', 'official STC and LTC have not both passed')
            require(same_pins(record, state['packet']), 'experiment pins changed after drafting')
            verify_review(record)
            print(state['upstream_pr_body'], end='')
        else:
            print(json.dumps(stage_view(record, state) if args.action == 'stage' else state, indent=2))
        return
    polls = 1
    if args.action == 'monitor':
        require(60 <= args.interval <= 3600, 'interval must be 60..3600 seconds')
        args.action, polls = 'refresh', args.polls
    for index in range(polls):
        if index:
            time.sleep(args.interval)
        with ledger.ledger_lock():
            record = ledger.load_record(args.id)
            before = json.dumps(record, sort_keys=True)
            state = update(record, args)
            if json.dumps(record, sort_keys=True) != before:
                publish_record(record, f"Fishtest {args.action} ({state['status']})")
            output = stage_view(record, state) if args.action == 'reserve' else state
            print(json.dumps(output, indent=2), flush=True)
            if state['status'] != 'active':
                break


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        sys.exit(f'Fishtest workflow blocked: {exc}')
