#!/usr/bin/env python3
"""Generate explicit offline rehearsal fixtures. Model invocation is disabled."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROLES = ('implement', 'review', 'research')
PATH = re.compile(r'src/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.(?:cpp|h)')
SHA = re.compile(r'[0-9a-f]{40}')
SECRET = re.compile(r'-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----|\b(?:github_pat_|gh[pousr]_|sk-)[A-Za-z0-9_-]{16,}')
COMMENT = '// Research lab rehearsal: exercise the validation pipeline.'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def valid_text(value, limit):
    return isinstance(value, str) and 0 < len(value.encode()) <= limit and value.strip() and '\0' not in value


def git(source, *args):
    return subprocess.run(['git', '--no-replace-objects', '-C', str(source), *args], capture_output=True,
                          check=True, timeout=15).stdout


def selected_source(source, sha, path):
    require(isinstance(sha, str) and SHA.fullmatch(sha), 'source_commit must be a full commit SHA')
    require(isinstance(path, str) and PATH.fullmatch(path), 'only a Stockfish src C++ path is permitted')
    require(git(source, 'cat-file', '-t', sha).strip() == b'commit', 'source pin is not a commit')
    entry = git(source, 'ls-tree', sha, '--', path).decode().strip().split()
    require(len(entry) == 4 and entry[0] == '100644' and entry[1] == 'blob' and entry[3] == path,
            'source must be a regular tracked file at the pinned commit')
    require(int(git(source, 'cat-file', '-s', entry[2])) <= 1_500_000, 'source exceeds byte limit')
    return git(source, 'cat-file', 'blob', entry[2]).decode()


def checked_edits(edits, path, source):
    require(isinstance(edits, list) and 1 <= len(edits) <= 3, 'one to three small edits required')
    spans = []
    for edit in edits:
        require(isinstance(edit, dict) and set(edit) == {'path', 'old', 'new'} and edit['path'] == path,
                'edit outside assigned file')
        old, new = edit['old'], edit['new']
        require(valid_text(old, 1800) and valid_text(new, 2400) and old != new
                and max(old.count('\n'), new.count('\n')) <= 32 and source.count(old) == 1,
                'edit preimage must be unique; replacement must be small and different')
        start = source.index(old)
        end = start + len(old)
        require(all(end <= a or start >= b for a, b in spans), 'overlapping edits')
        spans.append((start, end))
    return edits


def context(role, task, source_dir):
    identifier = task['id']
    require(isinstance(identifier, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,78}', identifier), 'invalid experiment id')
    path, sha = task.get('path', 'src/search.cpp'), task['source_commit']
    source = selected_source(source_dir, sha, path)
    hypothesis = task.get('hypothesis', '')
    require(valid_text(hypothesis, 1800) or role == 'research' and hypothesis == '', 'invalid hypothesis')
    history = task.get('history', [])
    require(isinstance(history, list), 'history must be a list')
    recent = []
    for row in history[-12:]:
        require(isinstance(row, dict), 'invalid research history')
        recent.append({key: row[key][:220] for key in ('topic', 'hypothesis', 'status', 'decision')
                       if isinstance(row.get(key), str)})
    edits = checked_edits(task.get('edits'), path, source) if role == 'review' else None
    data = {'experiment_id': identifier, 'hypothesis': hypothesis, 'path': path,
            'source_commit': sha, 'history': recent}
    if edits:
        data['edits'] = edits
    candidate = task.get('candidate_commit')
    require(candidate is None or isinstance(candidate,str) and SHA.fullmatch(candidate), 'invalid candidate commit')
    require(not SECRET.search(canonical(data).decode()), 'credential-shaped text detected in selected context')
    return data, source


def rehearsal(role, data):
    if role == 'implement':
        old = 'namespace Stockfish {'
        return {'edits':[{'path':data['path'],'old':old,'new':COMMENT + '\n' + old}]}
    if role == 'review':
        approved = data['edits'] == rehearsal('implement',data)['edits']
        return {'approved': approved, 'reason': 'Verified the exact rehearsal-only comment.' if approved
                else 'The patch differs from the allowlisted rehearsal-only comment.'}
    if data['path'] != 'src/evaluate.cpp':
        return dict(topic='rehearsal-rule50-discount', path='src/evaluate.cpp',
                    hypothesis='Rehearsal proposal: assess whether reducing the reversible-move evaluation discount warrants paired screening.')
    return dict(topic='rehearsal-root-time', path='src/timeman.cpp',
                hypothesis='Rehearsal proposal: assess whether a root move switch merits additional search time.')


def execute(role, plan, source_dir):
    data, source = context(role, plan, source_dir)
    result = rehearsal(role, data)
    if role == 'implement':
        checked_edits(result['edits'], data['path'], source)
    if role == 'research':
        selected_source(source_dir, data['source_commit'], result['path'])
    edits = result.get('edits', data.get('edits'))
    return {**result, 'provenance': {'adapter': 'deterministic_rehearsal', 'role': role,
                                     'experiment_id': data['experiment_id'], 'source_commit': data['source_commit'],
                                     'candidate_commit': plan.get('candidate_commit'), 'task_sha256': digest(data),
                                     'edits_sha256': digest(edits) if edits is not None else None,
                                     'model': None, 'response_id': None},
            'usage': {'requests': 0, 'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=ROLES)
    parser.add_argument('task', type=Path)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--rehearsal', action='store_true')
    args = parser.parse_args()
    require(args.rehearsal, 'model invocation disabled; --rehearsal produces only an offline fixture')
    require(not args.output.exists() and args.output.parent.is_dir(), 'output requires a new file in an existing directory')
    require(args.task.stat().st_size <= 128000, 'task too large')
    result = execute(args.role, json.loads(args.task.read_text()), args.source)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=args.output.parent, delete=False) as f:
            tmp = Path(f.name)
            f.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, args.output)
    finally:
        if tmp is not None:
            tmp.unlink()


if __name__=='__main__':
    try:
        main()
    except (KeyError, ValueError, OSError, TypeError, subprocess.SubprocessError, ArithmeticError) as exc:
        sys.exit('agent blocked: '+str(exc))
