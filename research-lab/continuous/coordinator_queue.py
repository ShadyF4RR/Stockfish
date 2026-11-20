#!/usr/bin/env python3
"""Atomic, Git-backed research queue. Run --help for the control interface."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "config/continuous.json").read_text())
METERS = ("requests", "input_tokens", "output_tokens", "cost_usd", "runner_minutes")
FINAL = ("screened", "inconclusive", "rejected", "failed", "proposed")
SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}")
SHA = re.compile(r"[0-9a-f]{40}")
ENGINE_PATH = re.compile(r"src/(?:[a-zA-Z0-9_]+/)*[a-zA-Z0-9_]+\.(?:cpp|h)")
MAX_STATE = 24 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def iso(now):
    return now.isoformat(timespec="seconds")


def dimensions(data):
    require(isinstance(data, dict) and set(METERS).issubset(data), "all usage dimensions are required")
    values = {}
    for key in METERS:
        value = data[key]
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0, "invalid " + key)
        if key == "cost_usd":
            # Integer microdollar comparisons keep reservation accounting conservative.
            values[key] = float(Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING))
        else:
            require(key == "runner_minutes" or type(value) is int, "invalid token or request count")
            values[key] = math.ceil(value)
    return values


def zero():
    return dict.fromkeys(METERS, 0)


def total(charges):
    out = {k: Decimal(0) for k in METERS}
    for charge in charges:
        for k in METERS:
            out[k] += Decimal(str(charge[k]))
    return {k: float(v) if k == "cost_usd" else int(v) for k, v in out.items()}


def canonical(proposal):
    require(isinstance(proposal, dict), "invalid proposal")
    topic = proposal["topic"].strip().lower()
    text = " ".join(proposal["hypothesis"].split())
    path = proposal["path"]
    require(re.fullmatch(r"[a-z][a-z0-9-]{0,95}", topic) and 8 <= len(text) <= 1200
            and ENGINE_PATH.fullmatch(path), "invalid topic, hypothesis or engine source path")
    return dict(topic=topic, hypothesis=text, path=path)


def attempts(state):
    for item in state["items"]:
        yield from item["attempts"]


def count_today(state, now, kind):
    return sum(it["kind"] == kind and it["created_at"][:10] == now.date().isoformat() for it in state["items"])


def admit(state, proposal, now, config):
    p = canonical(proposal)
    all_proposals = state["items"] + state["deferred"]
    if any(it["topic"] == p["topic"] or it["hypothesis"].casefold() == p["hypothesis"].casefold()
           for it in all_proposals):
        return "duplicate"
    if count_today(state, now, "run") >= config["max_hypotheses_per_day"]:
        if len(state["deferred"]) >= 12:
            return "backlog_full"
        state["deferred"].append(p)
        return "deferred"
    add_item(state, p, "run", now)
    return "admitted"


def add_item(state, proposal, kind, now):
    prefix = f"R{state['rehearsal']}-" if state["rehearsal"] else ""
    identifier = f"{prefix}EXP-CONT-{len(state['items']) + 1:06d}"
    item = dict(**proposal, id=identifier, kind=kind, created_at=iso(now), status="queued",
                baseline=None, attempts=[])
    state["items"].append(item)
    return item


def bootstrap(rehearsal, now, config):
    state = {"schema": 1, "rehearsal": rehearsal, "paused": None, "items": [], "deferred": []}
    admit(state, config["bootstrap"], now, config)
    return state


def accounting(state, now):
    today = now.date().isoformat()
    daily, lifetime, reserved = [], [], []
    for attempt in attempts(state):
        cost = attempt.get("usage") or attempt["budget"]
        lifetime.append(cost)
        # A cross-midnight claim is conservatively counted on every UTC date it touched.
        if attempt["started_at"][:10] <= today <= attempt.get("ended_at", iso(now))[:10]:
            daily.append(cost)
        if "ended_at" not in attempt:
            reserved.append(attempt["budget"])
    return {"daily": total(daily), "lifetime": total(lifetime), "reserved": total(reserved)}


def limits(state, config):
    if state["rehearsal"]:
        return [dict(zero(), runner_minutes=config[k]) for k in
                ("rehearsal_daily_runner_minutes", "rehearsal_lifetime_runner_minutes")]
    return [dimensions(config[k]) for k in ("daily_budget", "lifetime_budget")]


def expire(state, now, config):
    for item in state["items"]:
        if item["status"] == "running":
            attempt = item["attempts"][-1]
            if datetime.fromisoformat(attempt["expires_at"]) <= now:
                attempt.update(ended_at=iso(now), outcome="expired", reason="Lease expired before settlement")
                item["status"] = "queued" if len(item["attempts"]) < config["max_attempts"] else "failed"


def plan(state, args, now, config):
    require(SHA.fullmatch(args.baseline) and SLUG.fullmatch(args.job), "invalid immutable baseline or job ID")
    idle = lambda reason: {"action": "idle", "reason": reason}
    expire(state, now, config)
    if state["paused"]:
        return idle("paused")
    if any(a["job"] == args.job for a in attempts(state)):
        return idle("job_already_claimed")
    if any(i["status"] == "running" for i in state["items"]):
        return idle("campaign_active")
    while state["deferred"] and count_today(state, now, "run") < config["max_hypotheses_per_day"]:
        admit(state, state["deferred"].pop(0), now, config)
    queued = next((i for i in state["items"] if i["status"] == "queued"), None)
    kind = queued["kind"] if queued else "research"
    if queued is None:
        if count_today(state, now, "run") >= config["max_hypotheses_per_day"]:
            return idle("daily_proposal_limit")
        if count_today(state, now, "research") >= config["max_discovery_attempts_per_day"]:
            return idle("daily_discovery_limit")
    if args.action == "cloud-claim":
        require(not state["rehearsal"] and config["subscription_cloud"]["operator_claims_enabled"]
                and args.confirm_hosted_subscription_task, "an operator-authorized hosted subscription task is required")
    if not state["rehearsal"] and args.action != "cloud-claim":
        return idle("subscription_dispatch_unavailable")
    provider = "rehearsal" if state["rehearsal"] else "subscription_cloud_task"
    minutes = getattr(args, "reserve_minutes", None)
    minutes = config["runner_minutes_per_attempt"] if minutes is None else minutes
    require(type(minutes) is int and 1 <= minutes <= config["runner_minutes_per_attempt"],
            "reservation must fit the configured attempt limit")
    budget = dimensions(dict(zero(), runner_minutes=minutes))
    usage = accounting(state, now)
    for key, cap in zip(("daily", "lifetime"), limits(state, config)):
        expected = total([usage[key], budget])
        if any(expected[m] > cap[m] for m in METERS):
            return idle(key + "_budget")
    if queued is None:
        queued = add_item(state, dict(topic="research-discovery", hypothesis="", path="src/search.cpp"), kind, now)
    queued["baseline"] = queued["baseline"] or args.baseline
    lease = uuid.uuid4().hex
    attempt = dict(job=args.job, lease=lease, started_at=iso(now), budget=budget, provider=provider, checkpoints=[],
                   expires_at=iso(now + timedelta(minutes=config["lease_minutes"])))
    queued["attempts"].append(attempt)
    queued["status"] = "running"
    previous = [{"outcome": a["outcome"], "reason": a.get("reason"), "candidate_sha": a.get("candidate_sha"),
                 "checkpoints": [c["stage"] for c in a["checkpoints"]]} for a in queued["attempts"][:-1]]
    history = [{k: item[k] for k in ("id", "topic", "hypothesis", "status")} for item in state["items"]
               if item is not queued and item["kind"] == "run"][-60:]
    return dict(action=kind, id=queued["id"], topic=queued["topic"], hypothesis=queued["hypothesis"],
                path=queued["path"], lease=lease, attempt=len(queued["attempts"]), job=args.job, provider=provider,
                source_commit=queued["baseline"], baseline=queued["baseline"], budget=budget, history=history,
                rehearsal=state["rehearsal"], expires_at=attempt["expires_at"], previous_attempts=previous)


def lease_item(state, args):
    item = next((it for it in state["items"] if it["id"] == args.id), None)
    require(item and item["attempts"], "unknown attempt")
    attempt = item["attempts"][-1]
    require(attempt["lease"] == args.lease, "obsolete lease")
    return item, attempt


def finish(state, item, attempt, result, now, config):
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if attempt.get("receipt") == digest:
        return {"id": item["id"], "status": item["status"], "replayed": True}
    require(item["status"] == "running" and datetime.fromisoformat(attempt["expires_at"]) > now,
            "lease is closed or expired")
    status = result["status"]
    reason = result["reason"]
    require(status in FINAL and isinstance(reason, str) and 0 < len(reason.strip()) <= 4000, "invalid result")
    require(status != "proposed" or item["kind"] == "research", "proposal-only closure requires discovery task")
    require(item["kind"] == "run" or status in ("proposed", "failed"), "unexpected discovery result")
    sha = result.get("candidate_sha")
    require(sha is None or SHA.fullmatch(sha), "invalid candidate SHA")
    suggestions = result.get("next_hypotheses", [])
    require(isinstance(suggestions, list) and len(suggestions) <= 3, "too many next proposals")
    usage = dimensions(result["usage"]) if result.get("usage") is not None else None
    attempt.update(ended_at=iso(now), outcome=status, reason=reason.strip(), usage=usage, receipt=digest,
                   candidate_sha=sha, artifacts=result.get("artifacts"), next_proposals=[])
    item["status"] = status
    if status == "failed" and result.get("retryable") is True and len(item["attempts"]) < config["max_attempts"]:
        item["status"] = "queued"
    for proposal in suggestions:
        try:
            decision = admit(state, proposal, now, config)
        except (ValueError, KeyError, TypeError, AttributeError):
            decision = "invalid"
        attempt["next_proposals"].append(decision)
    if usage and any(usage[k] > attempt["budget"][k] for k in METERS):
        state["paused"] = "Measured usage exceeded an attempt reservation. Review budgets before resuming."
    return {"id": item["id"], "status": item["status"], "next_proposals": attempt["next_proposals"],
            "paused": state["paused"]}


def transition(state, args, now, config):
    if args.action in ("plan", "cloud-claim"):
        return plan(state, args, now, config)
    if args.action == "pause":
        state["paused"] = args.reason
    elif args.action == "resume":
        state["paused"] = None
    elif args.action == "handoff-test":
        # Preserve the original text, attempts and chronology for audit.
        for it in state["deferred"] + state["items"]:
            if it["topic"] != args.topic:
                continue
            require(args.candidate in it["hypothesis"] and args.baseline in it["hypothesis"],
                    "legacy request has different engine pins")
            require(it in state["deferred"] or it["status"] in ("queued","testing_handoff"),
                    "legacy entry is already running or completed")
            if it in state["deferred"]:
                state["deferred"].remove(it)
                state.setdefault("testing_handoffs", []).append(dict(it, at=iso(now), match=args.match))
            else:
                it.update(status="testing_handoff", match=args.match)
            return {"handoff":args.match}
        return {"already_absent":args.topic}
    elif args.action == "propose":
        return {"decision": admit(state, vars(args), now, config)}
    elif args.action in ("checkpoint", "finalize"):
        item, attempt = lease_item(state, args)
        if args.action == "finalize":
            return finish(state, item, attempt, args.payload, now, config)
        require(item["status"] == "running" and datetime.fromisoformat(attempt["expires_at"]) > now,
                "lease is closed or expired")
        require(SLUG.fullmatch(args.stage), "invalid checkpoint stage")
        data = dict(stage=args.stage, details=args.payload)
        for ck in attempt["checkpoints"]:
            if ck["stage"] == args.stage:
                require(ck["details"] == args.payload, "conflicting checkpoint")
                return {"checkpoint": args.stage, "replayed": True}
        require(len(attempt["checkpoints"]) < 30, "checkpoint limit reached")
        attempt["checkpoints"].append(dict(**data, time=iso(now)))
        return {"checkpoint": args.stage, "paused": state["paused"]}
    return {"paused": state["paused"], "accounting": accounting(state, now), "state": state}


class QueueStore:
    """Access a small, data-only commit tree; a ref race always requires re-planning."""
    def __init__(self, directory, remote, ref, token=None, auth_config=None):
        self.directory, self.remote, self.ref = directory, remote, ref
        self.env = dict(PATH=os.environ["PATH"], GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
                        GIT_AUTHOR_NAME="Research coordinator", GIT_AUTHOR_EMAIL="research-lab@users.noreply.github.com",
                        GIT_COMMITTER_NAME="Research coordinator", GIT_COMMITTER_EMAIL="research-lab@users.noreply.github.com")
        self.auth_config = auth_config
        if auth_config:
            # Git's existing credential helper handles the approved fork URL. Python never
            # requests the credential text. Only Git transport runs with these settings.
            for key in ("HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "GH_CONFIG_DIR", "GITHUB_TOKEN",
                        "GH_TOKEN", "GIT_ASKPASS", "SSH_AUTH_SOCK", "HTTPS_PROXY", "HTTP_PROXY",
                        "NO_PROXY", "https_proxy", "http_proxy", "no_proxy", "SSL_CERT_FILE"):
                if key in os.environ:
                    self.env[key] = os.environ[key]
            self.env.pop("GIT_CONFIG_NOSYSTEM", None)
            self.env.pop("GIT_CONFIG_GLOBAL", None)
        if token:
            encoded = base64.b64encode(("x-access-token:" + token).encode()).decode()
            self.env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http." + remote + ".extraheader",
                            GIT_CONFIG_VALUE_0="Authorization: Basic " + encoded)
        self.git("init", "--bare", "--template=")

    def git(self, *args, data=None):
        include = ["-c", "include.path=" + str(self.auth_config)] if self.auth_config and args[0] in (
            "ls-remote", "fetch", "push") else []
        result = subprocess.run(["git", *include, "-c", "core.hooksPath=/dev/null", "-c", "http.followRedirects=false", *args],
                                cwd=self.directory, env=self.env, input=data, capture_output=True, timeout=90)
        if result.returncode:
            raise RuntimeError("Git " + args[0] + " failed")
        return result.stdout

    def read(self):
        if not self.git("ls-remote", "--refs", self.remote, self.ref):
            return None, None
        self.git("fetch", "--depth=1", "--no-tags", "--recurse-submodules=no", self.remote, self.ref)
        tip = self.git("rev-parse", "FETCH_HEAD^{commit}").decode().strip()
        entry = self.git("ls-tree", tip, "--", "queue.json")
        if not re.fullmatch(rb"100644 blob [a-f0-9]{40}\tqueue\.json\n", entry):
            raise ValueError("queue branch lacks regular queue.json")
        oid = entry.split()[2].decode()
        require(int(self.git("cat-file", "-s", oid)) <= MAX_STATE, "queue state exceeds size limit")
        return tip, json.loads(self.git("cat-file", "blob", oid))

    def save(self, tip, state, action):
        data = (json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        require(len(data) <= MAX_STATE, "queue state exceeds size limit")
        self.git("read-tree", tip or "--empty")
        oid = self.git("hash-object", "-w", "--stdin", data=data).decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "100644", oid, "queue.json")
        tree = self.git("write-tree").decode().strip()
        parent = ["-p", tip] if tip else []
        commit = self.git("commit-tree", tree, *parent, "-m", "Research queue " + action).decode().strip()
        self.git("push", self.remote, commit + ":" + self.ref)


def transact(store, args, config=CONFIG):
    for retry in range(3):
        tip, state = store.read()
        now = datetime.now(timezone.utc)
        if state is None:
            require(args.action == "init", "queue absent; initialize reviewed queue first")
            state = bootstrap(args.rehearsal, now, config)
        require(state["schema"] == 1 and state["rehearsal"] == args.rehearsal, "wrong queue schema or namespace")
        before = json.dumps(state, sort_keys=True)
        response = transition(state, args, now, config)
        if tip is not None and before == json.dumps(state, sort_keys=True):
            return response
        try:
            store.save(tip, state, args.action)
            return response
        except RuntimeError:
            if retry == 2 or store.read()[0] == tip:
                raise


def existing_fork_git_auth():
    root = Path(__file__).resolve().parents[2]
    url = subprocess.check_output(["git", "-C", str(root), "config", "--get", "remote.origin.url"],
                                  text=True, stderr=subprocess.DEVNULL).strip()
    fork = CONFIG["fork"]
    require(url.rstrip("/") in ("https://github.com/" + fork + ".git", "https://github.com/" + fork),
            "existing Git authorization requires the configured HTTPS fork checkout")
    common = subprocess.check_output(["git", "-C", str(root), "rev-parse", "--path-format=absolute",
                                      "--git-common-dir"], text=True, stderr=subprocess.DEVNULL).strip()
    return Path(common) / "config"


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--rehearsal", help="isolate a numeric Actions run in its own fork queue")
    cmd = cli.add_subparsers(dest="action", required=True)
    for name in ("init", "status", "resume"):
        cmd.add_parser(name)
    cmd.add_parser("pause").add_argument("--reason", required=True)
    migration = cmd.add_parser("handoff-test")
    for field in ("topic","candidate","baseline","match"):
        migration.add_argument("--" + field, required=True)
    prop = cmd.add_parser("propose")
    for flag in ("topic", "hypothesis", "path"):
        prop.add_argument("--" + flag, required=True)
    for name in ("plan", "cloud-claim"):
        planner = cmd.add_parser(name)
        for flag in ("baseline", "job"):
            planner.add_argument("--" + flag, required=True)
        planner.add_argument("--out", type=Path)
        if name == "cloud-claim":
            planner.add_argument("--reserve-minutes", type=int, help="reserve a smaller bounded supervised campaign")
            planner.add_argument("--confirm-hosted-subscription-task", action="store_true", required=True,
                                 help="operator confirms this ordinary subscription task was already started")


    for name in ("checkpoint", "finalize"):
        sub = cmd.add_parser(name)
        for flag in ("id", "lease"):
            sub.add_argument("--" + flag, required=True)
        if name == "checkpoint":
            sub.add_argument("--stage", required=True)
            sub.add_argument("--details-file", type=Path)
        else:
            sub.add_argument("--result", required=True, type=Path)
    args = cli.parse_args()

    if args.rehearsal is not None:
        require(re.fullmatch(r"[1-9][0-9]{0,19}", args.rehearsal), "invalid rehearsal run ID")
    if args.action == "pause":
        require(0 < len(args.reason.strip()) <= 512, "invalid pause reason")
    source = getattr(args, "result", None) or getattr(args, "details_file", None)
    if source:
        require(source.stat().st_size <= 32768, "result/checkpoint file too large")
    args.payload = json.loads(source.read_text()) if source else {}
    require(os.environ.get("GITHUB_REPOSITORY", CONFIG["fork"]) == CONFIG["fork"] == "ShadyF4RR/Stockfish",
            "unexpected fork")
    if args.action == "cloud-claim":
        require(os.environ.get("GITHUB_ACTIONS") != "true", "GitHub Actions cannot claim a hosted subscription task")

    ref = "refs/heads/research/queue" if not args.rehearsal else f"refs/heads/research/rehearsal/{args.rehearsal}/queue"
    token = os.environ.get("QUEUE_GITHUB_TOKEN")
    auth_config = existing_fork_git_auth() if not token else None
    with tempfile.TemporaryDirectory() as temp:
        response = transact(QueueStore(temp, "https://github.com/" + CONFIG["fork"] + ".git", ref, token, auth_config), args)
    data = json.dumps(response, indent=2, allow_nan=False) + "\n"
    if args.action in ("plan", "cloud-claim") and args.out:
        args.out.write_text(data)
    else:
        print(data, end="")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
        sys.exit(f"research queue: {e}")
