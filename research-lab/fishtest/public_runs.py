"""Browse official public runs without requiring a lab packet or any credentials."""
import argparse
from urllib.parse import urlencode

from official_results import HOST, canonical_id, check, fetch_json, numeric


def page_number(text):
    try:
        page = int(text)
        if 1 <= page <= 9999:
            return page
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('page must be between 1 and 9999')


def counts(results):
    wdl = {key: results[key] for key in ('wins', 'draws', 'losses')}
    check(all(type(n) is int and n >= 0 for n in wdl.values()), 'invalid public WDL')
    penta = results.get('pentanomial')
    if penta is not None:
        check(isinstance(penta, list) and len(penta) == 5 and
              all(type(n) is int and n >= 0 for n in penta) and 2 * sum(penta) == sum(wdl.values()),
              'invalid public game-pair counts')
    return wdl, penta


def public_summary(run_id, run):
    check(canonical_id(run_id) == run['_id'], 'public run ID mismatch')
    args = run['args']
    finished = run['finished']
    check(type(finished) is bool, 'invalid public finished flag')
    for key in ('approved', 'failed', 'deleted'):
        check(key not in run or type(run[key]) is bool, 'invalid public run flag')
    wdl, paired = counts(run['results'])
    games = sum(wdl.values())
    kind = 'sprt' if 'sprt' in args else 'spsa' if 'spsa' in args else 'fixed_games'
    check(not ('sprt' in args and 'spsa' in args), 'ambiguous public test design')
    workers = run.get('workers')
    check(workers is None or type(workers) is int and workers >= 0, 'invalid worker count')
    if run.get('deleted'):
        status = 'deleted'
    elif run.get('failed'):
        status = 'failed'
    elif finished:
        status = 'finished'
    elif run.get('approved') is False:
        status = 'pending_approval'
    elif workers == 0:
        status = 'waiting_for_workers'
    elif workers is not None:
        status = 'running'
    else:
        status = 'active'
    report = {'run_id': run_id, 'url': f'{HOST}/tests/view/{run_id}', 'test_type': kind,
              'status': status, 'approved': run.get('approved'), 'finished': finished,
              'workers': workers, 'games': games, 'planned_games': args.get('num_games'),
              'wdl': wdl, 'pentanomial': paired, 'last_updated': run.get('last_updated'),
              'base_sha': args.get('resolved_base'), 'candidate_sha': args.get('resolved_new'),
              'candidate_ref': args.get('new_tag'), 'info': args.get('info'), 'tc': args.get('tc'),
              'new_tc': args.get('new_tc', args.get('tc')), 'threads': args.get('threads'),
              'submitter': args.get('username'), 'repository': args.get('tests_repo')}
    if kind == 'sprt':
        sprt = args['sprt']
        state = sprt.get('state', '')
        check(state in ('', 'accepted', 'rejected'), 'unrecognized public SPRT state')
        report['sprt'] = {key: sprt.get(key) for key in
                          ('llr', 'lower_bound', 'upper_bound', 'elo0', 'elo1', 'alpha', 'beta')}
        check(all(v is None or numeric(v) for v in report['sprt'].values()), 'invalid public SPRT field')
        report['sprt'].update(state=state or 'undecided', elo_model=sprt.get('elo_model', 'BayesElo'))
    elif kind == 'spsa':
        tuning = args['spsa']
        report['spsa'] = {'iteration': tuning.get('iter'), 'planned_iterations': tuning.get('num_iter'),
                          'parameter_count': len(tuning.get('params', []))}
    if status in ('failed', 'deleted'):
        decision = status
    elif not finished:
        decision = 'pending'
    elif kind == 'sprt':
        decision = state or 'inconclusive'
    elif not isinstance(report['planned_games'], int) or games < report['planned_games']:
        decision = 'stopped_or_incomplete'
    elif kind == 'spsa':
        decision = 'tuning_completed'
    else:
        decision = 'fixed_sample_completed'
    report['decision'] = decision
    return report


def listing(active, page=1):
    path = '/api/active_runs' if active else f'/api/finished_runs?page={page}'
    runs = []
    for identifier, run in fetch_json(path).items():
        summary = public_summary(identifier, run)
        check(summary['finished'] is not active, 'official listing contains a run in the wrong state')
        runs.append({key: summary[key] for key in
                     ('run_id', 'url', 'test_type', 'status', 'decision', 'candidate_ref',
                      'submitter', 'games', 'tc', 'last_updated')})
    return {'source': HOST + path, 'page': None if active else page, 'count': len(runs), 'runs': runs}


def read_run(ref, include_elo):
    run_id = canonical_id(ref)
    path = '/api/get_run/' + run_id
    summary = public_summary(run_id, fetch_json(path))
    summary['source'] = HOST + path
    if not include_elo:
        return summary
    kind = summary['test_type']
    if kind == 'spsa' or not summary['games']:
        summary['elo'] = {'available': False, 'reason': 'SPSA tunes parameters' if kind == 'spsa' else 'no games played'}
        return summary
    if kind == 'sprt':
        path = '/api/get_elo/' + run_id
        snapshot = fetch_json(path)
        # This endpoint returns a new, coherent snapshot including its Elo statistics.
        summary = public_summary(run_id, snapshot)
        check(summary['test_type'] == 'sprt', 'public SPRT design changed')
        summary['source'] = HOST + path
        estimate = snapshot['elo']
    else:
        penta, wdl = summary['pentanomial'], summary['wdl']
        bins = zip(('LL', 'LD', 'DDWL', 'WD', 'WW'), penta) if penta is not None else (
            (letter, wdl[key]) for letter, key in [('W', 'wins'), ('D', 'draws'), ('L', 'losses')])
        path = '/api/calc_elo?' + urlencode(list(bins))
        estimate = fetch_json(path)
    ci, value = estimate['ci'], estimate['elo']
    check(numeric(value) and isinstance(ci, list) and len(ci) == 2 and
          all(numeric(x) for x in ci) and ci[0] <= value <= ci[1], 'invalid official Elo estimate')
    summary['elo'] = {'available': True, 'scale': 'logistic', 'estimate': value,
                      'confidence': 0.95, 'interval': ci, 'source': HOST + path}
    if 'LOS' in estimate:
        check(numeric(estimate['LOS']) and 0 <= estimate['LOS'] <= 1, 'invalid official LOS')
        summary['elo']['los'] = estimate['LOS']
    return summary
