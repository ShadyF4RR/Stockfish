"""Validate Stage A–C artifacts produced by the trusted local lab scripts."""
import json
from pathlib import Path

from local_match import COMPARABLE, read_build, results, sha256


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def inspect_engine(directory, commit):
    _, build = read_build(directory)
    validation, bench = (read(directory / name) for name in ('validation.json', 'benchmark.json'))
    binary = build['binary_sha256']
    for record in (build, validation, bench):
        require(record['commit'] == commit and record['binary_sha256'] == binary,
                'local evidence must identify the tested commit and executable')
    require(all(validation[key] == 'passed' for key in ('uci', 'perft', 'reprosearch')),
            'UCI, upstream perft and reproducibility must pass')
    signature = bench['signature']
    require(type(signature) is int and signature > 0 and len(bench['runs']) >= 2 and
            all(run['nodes'] == signature and run['nps'] > 0 for run in bench['runs']),
            'two reproducible canonical bench runs required')
    require(bench['command'] == ['stockfish', 'bench'], 'canonical benchmark required')
    return build, signature


def verify_review(record):
    require(record['status'] == 'screened', 'requires a screened experiment record')
    review = record['evidence'].get('review')
    require(isinstance(review, dict) and review.get('verdict') == 'approved'
            and review.get('reviewed_sha') == record['candidate_commit']
            and isinstance(review.get('reviewer'), str) and bool(review['reviewer'].strip())
            and review.get('unresolved_defects') == [] and any(
                item['role'] == 'reviewer' and item['candidate_commit'] == record['candidate_commit']
                and item['evidence'].get('review') == review for item in record['reports']),
            'an independent approving review for this candidate, without unresolved defects, is required')
    implementers = set()
    for item in record['reports']:
        if item['role'] == 'implementation' and item['candidate_commit'] == record['candidate_commit']:
            build = item['evidence'].get('build_configuration')
            if isinstance(build, dict) and isinstance(build.get('implementer'), str) and build['implementer'].strip():
                implementers.add(build['implementer'].strip().casefold())
    require(implementers and review['reviewer'].strip().casefold() not in implementers,
            'record the implementing identities and use a distinct reviewer')


def local_evidence(record, base_dir, new_dir, match_dir, book, rationale):
    verify_review(record)
    base, base_signature = inspect_engine(base_dir, record['baseline_commit'])
    new, new_signature = inspect_engine(new_dir, record['candidate_commit'])
    require(base['commit'] != new['commit'], 'self-matches do not qualify')
    match = read(match_dir / 'match.json')
    require(match['baseline'] == base and match['candidate'] == new,
            'screen must use these exact build manifests')
    require(match['stage'] == 'C' and match['status'] == 'screened'
            and match['decision'] in ('escalate', 'inconclusive'), 'missing viable completed screen')
    pairs = match['settings']['pairs']
    wdl = [match[key] for key in ('wins', 'draws', 'losses')]
    require(type(pairs) is int and pairs > 0 and match['games'] == pairs * 2 == sum(wdl)
            and all(type(item) is int and item >= 0 for item in wdl)
            and len(match['pentanomial']) == 5 and sum(match['pentanomial']) == pairs,
            'invalid opening-paired result')
    require(sha256(match_dir / 'games.pgn') == match['pgn_sha256'], 'PGN digest mismatch')
    require(sha256(book) == match['book_sha256'], 'local opening book digest mismatch')
    positions = {' '.join(line.split()[:4]) for line in book.read_text().splitlines() if line.strip()}
    observed = results(match_dir / 'games.pgn', pairs, positions)
    for key, value in observed.items():
        require(match.get(key) == value, f'local results disagree with PGN: {key}')
    for key in COMPARABLE:
        require(base[key] == new[key], f'unmatched local builds: {key}')
    return {'base_sha': base['commit'], 'candidate_sha': new['commit'],
            'base_signature': base_signature, 'candidate_signature': new_signature,
            'base_binary_sha256': base['binary_sha256'], 'candidate_binary_sha256': new['binary_sha256'],
            'match_sha256': sha256(match_dir / 'match.json'), 'pgn_sha256': match['pgn_sha256'],
            'games': match['games'], 'local_wdl': wdl, 'screen_decision': match['decision'],
            'escalation_rationale': rationale}
