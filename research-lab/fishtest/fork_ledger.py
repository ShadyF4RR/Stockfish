"""Publish a single coordinator transaction by ordinary fast-forward Git push."""
import re
import subprocess


def is_fork(address, expected):
    if address == expected:
        return True
    suffix = expected.removeprefix('https://github.com/').casefold()
    prefixes = ('https://github.com/', 'ssh://git@github.com/', 'git@github.com:')
    return any(address.startswith(prefix) and address[len(prefix):].removesuffix('.git').casefold() == suffix
               for prefix in prefixes)


def publish_append(git, fork, branch, append):
    """Keep uncertain or conflicting commits locally, withhold success until read-back."""
    ref = 'refs/heads/' + branch
    for args in (('remote', 'get-url', '--all', 'origin'), ('remote', 'get-url', '--push', '--all', 'origin')):
        addresses = git(*args).splitlines()
        if len(addresses) != 1 or not is_fork(addresses[0], fork):
            raise ValueError('origin must fetch and push only the configured fork')
    if git('symbolic-ref', 'HEAD') != ref:
        raise ValueError('public writes require the dedicated ledger checkout')

    def remote_tip():
        try:
            answer = git('ls-remote', '--exit-code', 'origin', ref)
        except subprocess.CalledProcessError as exc:
            raise ValueError('cannot verify fork ledger; confirm access and initialize the approved ledger ref') from exc
        pair = answer.split()
        if len(pair) != 2 or not re.fullmatch('[0-9a-f]{40}', pair[0]) or pair[1] != ref:
            raise ValueError('fork did not return an unambiguous ledger tip')
        return pair[0]

    before = git('rev-parse', 'HEAD')
    if remote_tip() != before:
        raise ValueError('local ledger differs from fork; fetch, inspect both histories and reconcile before retrying')
    append()
    after = git('rev-parse', 'HEAD')
    if git('rev-parse', after + '^') != before:
        raise ValueError('expected one local ledger commit; inspect history before publishing')
    try:
        git('push', '--porcelain', '--no-follow-tags', '--recurse-submodules=no', 'origin', after + ':' + ref)
        confirmed = remote_tip()
    except (ValueError, subprocess.SubprocessError) as exc:
        raise ValueError(f'fork publication unconfirmed for local commit {after}; retain it, inspect the remote and reconcile') from exc
    if confirmed != after:
        raise ValueError(f'fork ledger moved during confirmation of {after}; inspect the remote and reconcile')
    return after
