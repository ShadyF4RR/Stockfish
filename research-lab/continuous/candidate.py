#!/usr/bin/env python3
"""Reconstruct one pinned, single-file C++ candidate without checking it out.

Run on Linux with Python 3.9+ and Git 2.31+. Both commands consume an object
with an edits list of {path, old, new} hunks. Every old value identifies a
unique span in the pinned baseline; all spans must be disjoint and belong to
the independently approved --target. The repo must contain the baseline commit.

apply creates a local experiment ref. publish recomputes the commit in a
trusted writer, requires the SHA from the disposable tester (--expected), and
creates only that experiment ref on the configured fork. Pass the writer's
contents-write token through CANDIDATE_PUSH_TOKEN in the publish process.
Exit 0 prints the candidate SHA, exit 1 indicates failure, and exit 2
indicates a CLI usage error. No engine code is executed by this helper.
"""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

FORK = "https://github.com/ShadyF4RR/Stockfish.git"
SHA = re.compile(r"[0-9a-f]{40}")
ZERO = "0" * 40
SOURCE_PATH = re.compile(r"src/(?:[a-zA-Z0-9_]+/)*[a-zA-Z0-9_]+\.(?:cpp|h)")
MAX_PACKET = 64 * 1024
MAX_BLOB = 512 * 1024
MAX_HUNKS = 8
MAX_HUNK_BYTES = 4096
ALLOWED_SOURCE_DIRS = {"src", "src/nnue", "src/nnue/features", "src/nnue/layers", "src/syzygy"}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def unique_fields(items):
    fields = dict(items)
    require(len(fields) == len(items), "duplicate JSON field")
    return fields


def read_packet(path):
    # A downloaded artifact can contain a symlink or FIFO; open without following or blocking.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        mode = os.fstat(fd)
        require(stat.S_ISREG(mode.st_mode) and mode.st_size <= MAX_PACKET, "invalid edit packet file")
        raw = stream.read(MAX_PACKET + 1)
    require(len(raw) <= MAX_PACKET, "oversize edit packet")
    packet = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_fields)
    require(type(packet) is dict and set(packet) == {"edits"}, "expected an object with edits only")
    edits = packet["edits"]
    require(type(edits) is list and 1 <= len(edits) <= MAX_HUNKS, "invalid number of edit hunks")
    return edits


def replace_hunks(source, target, edits):
    spans = []
    for edit in edits:
        require(type(edit) is dict and set(edit) == {"path", "old", "new"}, "invalid edit fields")
        require(edit["path"] == target, "edit differs from approved source path")
        old, new = edit["old"], edit["new"]
        for value in (old, new):
            require(type(value) is str and len(value.encode("utf-8")) <= MAX_HUNK_BYTES,
                    "text hunk exceeds byte limit or has invalid type")
            require(all(c in "\n\t" or 32 <= ord(c) < 127 for c in value), "invalid edit character")
        require(old.strip() and new != old, "empty preimage or unchanged hunk")
        start = source.find(old)
        require(start >= 0 and source.find(old, start + 1) == -1, "preimage must match exactly once")
        spans.append((start, start + len(old), new))
    pieces = []
    cursor = 0
    for start, end, replacement in sorted(spans):
        require(start >= cursor, "overlapping replacements in original source")
        pieces.extend([source[cursor:start], replacement])
        cursor = end
    pieces.append(source[cursor:])
    result = "".join(pieces)
    require(result != source and result.strip() and len(result.encode("utf-8")) <= MAX_BLOB,
            "empty, oversized or unchanged candidate source")
    # Preprocessor directives can alter dependencies or build inputs. Restrict this
    # path to ordinary C++ implementation changes; the reviewer still inspects semantics.
    def directives(text):
        lines = []
        continued = False
        for line in text.splitlines():
            if continued or line.lstrip().startswith(("#", "%:")):
                lines.append(line)
                continued = line.rstrip().endswith("\\")
        return lines
    require(directives(result) == directives(source), "candidate changed preprocessor directives")
    return result


def git(repo, *args, data=None, env=None, check=True):
    # Keep inherited credentials, shell tracing, replace refs and Git namespace/
    # path overrides out of all child processes. The authenticated push uses a fresh bare repo.
    process_env = {
        "PATH": os.defpath, "LC_ALL": "C", "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1", **(env or {}),
    }
    proc = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                           "-c", "commit.gpgSign=false", *args], cwd=repo, input=data,
                          capture_output=True, timeout=120, env=process_env)
    if check and proc.returncode:
        raise ValueError(f"Git {args[0]} failed with exit {proc.returncode}")
    return proc


def oid(repo, *args, **kwargs):
    value = git(repo, *args, **kwargs).stdout.decode("ascii").strip()
    require(SHA.fullmatch(value), "expected full Git object ID")
    return value


def branch_ref(identifier, rehearsal):
    if rehearsal is None:
        require(re.fullmatch(r"EXP-CONT-[0-9]{6}", identifier), "invalid production experiment ID")
        return "refs/heads/experiment/" + identifier
    require(re.fullmatch(r"[1-9][0-9]{0,19}", rehearsal), "invalid rehearsal run ID")
    require(re.fullmatch("R" + rehearsal + r"-EXP-CONT-[0-9]{6}", identifier),
            "experiment ID does not match rehearsal")
    return f"refs/heads/research/rehearsal/{rehearsal}/experiment/{identifier}"


