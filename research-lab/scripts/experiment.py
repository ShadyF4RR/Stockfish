#!/usr/bin/env python3
"""Versioned local experiment queue. See experiments/README.md for commands and recovery."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

LAB = Path(__file__).resolve().parents[1]
ROOT = LAB.parent
QUEUE = LAB / "experiments"
CONFIG = json.loads((LAB / "config/agents.yaml").read_text())
FIELDS = {
    "build_configuration", "hardware", "compiler", "correctness", "benchmark",
    "match_settings", "games", "elo_estimate", "elo_uncertainty",
    "statistical_decision", "compute_usage", "review", "fishtest",
    "artifacts", "hardware_review", "compression",
}
TERMINAL = ("rejected", "failed", "inconclusive", "screened", "documented")


def git(*args, cwd=ROOT):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(value):
    if not re.fullmatch("[A-Za-z][A-Za-z0-9-]{0,62}", value):
        raise argparse.ArgumentTypeError("use 1–63 letters, digits or hyphens, starting with a letter")
    return value


def nonempty(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("empty value")
    return value.strip()


def records():
    return [load_record(p.stem) for p in sorted(QUEUE.glob("*.json"))]


def load_record(identifier):
    record = json.loads((QUEUE / (identifier + ".json")).read_text())
    if record["id"] != identifier or not re.fullmatch("[A-Za-z][A-Za-z0-9-]{0,62}", identifier):
        raise ValueError("record ID must be a safe slug matching the filename")
    if not re.fullmatch("[0-9a-f]{40}", record["baseline_commit"]):
        raise ValueError("record requires a full baseline commit hash")
    return record


@contextmanager
def ledger_lock():
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    with (common / "research-lab-ledger.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if git("branch", "--show-current") != CONFIG["ledger_branch"]:
            raise ValueError(f"run mutations from a checkout of {CONFIG['ledger_branch']}")
        if git("status", "--porcelain", "--", str(QUEUE)):
            raise ValueError("ledger has pending experiment changes; review and commit them before retrying")
        yield


def save(record, event):
    path = QUEUE / (record["id"] + ".json")
    record["updated_at"] = now()
    with tempfile.NamedTemporaryFile(mode="w", dir=QUEUE, delete=False) as tmp:
        json.dump(record, tmp, indent=2, sort_keys=True, allow_nan=False)
        tmp.write("\n")
    os.replace(tmp.name, path)
    git("add", "--", str(path))
    git("commit", "--only", "-m", f"Lab {record['id']}: {event}", "--", str(path))


def propose(args):
    queue = records()
    if any(r["id"].casefold() == args.id.casefold() for r in queue):
        raise ValueError("experiment ID already exists")
    if sum(r["created_at"][:10] == now()[:10] for r in queue) >= CONFIG["limits"]["new_hypotheses_per_utc_day"]:
        raise ValueError("daily proposal quota reached")
    def normalized(hypothesis):
        return " ".join(hypothesis.casefold().split())

    same = [r for r in queue if r["topic"].casefold() == args.topic.casefold()
            or normalized(r["hypothesis"]) == normalized(args.hypothesis)]
    if same and (not args.retry_reason or any(r["status"] not in TERMINAL for r in same)):
        raise ValueError("duplicate topic/hypothesis; archived retries need a documented new reason")
    baseline = git("rev-parse", "--verify", f"refs/heads/{CONFIG['baseline_branch']}^{{commit}}")
    save({
        "schema_version": 1, "id": args.id, "topic": args.topic, "hypothesis": args.hypothesis,
        "responsible_agent": args.agent, "baseline_commit": baseline, "candidate_commit": None,
        "source_changes": [], "status": "proposed", "created_at": now(), "explanation": None,
        "related_experiments": [r["id"] for r in same], "retry_reason": args.retry_reason,
        "evidence": dict.fromkeys(sorted(FIELDS)), "reports": [],
    }, "proposal")


def worktree(record):
    return ROOT.parent / (ROOT.name + "-candidates") / record["id"]


def branch(record):
    return "experiment/" + record["id"]


def inspect_tree(record):
    tree = worktree(record)
    if Path(git("rev-parse", "--show-toplevel", cwd=tree)).resolve() != tree.resolve():
        raise ValueError("candidate path is not a worktree root")
    if git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=tree) != git("rev-parse", "--path-format=absolute", "--git-common-dir"):
        raise ValueError("candidate belongs to another repository")
    if git("branch", "--show-current", cwd=tree) != branch(record):
        raise ValueError("wrong candidate branch")
    head = git("rev-parse", "HEAD", cwd=tree)
    git("merge-base", "--is-ancestor", record["baseline_commit"], head)
    return head


def snapshot(record, require_clean):
    tree = worktree(record)
    head = inspect_tree(record)
    if require_clean and git("status", "--porcelain", cwd=tree):
        raise ValueError("candidate must be clean; commit source changes before reporting")
    if head != record["candidate_commit"]:
        # Earlier handoffs stay in reports but are never evidence for a revised candidate.
        record["evidence"] = dict.fromkeys(sorted(FIELDS))
    record["candidate_commit"] = head
    record["source_changes"] = git("diff", "--name-only", record["baseline_commit"], head, "--").splitlines()


def start(record):
    state = record["status"]
    if state not in ("proposed", "active"):
        raise ValueError("experiment is archived")
    if state == "proposed":
        if sum(r["status"] == "active" for r in records()) >= CONFIG["limits"]["concurrent_coding_experiments"]:
            raise ValueError("coding concurrency limit reached")
        if worktree(record).exists() or git("branch", "--list", branch(record)):
            raise ValueError("candidate path or branch already exists")
        record["status"] = "active"
        save(record, "reserve coding slot")
    tree = worktree(record)
    if not tree.exists():
        tree.parent.mkdir(exist_ok=True)
        if git("branch", "--list", branch(record)):
            git("worktree", "add", str(tree), branch(record))
        else:
            git("worktree", "add", "-b", branch(record), str(tree), record["baseline_commit"])
    inspect_tree(record)
    if record["candidate_commit"] is None:
        snapshot(record, require_clean=True)
        save(record, "initialize candidate")
    print(tree)


def handoff(record, args):
    report = json.loads(args.file.read_text())
    if not isinstance(report, dict) or "candidate_commit" not in report:
        raise ValueError("handoff requires a candidate_commit field")
    if record["status"] == "proposed":
        if report.get("baseline_commit") != record["baseline_commit"]:
            raise ValueError("a pre-implementation handoff requires the exact baseline commit")
    elif record["status"] in ("active", "screened"):
        screened_commit = record["candidate_commit"] if record["status"] == "screened" else None
        snapshot(record, require_clean=True)
        if screened_commit and record["candidate_commit"] != screened_commit:
            raise ValueError("new evidence for a screened experiment must refer to its screened commit")
    else:
        raise ValueError("experiment is archived")
    if report["candidate_commit"] != record["candidate_commit"]:
        raise ValueError("handoff must identify the exact current candidate commit")
    if not isinstance(report.get("summary"), str) or not report["summary"].strip():
        raise ValueError("handoff needs a summary")
    evidence = report.get("evidence")
    if not isinstance(evidence, dict) or evidence.keys() - FIELDS:
        raise ValueError("unrecognized evidence fields")
    record["reports"].append({
        "role": args.role, "time": now(), "summary": report["summary"],
        "baseline_commit": record["baseline_commit"], "candidate_commit": record["candidate_commit"],
        "evidence": evidence,
    })
    record["evidence"].update(evidence)
    save(record, f"{args.role} handoff")


def finish(record, args):
    if record["status"] in TERMINAL and not (record["status"] == "screened" and args.status != "screened"):
        raise ValueError("experiment is already archived")
    played = args.status in ("inconclusive", "screened")
    if played:
        snapshot(record, require_clean=True)
    elif (worktree(record) / ".git").is_file():
        try:
            snapshot(record, require_clean=False)
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            # An unusable checkout must still be archivable and release its slot.
            record["checkout_error"] = str(error)
    if args.status == "documented" and not all(record["evidence"].get(k) for k in ("artifacts", "compression")):
        raise ValueError("a packaging-only conclusion requires measured artifact and compression evidence")
    if played:
        evidence = record["evidence"]
        required = FIELDS - {"fishtest", "review", "artifacts", "hardware_review", "compression"}
        missing = sorted(k for k in required if evidence[k] is None and k not in {"elo_estimate", "elo_uncertainty"})
        games = evidence["games"]
        if missing or type(games) is not int or games < 2 or games % 2:
            raise ValueError(f"a played screen needs documented settings and results (missing {missing})")
    record.update(status=args.status, explanation=args.explanation)
    save(record, f"archive as {args.status}")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="print queue summary for this checkout as JSON")
    show = commands.add_parser("show", help="print a full record")
    show.add_argument("id", type=slug)
    proposal = commands.add_parser("propose", help="admit one hypothesis against the pinned local baseline")
    proposal.add_argument("id", type=slug)
    proposal.add_argument("topic", type=slug, help="stable conceptual duplicate key")
    proposal.add_argument("agent", choices=CONFIG["research_roles"])
    proposal.add_argument("hypothesis", type=nonempty)
    proposal.add_argument("--retry-reason", type=nonempty)
    begin = commands.add_parser("start", help="reserve a slot, then create or recover its candidate worktree")
    begin.add_argument("id", type=slug)
    report = commands.add_parser("report", help="append a structured, commit-specific agent handoff")
    report.add_argument("id", type=slug)
    report.add_argument("role", choices=CONFIG["roles"])
    report.add_argument("file", type=Path)
    end = commands.add_parser("finish", help="archive an experiment and release its slot")
    end.add_argument("id", type=slug)
    end.add_argument("status", choices=TERMINAL)
    end.add_argument("explanation", type=nonempty)
    args = cli.parse_args()
    if args.command == "list":
        print(json.dumps([{k: r[k] for k in ("id", "topic", "status", "created_at")} for r in records()], indent=2))
    elif args.command == "show":
        print((QUEUE / (args.id + ".json")).read_text(), end="")
    else:
        with ledger_lock():
            if args.command == "propose":
                propose(args)
            else:
                record = load_record(args.id)
                if args.command == "start":
                    start(record)
                elif args.command == "report":
                    handoff(record, args)
                else:
                    finish(record, args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        sys.exit(f"experiment: {e}")
