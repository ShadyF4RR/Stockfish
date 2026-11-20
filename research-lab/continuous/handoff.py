#!/usr/bin/env python3
"""Prepare a human STC packet from a verified live screen. No public submissions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import zipfile

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "continuous"))
sys.path.insert(0, str(LAB / "fishtest"))
import candidate
import cli as ft
from local_evidence import local_evidence, require
from scheduled_monitor import LedgerStore, REF

REPO = "ShadyF4RR/Stockfish"
WORKFLOW = REPO + "/.github/workflows/experiment-scheduler.yml@refs/heads/master"
DIGEST = re.compile(r"[0-9a-f]{64}")
FILES = tuple(f"{side}/{artifact}.json" for side in ("baseline", "candidate")
              for artifact in ("build", "validation", "benchmark")) + (
                  "match/match.json", "match/openings.epd", "match/games.pgn")
TASK_BINDINGS = "research-lab/config/codex-task-handoffs"


def approved_tasks(plan, implementation, review):
    # Only operator-reviewed inputs in the exact protected control commit may attest
    # native Codex tasks. A task URL or a role string supplied by an artifact is insufficient.
    candidate.branch_ref(plan["id"], None)
    ref = f"HEAD:{TASK_BINDINGS}/{plan['id']}.json"
    proc = candidate.git(LAB.parent, "cat-file", "-e", ref, check=False)
    if proc.returncode:
        return False
    size = int(candidate.git(LAB.parent, "cat-file", "-s", ref).stdout)
    require(0 < size <= 8192, "invalid owner-reviewed task binding")
    binding = json.loads(candidate.git(LAB.parent, "cat-file", "blob", ref).stdout)
    config = json.loads(candidate.git(LAB.parent, "show", "HEAD:research-lab/config/continuous.json").stdout)["subscription_cloud"]
    return (bool(config["approved_environment_id"]) and binding["environment_id"] == config["approved_environment_id"] and
            binding["experiment_id"] == plan["id"] and binding["source_commit"] == plan["source_commit"] and
            all(binding[role]["task_id"] == response["provenance"]["response_id"] and
                binding[role]["result_sha256"] == stable_hash(response)
                for role, response in (("implement", implementation), ("review", review))))


def read_bytes(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_size <= limit,
                "nonregular or oversized input")
        data = f.read(limit + 1)
        require(len(data) <= limit, "input exceeds size limit")
        return data


def read_json(path):
    return json.loads(read_bytes(path, 256 * 1024), object_pairs_hook=candidate.unique_fields)


def stable_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def verify_artifact(result, env):
    identity = {k: env["GITHUB_" + k.upper()] for k in ("repository", "run_id", "run_attempt", "sha", "workflow_ref")}
    require(identity == result["workflow"] and identity["repository"] == REPO and
            identity["workflow_ref"] == WORKFLOW, "worker workflow identity mismatch")
    require(identity["sha"] == env["GITHUB_WORKFLOW_SHA"] ==
            candidate.oid(LAB.parent, "rev-parse", "HEAD"), "untrusted control checkout")
    number, name = env["WORKER_ARTIFACT_ID"], env["WORKER_ARTIFACT_NAME"]
    digest = env["WORKER_ARTIFACT_DIGEST"].removeprefix("sha256:")
    require(re.fullmatch(r"[1-9][0-9]*", number) and DIGEST.fullmatch(digest) and
            name in {f"evidence-{tick}-{identity['run_attempt']}" for tick in ("first", "next")},
            "invalid worker artifact selector")
    token = env.get("GH_TOKEN") or env.get("FISHTEST_LEDGER_TOKEN")
    require(token, "missing GitHub artifact read access")
    path = f"repos/{REPO}/actions/artifacts/{number}"
    gh_env = {"PATH": os.environ["PATH"], "GH_TOKEN": token, "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1"}
    proc = subprocess.run(["gh", "api", path], capture_output=True, timeout=30, env=gh_env)
    require(proc.returncode == 0, "cannot verify GitHub artifact")
    artifact = json.loads(proc.stdout)
    run = artifact["workflow_run"]
    require(str(artifact["id"]) == number and artifact["name"] == name and artifact["expired"] is False
            and artifact["digest"] == "sha256:" + digest and str(run["id"]) == identity["run_id"]
            and run["head_sha"] == identity["sha"] and run["head_branch"] == "master"
            and run["repository_id"] == run["head_repository_id"] == 1465801587,
            "official worker artifact metadata differs from trusted job outputs")
    require(0 < artifact["size_in_bytes"] <= 32 * 1024 * 1024, "worker archive exceeds size bound")
    # Bind the locally downloaded result manifest to the exact GitHub artifact bytes.
    with tempfile.TemporaryFile() as archive:
        proc = subprocess.run(["gh", "api", path + "/zip"], stdout=archive, stderr=subprocess.PIPE,
                              timeout=90, env=gh_env)
        require(proc.returncode == 0 and archive.tell() == artifact["size_in_bytes"],
                "could not read complete GitHub worker archive")
        archive.seek(0)
        actual = hashlib.sha256()
        while block := archive.read(1024 * 1024):
            actual.update(block)
        require(actual.hexdigest() == digest, "GitHub archive SHA-256 differs from worker output")
        with zipfile.ZipFile(archive) as contents:
            members = [item for item in contents.infolist() if item.filename == "result.json"]
            require(len(members) == 1 and members[0].file_size <= 256 * 1024, "invalid result member in archive")
            require(json.loads(contents.read(members[0]), object_pairs_hook=candidate.unique_fields) == result,
                    "local result differs from authenticated worker artifact")
    return {"id": number, "name": name, "sha256": digest, **identity}


def role_ids(plan, edits, implementation, review, head):
    require(review.get("approved") is True, "independent review did not approve")
    ids = []
    for role, decision in (("implement", implementation), ("review", review)):
        p = decision["provenance"]
        require(p.get("adapter") == "subscription_cloud_task" and isinstance(p.get("response_id"), str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", p["response_id"])
                and p["role"] == role and p["experiment_id"] == plan["id"]
                and p["source_commit"] == plan["source_commit"] and DIGEST.fullmatch(p["task_sha256"])
                and p["edits_sha256"] == stable_hash(edits)
                and decision["usage"]["cost_usd"] == 0, "missing real role invocation or edit binding")
        if role == "review":
            require(p.get("candidate_commit") == head, "review does not identify the exact candidate commit")
        ids.append(p["response_id"])
    require(ids[0] != ids[1], "reviewer and implementer invocation IDs coincide")
    return ids


def screen_record(plan, implementation, review, output, result, artifact):
    identifier, base, head = plan["id"], plan["source_commit"], result["candidate_sha"]
    candidate.branch_ref(identifier, None)
    require(result["baseline_sha"] == base and result["source_paths"] == [plan["path"]]
            and result["rehearsal"] is False, "incorrect pinned source or path")
    edits = implementation["edits"]
    require(candidate.reconstruct(LAB.parent, base, identifier, plan["path"], edits) == head,
            "candidate differs from reconstructed edit")
    who, reviewer = role_ids(plan, edits, implementation, review, head)
    now = ft.ledger.now()
    evidence = dict.fromkeys(sorted(ft.ledger.FIELDS))
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for name in FILES:
            path = output / name
            require(not path.parent.is_symlink(), "symlinked artifact directory")
            raw = read_bytes(path, 8 * 1024 * 1024)
            require(hashlib.sha256(raw).hexdigest() == result["evidence_sha256"][name],
                    "worker file digest mismatch: " + name)
            dest = stage / name
            dest.parent.mkdir(exist_ok=True)
            dest.write_bytes(raw)
        build = read_json(stage / "candidate/build.json")
        baseline_build = read_json(stage / "baseline/build.json")
        for b in (build, baseline_build):
            require(DIGEST.fullmatch(b["binary_sha256"]), "invalid binary SHA-256")
        match = read_json(stage / "match/match.json")
        require(match["decision"] == result["decision"] == "escalate" and match["games"] == result["games"],
                "screen decision or number of games differs from worker")
        evidence.update(build_configuration={**build["build_flags"], "implementer": who},
                        hardware=build["hardware"], compiler=build["compiler"],
                        benchmark=result["benchmark"], correctness={"baseline": read_json(stage / "baseline/validation.json"),
                            "candidate": read_json(stage / "candidate/validation.json")},
                        match_settings=match["settings"], games=match["games"], elo_estimate=match["elo"],
                        elo_uncertainty=match["elo_ci95"], statistical_decision=match["decision"],
                        compute_usage=result["compute"], artifacts=artifact,
                        review={"reviewer": reviewer, "reviewed_sha": head, "verdict": "approved",
                                "unresolved_defects": [], "summary": review["reason"]})
        record = dict(schema_version=1, id=identifier, topic=plan["topic"], hypothesis=plan["hypothesis"],
                      responsible_agent="coordinator", baseline_commit=base, candidate_commit=head,
                      source_changes=[plan["path"]], status="screened", created_at=now, updated_at=now,
                      explanation="Live local paired screening cleared the registered escalation rule.",
                      related_experiments=[], retry_reason=None, evidence=evidence)
        record["reports"] = [dict(role=role, time=now, candidate_commit=head, baseline_commit=base,
                                  summary=summary, evidence=fields) for role, summary, fields in (
            ("implementation", "Pinned engine edit reconstructed by the publisher.",
             {"build_configuration": evidence["build_configuration"]}),
            ("reviewer", review["reason"], {"review": evidence["review"]}),
            ("testing", record["explanation"], {"artifacts": artifact, "statistical_decision": match["decision"]}))]
        local = local_evidence(record, stage / "baseline", stage / "candidate", stage / "match",
                               stage / "match/openings.epd", record["explanation"], verified_worker_manifests=True)
        require(local["base_signature"] == result["benchmark"]["baseline"] and
                local["candidate_signature"] == result["benchmark"]["candidate"], "bench result mismatch")
    local["username"] = ft.ledger.CONFIG["public_fishtest_username"]
    require(local["username"] == "ShadyF4RR", "unexpected public account configuration")
    packet = ft.make_packet(record, local, "stc")
    record[ft.PUBLIC] = dict(status="prepared", local=local, packet=packet, runs={},
                             patch_id=ft.patch_id(base, head), explanation=record["hypothesis"])
    record["continuous_handoff"] = {"artifact": artifact, "edit_sha256": stable_hash(edits),
                                    "implementer": who, "reviewer": reviewer}
    return record


def publish(store, record, control_repo, control_sha):
    def tip():
        rows = store.git("ls-remote", "--refs", store.remote, REF).splitlines()
        if rows:
            return store.fetch()
        store.git("fetch", "--no-tags", "--depth=1", str(control_repo), control_sha)
        return control_sha

    def existing(head):
        return next((r for r in store.records(head) if r["id"].casefold() == record["id"].casefold()), None)

    for attempt in range(2):
        before = tip()
        old = existing(before)
        if old:
            require(old["id"] == record["id"] and old["baseline_commit"] == record["baseline_commit"] and
                    old["candidate_commit"] == record["candidate_commit"] and
                    old.get("continuous_handoff") == record["continuous_handoff"],
                    "ledger ID already belongs to a different handoff")
            return "already_prepared", before
        try:
            store.write(before, [record], f"Prepare reviewed STC packet for {record['id']}")
        except RuntimeError:
            if attempt:
                raise
            continue
        after = store.fetch()
        found = existing(after)
        require(found and found.get("continuous_handoff") == record["continuous_handoff"] and
                found["candidate_commit"] == record["candidate_commit"], "ledger write was not confirmed")
        return "prepared", after
    raise ValueError("unable to publish STC preparation")


def handoff(plan, implementation, review, output, env):
    info = {"experiment_id": plan.get("id"), "status": "skipped"}
    if plan.get("rehearsal") is not None or plan.get("action") != "run":
        return dict(info, reason="only live coding experiments can enter public packet preparation")
    if env.get("GITHUB_WORKFLOW_REF") != WORKFLOW or env.get("GITHUB_REF") != "refs/heads/master":
        return dict(info, reason="live public-packet writes require the trusted default-branch scheduler")
    result = read_json(output / "result.json")
    if result.get("status") != "screened" or result.get("decision") != "escalate" or result.get("rehearsal") is not False:
        return dict(info, reason="local result did not meet the registered STC escalation rule")
    if not approved_tasks(plan, implementation, review):
        return dict(info, status="awaiting_cloud_tasks", reason="no owner-verified subscription Codex task bindings")
    artifact = verify_artifact(result, env)
    record = screen_record(plan, implementation, review, output, result, artifact)
    packet = record[ft.PUBLIC]["packet"]
    if ft.public_ref(ft.OFFICIAL, "master") != packet["base_sha"]:
        return dict(info, status="rebase_required", reason="official master advanced; rebase and repeat local tests")
    require(ft.public_ref(ft.FORK, packet["branch"]) == packet["candidate_sha"], "published candidate ref changed")
    token = env.get("FISHTEST_LEDGER_TOKEN")
    require(token, "missing fork ledger write token")
    with tempfile.TemporaryDirectory() as tmp:
        store = LedgerStore(tmp, candidate.FORK, token)
        status, head = publish(store, record, LAB.parent, env["GITHUB_WORKFLOW_SHA"])
    return dict(info, status=status, reason="STC packet awaits authorized human reservation and official submission",
                candidate_sha=packet["candidate_sha"], ledger_sha=head, stage="stc")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("plan", "implementation", "review", "output"):
        parser.add_argument(field, type=Path)
    args = parser.parse_args()
    try:
        plan = read_json(args.plan)
        if plan.get("action") != "run" or plan.get("rehearsal") is not None or os.getenv("GITHUB_WORKFLOW_REF") != WORKFLOW:
            summary = handoff(plan, {}, {}, args.output, os.environ)
        else:
            summary = handoff(plan, read_json(args.implementation), read_json(args.review), args.output, os.environ)
        print(json.dumps(summary))
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        sys.exit(f"Fishtest handoff blocked: {exc}")


if __name__ == "__main__":
    main()
