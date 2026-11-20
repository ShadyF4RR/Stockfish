#!/usr/bin/env python3
"""Resolve a trusted control request against the public fork and stage two immutable engine revisions."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from validate_candidate import check_source

FORK = 'https://github.com/ShadyF4RR/Stockfish.git'
BASE_REF = 'refs/heads/research/baseline'
SHA = re.compile('[0-9a-f]{40}')
ID = re.compile('EXP-[A-Z0-9]+(?:-[A-Z0-9]+)*')


def checked(request):
    if not isinstance(request, dict) or set(request) != {'experiment', 'baseline', 'candidate'}:
        raise ValueError('expected exactly experiment, baseline and candidate')
    if not isinstance(request['experiment'], str) or len(request['experiment']) > 63 or not ID.fullmatch(request['experiment']):
        raise ValueError('invalid experiment ID')
    for key in ('baseline', 'candidate'):
        if not isinstance(request[key], str) or not SHA.fullmatch(request[key]):
            raise ValueError('expected full lowercase commit hashes')
    if request['baseline'] == request['candidate']:
        raise ValueError('baseline and candidate must differ')
    return request


def git(directory, *args):
    return subprocess.run(['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null', '-C', str(directory), *args],
                          capture_output=True, check=True, text=True, timeout=150,
                          env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}).stdout.strip()


def stage(request, output, *, fork=FORK):
    request = checked(request)
    output.mkdir(mode=0o700)
    repo = output / 'objects.git'
    git(output, 'init', '--quiet', '--bare', str(repo))
    new_ref = 'refs/heads/experiment/' + request['experiment']
    # Fetch branch tips together. A submitted SHA string must match its advertised branch.
    git(repo, 'fetch', '--quiet', '--no-tags', '--depth=128', fork, BASE_REF + ':refs/check/base',
        new_ref + ':refs/check/new')
    for local, key in [('base', 'baseline'), ('new', 'candidate')]:
        if git(repo, 'rev-parse', f'refs/check/{local}^{{commit}}') != request[key]:
            raise ValueError(f'published {key} branch does not match requested SHA')
    # Validate the parent and every changed path before checking out the candidate.
    paths = check_source(repo, request['baseline'], request['candidate'], False)
    for key in ('baseline', 'candidate'):
        git(repo, 'worktree', 'add', '--quiet', '--detach', str(output / key), request[key])
    return {**request, 'source_paths': paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--marker', type=Path)
    for field in ('experiment', 'candidate', 'baseline'):
        parser.add_argument('--' + field)
    args = parser.parse_args()
    values = {field: getattr(args, field) for field in ('experiment','baseline','candidate')}
    if args.marker:
        if any(values.values()) or args.marker.is_symlink() or args.marker.stat().st_size > 4096:
            raise ValueError('expected only a small, regular control request')
        values = json.loads(args.marker.read_text())
    print(json.dumps(stage(values, args.output.resolve()), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f'candidate preparation blocked: {error}')
