"""Read-only Phase 1 preflight. This module never grants permission to promote."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
ROOT = LAB.parent
SHA = re.compile(r"[0-9a-f]{40}")
VIEW = re.compile(r"https://tests\.stockfishchess\.org/tests/view/([0-9a-f]{24})")


def git(*args):
    return subprocess.run(
        ["git", "--no-replace-objects", "-C", str(ROOT), *args],
        capture_output=True, check=True,
    ).stdout


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def inspect_patch(record, policy):
    base, head = (record[key] for key in ("baseline_commit", "candidate_commit"))
    for commit in (base, head):
        require(isinstance(commit, str) and SHA.fullmatch(commit), "missing full pinned Git commit")
        require(git("cat-file", "-t", commit).strip() == b"commit", "pin is not a commit")
    require(base != head, "candidate and baseline are identical")
    git("merge-base", "--is-ancestor", base, head)
    changes = git("diff", "--no-ext-diff", "--no-renames", "--raw", "-z", base, head, "--")
    fields = changes.split(b"\0")
    require(changes and fields[-1] == b"" and len(fields) % 2 == 1, "expected an engine patch")
    paths = []
    for entry, raw_name in zip(fields[:-1:2], fields[1:-1:2]):
        old_mode, new_mode = entry.lstrip(b":").split()[:2]
        name = raw_name.decode("utf-8")
        require(name.startswith(policy["engine_path_prefix"]) and
                name not in policy["protected_engine_files"], f"path requires infrastructure review: {name!r}")
        require({old_mode, new_mode} <= {b"100644", b"000000"},
                f"unexpected file mode at {name!r}")
        paths.append(name)
    target = git("rev-parse", "--verify", "refs/heads/" + policy["integration_branch"]).decode().strip()
    require(git("rev-parse", target + ":src") != git("rev-parse", head + ":src"),
            "candidate engine is already present in the integration branch")
    # Git will exit 1 for conflicts. A clean merge can still produce a different engine.
    tree = git("merge-tree", "--write-tree", target, head).decode().strip()
    require(git("rev-parse", tree + ":src") == git("rev-parse", head + ":src"),
            "prospective integration engine differs from tested candidate; rebase and retest")
    return paths, target, tree


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog="Exit 1: blocked. Exit 3: preflight only.")
    parser.add_argument("record", type=Path, help="coordinator experiment JSON")
    args = parser.parse_args()
    policy = json.loads((LAB / "config/promotion.yaml").read_text())
    require(policy["automatic_integration_merge"] is False and
            policy["automatic_upstream_submission"] is False,
            "this Phase 1 implementation only supports disabled automation")
    record = json.loads(args.record.read_text())
    paths, target, tree = inspect_patch(record, policy)
    require(record["status"] == "screened", "requires a completed local screening record")
    evidence = record["evidence"]
    for field in ("correctness", "benchmark", "review", "statistical_decision"):
        require(evidence.get(field), f"missing {field} evidence")
    runs = evidence.get("fishtest")
    require(isinstance(runs, dict), "missing official Fishtest records")
    links = []
    for stage in ("stc", "ltc"):
        ref = runs.get(stage)
        if isinstance(ref, str) and VIEW.fullmatch(ref):
            links.append(ref)
            continue
        require(isinstance(ref, dict) and isinstance(ref.get("not_applicable"), str)
                and ref["not_applicable"].strip(), f"missing {stage} result URL or applicability rationale")
    require(links and len(set(links)) == len(links), "expected distinct official test links")
    print(json.dumps({
        "decision": "awaiting_trusted_verification",
        "record_id": record["id"],
        "changed_engine_paths": paths,
        "local_integration_tip": target,
        "prospective_merge_tree": tree,
        "reported_fishtest": runs,
        "target_elo": policy["project_target_elo"],
        "remaining_gates": [
            "Verify authoritative Fishtest results, SHA pins, time controls and test design",
            "Verify sequentially valid uncertainty against the project target",
            "Verify correctness, repeat benchmarks, independent review and regression resolution",
            "Verify live branch rules and trusted CI on the exact prospective integration commit",
        ],
    }, indent=2))
    return 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError) as exc:
        print(f"Promotion blocked: {exc}", file=sys.stderr)
        sys.exit(1)