def reconstruct(repo, base, identifier, target, edits):
    require(SHA.fullmatch(base) and git(repo, "cat-file", "-t", base).stdout == b"commit\n",
            "base must identify a commit by full SHA")
    require(SOURCE_PATH.fullmatch(target) and str(Path(target).parent) in ALLOWED_SOURCE_DIRS,
            "target must be an approved engine C++ source or header")
    entry = git(repo, "ls-tree", "-z", "-l", base, "--", target).stdout
    require(entry.endswith(b"\0") and entry.count(b"\0") == 1, "source missing at pinned baseline")
    metadata, path = entry[:-1].split(b"\t", 1)
    mode, kind, blob, size = metadata.split()
    require(path.decode() == target and (mode, kind) == (b"100644", b"blob"),
            "expected a tracked, regular, non-executable source blob")
    require(int(size) <= MAX_BLOB, "oversized baseline blob")
    source = git(repo, "cat-file", "blob", blob.decode()).stdout.decode("utf-8")
    require("\0" not in source, "binary data in engine source")
    updated = replace_hunks(source, target, edits)
    updated_blob = oid(repo, "hash-object", "-w", "--stdin", data=updated.encode())
    with tempfile.TemporaryDirectory() as temp:
        index = {"GIT_INDEX_FILE": str(Path(temp) / "index")}
        git(repo, "read-tree", base, env=index)
        git(repo, "update-index", "--cacheinfo", "100644", updated_blob, target, env=index)
        tree = oid(repo, "write-tree", env=index)
    timestamp = git(repo, "log", "-1", "--format=%ct", base).stdout.decode("ascii").strip()
    require(timestamp.isdigit(), "invalid baseline timestamp")
    identity = {}
    for prefix in ("AUTHOR", "COMMITTER"):
        identity.update({f"GIT_{prefix}_NAME": "Stockfish Research Lab",
                         f"GIT_{prefix}_EMAIL": "research-lab@users.noreply.github.com",
                         f"GIT_{prefix}_DATE": f"@{timestamp} +0000"})
    return oid(repo, "commit-tree", tree, "-p", base, "-m", f"Research candidate {identifier}", env=identity)


def create_local_ref(repo, ref, commit):
    result = git(repo, "update-ref", ref, commit, ZERO, check=False)
    if result.returncode:
        require(oid(repo, "rev-parse", "--verify", ref) == commit, "candidate branch exists at another commit")


def publish(repo, base, ref, commit, token):
    require(token, "CANDIDATE_PUSH_TOKEN is required in the isolated writer")
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        git(directory, "init", "--quiet", "--bare", "--template=")
        object_dir = git(repo, "rev-parse", "--path-format=absolute", "--git-path", "objects").stdout.decode().rstrip("\n")
        require("\n" not in object_dir and "\r" not in object_dir, "invalid Git object directory")
        (directory / "objects/info/alternates").write_text(object_dir + "\n")
        (directory / "shallow").write_text(base + "\n")
        # Git passes the actual advertised remote OID to pre-push. Only create a
        # new ref; a racing or preexisting branch at another commit must survive.
        hooks = directory / "checked-hooks"
        hooks.mkdir()
        hook = hooks / "pre-push"
        hook.write_text(
            '#!/bin/sh\nwhile read -r local_ref local_oid remote_ref remote_oid; do\n'
            '  [ "$remote_ref" = "$CANDIDATE_REF" ] && [ "$local_oid" = "$CANDIDATE_SHA" ]'
            ' && [ "$remote_oid" = "0000000000000000000000000000000000000000" ] || exit 1\ndone\n'
        )
        hook.chmod(0o700)
        auth = base64.b64encode(("x-access-token:" + token).encode()).decode("ascii")
        env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraHeader",
               "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + auth,
               "CANDIDATE_REF": ref, "CANDIDATE_SHA": commit}
        opts = ("-c", "http.followRedirects=false", "-c", f"core.hooksPath={hooks}")
        result = git(directory, *opts, "push", "--porcelain", "--no-follow-tags",
                     FORK, commit + ":" + ref, env=env, check=False)
        # A retry can observe an existing identical ref or an ambiguous push response.
        actual = git(directory, *opts, "ls-remote", "--heads", FORK, ref, env=env).stdout
        require(actual == f"{commit}\t{ref}\n".encode(), f"publication unconfirmed (push exit {result.returncode})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("apply", "publish"):
        sub = commands.add_parser(action)
        sub.add_argument("--repo", type=Path, required=True)
        sub.add_argument("--base", required=True)
        sub.add_argument("--id", required=True)
        sub.add_argument("--edits", type=Path, required=True)
        sub.add_argument("--target", required=True, help="source path from the coordinator-approved plan")
        sub.add_argument("--rehearsal", help="numeric GitHub run ID, bound to experiment ID and branch")
        if action == "publish":
            sub.add_argument("--expected", required=True, help="candidate SHA observed in the disposable job")
    args = parser.parse_args()
    ref = branch_ref(args.id, args.rehearsal)
    commit = reconstruct(args.repo, args.base, args.id, args.target, read_packet(args.edits))
    if args.action == "publish":
        require(SHA.fullmatch(args.expected) and args.expected == commit, "candidate differs from expected SHA")
        publish(args.repo, args.base, ref, commit, os.environ.get("CANDIDATE_PUSH_TOKEN"))
    else:
        create_local_ref(args.repo, ref, commit)
    print(commit)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, RecursionError) as exc:
        sys.exit(f"candidate: {exc}")
