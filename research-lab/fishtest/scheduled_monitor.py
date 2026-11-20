#!/usr/bin/env python3
"""Refresh attached public runs from trusted code and publish only fork ledger records."""
import argparse
import base64
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace

# Works under python3 -I; modules are loaded solely from the reviewed control checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli

REF = "refs/heads/research/ledger"
DIRECTORY = "research-lab/experiments"
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,62}")
MAX_RECORD_BYTES = 2_000_000
MAX_RECORDS_BYTES = 32_000_000


class LedgerStore:
    def __init__(self, directory, remote, token):
        self.directory = directory
        self.remote = remote
        self.env = {
            "PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_AUTHOR_NAME": "Fishtest ledger monitor", "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "Fishtest ledger monitor", "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        }
        if token:
            auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
            self.env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http." + remote + ".extraheader",
                            GIT_CONFIG_VALUE_0="Authorization: Basic " + auth)
        self.git("init", "--bare", "--template=")

    def git(self, *args, data=None):
        proc = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "http.followRedirects=false", *args],
                              input=data, capture_output=True, cwd=self.directory, env=self.env, timeout=90)
        if proc.returncode:
            # An authenticated remote may return arbitrary text; do not emit its headers or body.
            raise RuntimeError(f"Git {args[0]} failed (exit {proc.returncode})")
        return proc.stdout

    def fetch(self):
        self.git("fetch", "--depth=1", "--no-tags", "--recurse-submodules=no", self.remote, REF)
        return self.git("rev-parse", "--verify", "FETCH_HEAD^{commit}").decode().strip()

    def records(self, tip):
        listing = self.git("ls-tree", "-l", "-z", tip + ":" + DIRECTORY)
        total = 0
        for entry in listing.split(b"\0"):
            if not entry:
                continue
            meta, filename = entry.split(b"\t", 1)
            if not filename.endswith(b".json"):
                continue
            identifier = filename[:-5].decode("ascii")
            mode, kind, oid, size = meta.split()
            if not IDENTIFIER.fullmatch(identifier) or mode != b"100644" or kind != b"blob":
                raise ValueError("invalid ledger record name or Git mode")
            total += int(size)
            if int(size) > MAX_RECORD_BYTES or total > MAX_RECORDS_BYTES:
                raise ValueError("ledger scan exceeds configured data bounds")
            record = json.loads(self.git("cat-file", "blob", oid.decode()))
            if record.get("id") != identifier:
                raise ValueError("ledger record ID and filename disagree")
            yield record

    def write(self, tip, records):
        self.git("read-tree", tip)
        for record in records:
            data = (json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
            if len(data) > MAX_RECORD_BYTES:
                raise ValueError("updated ledger record exceeds byte limit")
            oid = self.git("hash-object", "-w", "--stdin", data=data).decode().strip()
            self.git("update-index", "--cacheinfo", "100644", oid, f"{DIRECTORY}/{record['id']}.json")
        tree = self.git("write-tree").decode().strip()
        commit = self.git("commit-tree", tree, "-p", tip, "-m", "Update verified Fishtest observations").decode().strip()
        self.git("push", self.remote, commit + ":" + REF)
        return commit


def refresh(record, now):
    state = record.get(cli.PUBLIC, {})
    if state.get("status") != "active":
        return None
    health = state.get("monitor", {})
    if health.get("retry_after") and datetime.fromisoformat(health["retry_after"]) > now:
        return None
    candidate = copy.deepcopy(record)
    try:
        stage = state["packet"]["stage"]
        if stage not in cli.STAGES or state["packet"] != cli.make_packet(record, state["local"], stage):
            raise ValueError("ledger packet differs from registered test")
        cli.update(candidate, SimpleNamespace(action="refresh", id=record["id"]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, TimeoutError) as exc:
        # Discard any partially updated snapshot. Back off after failed service/identity checks.
        count = min(6, int(health.get("consecutive_failures", 0)) + 1)
        hours = 0.5 * 2 ** (count - 1)
        candidate = copy.deepcopy(record)
        candidate[cli.PUBLIC]["monitor"] = {
            "stale": True, "consecutive_failures": count, "last_failure": now.isoformat(),
            "error_type": type(exc).__name__, "retry_after": (now + timedelta(hours=hours)).isoformat(),
        }
    else:
        candidate[cli.PUBLIC].pop("monitor", None)
        # The official snapshot hash includes all service fields and excludes our poll time.
        old_run = state["runs"][stage]
        new_run = candidate[cli.PUBLIC]["runs"][stage]
        if (not health and candidate[cli.PUBLIC]["status"] == "active" and new_run.get("official_snapshot_sha256")
                and new_run["official_snapshot_sha256"] == old_run.get("official_snapshot_sha256")):
            return None
    candidate["updated_at"] = now.isoformat()
    return candidate


def poll(store, dry_run=False):
    for attempt in range(2):
        tip = store.fetch()
        now = datetime.now(timezone.utc)
        updates = []
        active = 0
        for record in store.records(tip):
            if record.get(cli.PUBLIC, {}).get("status") == "active":
                active += 1
                if active > 1:
                    raise ValueError("multiple active public slots; reconcile the fork ledger")
                updated = refresh(record, now)
                if updated is not None:
                    updates.append(updated)
        stale = [record["id"] for record in updates if record[cli.PUBLIC].get("monitor", {}).get("stale")]
        summary = {"tip": tip, "active": active, "updated": [r["id"] for r in updates],
                   "newly_stale": stale, "dry_run": dry_run}
        if dry_run or not updates:
            return summary
        try:
            summary["committed"] = store.write(tip, updates)
            return summary
        except RuntimeError:
            # A collaborator may have advanced the ledger or a push response may have been lost.
            # Always re-fetch and recalculate before a single retry. No forced pushes.
            if attempt or store.fetch() == tip:
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report observed changes without pushing")
    args = parser.parse_args()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "https://github.com/" + repo != cli.FORK:
        parser.error("GITHUB_REPOSITORY must match the configured fork")
    token = os.environ.get("FISHTEST_LEDGER_TOKEN", "")
    if not token and not args.dry_run:
        parser.error("missing fork-scoped FISHTEST_LEDGER_TOKEN for ledger updates")
    with tempfile.TemporaryDirectory(prefix="fishtest-monitor-") as temp:
        report = poll(LedgerStore(temp, cli.FORK + ".git", token), args.dry_run)
    print(json.dumps(report))
    if report["newly_stale"]:
        sys.exit("Official result unavailable or mismatched; retained the last verified result.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(f"Fishtest monitor blocked: {error}")
