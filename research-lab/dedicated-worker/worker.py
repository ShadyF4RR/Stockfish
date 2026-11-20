#!/usr/bin/env python3
"""Run one trusted testing assignment at a time. Only Git transport receives a fork credential."""
import argparse
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
FORK = "https://github.com/ShadyF4RR/Stockfish.git"
REQUEST_KEYS = ("experiment", "baseline", "candidate", "pairs", "seed", "time_control")
SAFE_PATH = re.compile(r"[A-Za-z0-9_./-]+")
SHA = re.compile("[0-9a-f]{40}")


def atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(file.name, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def git_env():
    raw = os.environ.get("STOCKFISH_WORKER_TOKEN")
    if not raw and os.environ.get("CREDENTIALS_DIRECTORY"):
        raw = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "github-token").read_text().strip()
    if not raw:
        raise RuntimeError("fork publisher credential unavailable")
    basic = base64.b64encode(("x-access-token:" + raw).encode()).decode()
    env = {k: os.environ[k] for k in ("HTTP_PROXY","HTTPS_PROXY","NO_PROXY","http_proxy","https_proxy","no_proxy","SSL_CERT_FILE") if k in os.environ}
    env.update(PATH=os.defpath, HOME="/nonexistent", GIT_TERMINAL_PROMPT="0", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http." + FORK + ".extraheader",
               GIT_CONFIG_VALUE_0="Authorization: Basic " + basic)
    return env


def git(repo, args, env, data=None):
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "http.followRedirects=false", *args]
    p = subprocess.run(command, cwd=repo, env=env, input=data, capture_output=True, timeout=120)
    if p.returncode:
        raise RuntimeError("evidence Git operation failed: " + args[0])
    return p.stdout.strip()


def publish(directory, claim, receipt, *, remote=FORK, auth=None):
    """Create an immutable evidence ref. A lost push response can be retried without running games."""
    exp, lease = claim["experiment"], claim["lease"]
    if not re.fullmatch("EXP-[A-Z0-9-]{1,80}", exp) or not re.fullmatch("[a-f0-9-]{16,64}", lease):
        raise ValueError("unsafe evidence ref")
    ref = "refs/heads/research/testing-evidence/" + exp + "/" + lease
    env = git_env() if auth is None else auth.copy()
    env.update(GIT_AUTHOR_NAME="Stockfish testing worker", GIT_COMMITTER_NAME="Stockfish testing worker",
               GIT_AUTHOR_EMAIL="testing@users.noreply.github.com", GIT_COMMITTER_EMAIL="testing@users.noreply.github.com")
    with tempfile.TemporaryDirectory() as repo:
        git(repo, ["init", "--bare", "--template="], env)
        files = []
        total = 0
        for p in sorted(directory.rglob("*")):
            name = p.relative_to(directory).as_posix()
            if p.is_symlink() or not SAFE_PATH.fullmatch(name) or any(n.startswith(".") for n in p.relative_to(directory).parts):
                raise ValueError("unsafe evidence path")
            if p.is_dir():
                continue
            if not p.is_file() or p.suffix not in (".json", ".pgn", ".log", ".epd") or name == "receipt.json":
                raise ValueError("unexpected evidence file")
            if p.stat().st_size > 16*1024**2 or len(files) >= 256:
                raise ValueError("evidence file limit exceeded")
            total += p.stat().st_size
            if total > 48*1024**2:
                raise ValueError("campaign evidence exceeds upload limit")
            data = p.read_bytes()
            files.append((name, hashlib.sha256(data).hexdigest(), git(repo, ["hash-object", "-w", "--stdin"], env, data)))
        receipt = dict(receipt, sha256={name: digest for name,digest,_ in files})
        entries = [(name,oid) for name,_,oid in files]
        entries.append(("receipt.json", git(repo, ["hash-object", "-w", "--stdin"], env, encoded(receipt))))
        for name, oid in entries:
            git(repo, ["update-index", "--add", "--cacheinfo", "100644", oid.decode(), name], env)
        tree = git(repo, ["write-tree"], env)
        def readback():
            git(repo, ["fetch", "--depth=1", "--no-tags", remote, ref], env)
            if git(repo, ["rev-parse", "FETCH_HEAD^{tree}"], env) != tree:
                raise ValueError("immutable evidence ref has different content")
            return git(repo, ["rev-parse", "FETCH_HEAD"], env).decode()
        if not git(repo, ["ls-remote", "--refs", remote, ref], env):
            commit = git(repo, ["commit-tree", tree.decode(), "-m", "Record " + exp], env)
            git(repo, ["push", remote, commit.decode() + ":" + ref], env)
        return dict(receipt, evidence_commit=readback())


def dispatch(*args):
    # Output is a machine-readable decision. Never print transport environment or credentials.
    p = subprocess.run([sys.executable, "-I", HERE / "dispatch.py", *args], capture_output=True,
                       text=True, timeout=300)
    if p.returncode:
        raise RuntimeError("dispatch transition failed: " + args[0])
    return json.loads(p.stdout)


def isolated_run(request, out, max_minutes, worker, fastchess, monitor):
    group = next(line.split("::",1)[1] for line in Path("/proc/self/cgroup").read_text().splitlines()
                 if line.startswith("0::"))
    cgroup = (Path("/sys/fs/cgroup") / group.lstrip("/") / ("sf-" + uuid.uuid4().hex)).resolve()
    if Path("/sys/fs/cgroup") not in cgroup.parents:
        raise RuntimeError("invalid cgroup")
    cgroup.mkdir(mode=0o700)
    proc = None
    try:
        if not (cgroup / "cgroup.kill").is_file():
            raise RuntimeError("delegated cgroup v2 with cgroup.kill required")
        def enter_group():
            (cgroup / "cgroup.procs").write_text(str(os.getpid()))
        with (out.parent / "controller.log").open("wb") as log:
            command = [sys.executable, "-I", HERE / "executor.py", "--request", request, "--output", out,
                       "--fastchess", fastchess, "--worker", worker, "--max-minutes", str(max_minutes)]
            safe_env = {k: os.environ[k] for k in ("http_proxy","https_proxy","no_proxy","HTTP_PROXY","HTTPS_PROXY","NO_PROXY","SSL_CERT_FILE") if k in os.environ}
            safe_env.update(PATH="/usr/local/bin:/usr/bin:/bin", HOME="/nonexistent", LANG="C.UTF-8")
            proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=safe_env,
                                    preexec_fn=enter_group, start_new_session=True)
            deadline = time.monotonic() + max_minutes * 60
            while proc.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                try:
                    proc.wait(timeout=min(30, remaining))
                except subprocess.TimeoutExpired:
                    monitor()
            return proc.returncode == 0
    finally:
        if (cgroup / "cgroup.kill").exists():
            (cgroup / "cgroup.kill").write_text("1")
        if proc is not None:
            proc.wait(timeout=15)
        for _ in range(30):
            try:
                cgroup.rmdir()
                break
            except OSError:
                time.sleep(0.1)


def heartbeat(state, worker, owner, phase, claim=None, evidence=None):
    # Telemetry failures must never repeat a search or release an active campaign.
    script = HERE.parent / "dashboard" / "telemetry.py"
    if not script.is_file():
        return
    args = [sys.executable, "-I", script, "heartbeat", "--state", str(state), "--worker", worker,
            "--owner", owner, "--phase", phase]
    if claim:
        args.extend(["--experiment", claim["experiment"], "--candidate", claim["manifest"]["candidate"],
                     "--total", str(2*claim["manifest"]["pairs"])])
    if evidence:
        args.extend(["--evidence", str(evidence)])
    try:
        subprocess.run(args, timeout=45, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        print("Telemetry temporarily unavailable", flush=True)


def archive_interrupted(job, out, manifest):
    """Save the raw per-game PGNs when a deadline expired across a reboot."""
    out.mkdir(exist_ok=True)
    partial = out / "interrupted"
    partial.mkdir(exist_ok=True)
    count = 0
    for p in sorted((job / "spool").glob("primary-*/results/game-*/game.pgn")):
        name = p.parents[2].name + "-" + p.parent.name
        if not re.fullmatch(r"primary-\d{2}-game-\d{3}-(?:candidate|baseline)",name):
            raise ValueError("unexpected persisted game identity")
        if p.resolve() != p.absolute() or not p.is_file() or p.stat().st_size > 1024**2:
            raise ValueError("unsafe persisted PGN")
        data = p.read_bytes()
        atomic(partial / (name + ".pgn"),data)
        if re.search(rb"\s(?:1-0|0-1|1/2-1/2)\s*$",data):
            count += 1
    if not (out / "result.json").exists():
        atomic(out / "result.json",encoded(dict(status="invalid",decision="invalid",rehearsal=False,
            reason="Lease expired before a full verified match receipt", games=count,
            baseline_sha=manifest["baseline"],candidate_sha=manifest["candidate"])))


def tick(args, owner):
    if args.worker == "pc":
        enrollment = dispatch("ready", "--owner", owner)
        if enrollment["action"] != "ready":
            return "idle"
    claim = dispatch("claim", "--owner", owner, "--worker", args.worker)
    if claim["action"] == "idle":
        heartbeat(args.state,args.worker,owner,"idle")
        return "idle"
    if claim["action"] not in ("run","resume") or claim["owner"] != owner or claim["worker"] != args.worker:
        raise ValueError("unexpected testing assignment")
    lease = claim["lease"]
    if not re.fullmatch("[a-f0-9-]{16,64}", lease):
        raise ValueError("invalid lease")
    job = args.state / lease
    job.mkdir(exist_ok=True, mode=0o700)
    fence = ["--id", claim["experiment"], "--lease", lease, "--owner", owner, "--attempt", str(claim["attempt"])]
    manifest=claim["manifest"]
    request = job / "request.json"
    raw = encoded({k:manifest[k] for k in REQUEST_KEYS})
    if request.exists() and request.read_bytes()!=raw:
        raise ValueError("request changed across recovery")
    atomic(request, raw)
    out = job / "evidence"
    final = job / "completion.json"
    if not final.exists():
        gate = dispatch("record-start", *fence)
        if gate["action"] not in ("start_authorized", "recover_only"):
            raise RuntimeError("unexpected execution fence")
        if gate["action"] == "start_authorized":
            atomic(job/"started.json", encoded({"epoch":time.time()}))
        if not (job / "started.json").is_file():
            raise RuntimeError("missing durable start marker for existing lease")
        # Reboot or a lost upload response resumes the same lease and spool.
        # A second supervisor cannot enter while the service lock is held.
        if not (out / "result.json").is_file():
            remaining = (datetime.fromisoformat(claim["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
            minutes = min(manifest["max_minutes"], int(remaining // 60))
            if minutes > 0:
                isolated_run(request,out,minutes,args.worker,args.fastchess,
                             lambda: heartbeat(args.state,args.worker,owner,"testing",claim,out))
            if not (out / "result.json").is_file():
                if minutes > 0:
                    raise RuntimeError("incomplete executor receipt; retain lease and persistent PGNs")
                archive_interrupted(job,out,manifest)
        out.mkdir(exist_ok=True)
        atomic(out/"request.json",raw)
        measured = {}
        result_path = out/"result.json"
        if result_path.is_file() and not result_path.is_symlink() and result_path.stat().st_size <= 1024**2:
            try:
                measured = json.loads(result_path.read_text())
            except (ValueError,OSError):
                pass
        valid = measured.get("status") == "screened" and measured.get("rehearsal") is False
        if not valid:
            archive_interrupted(job,out,manifest)
        spent = manifest["max_minutes"]
        if (job/"started.json").exists():
            spent = max(1,math.ceil((time.time()-json.loads((job/"started.json").read_text())["epoch"])/60))
        receipt = {k:claim[k] for k in ("experiment","worker","owner","attempt","lease")}
        receipt.update(baseline=manifest["baseline"],candidate=manifest["candidate"],outcome="screened" if valid else "invalid",
                       games=measured.get("games",0),calibration_games=measured.get("calibration_games",0),
                       wall_minutes=spent,calibrated=bool(valid and (out/"calibration.json").is_file()),rehearsal=False)
        if not valid:
            # The isolated executor exited and its cgroup was drained. After a service
            # restart, KillMode=control-group drains children before the next start.
            receipt["stopped"] = True
        atomic(job/"receipt-base.json",encoded(receipt)) if not (job/"receipt-base.json").exists() else None
        receipt=json.loads((job/"receipt-base.json").read_text())
        atomic(final,encoded(publish(out,claim,receipt)))
    receipt=json.loads(final.read_text())
    dispatch("complete" if receipt["outcome"]=="screened" else "fail",*fence,"--receipt",str(final))
    heartbeat(args.state,args.worker,owner,"idle")
    return receipt["outcome"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["poll","identity"])
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--worker", choices=["pc","cloud"], default="pc")
    p.add_argument("--fastchess", type=Path, default=Path("/opt/stockfish-fastchess"))
    p.add_argument("--once", action="store_true")
    args=p.parse_args()
    os.umask(0o077)
    args.state.mkdir(parents=True,exist_ok=True)
    identity=args.state/"owner"
    with (args.state/"identity.lock").open("a") as identity_lock:
        fcntl.flock(identity_lock, fcntl.LOCK_EX)
        if not identity.exists():
            atomic(identity,(args.worker+"-"+uuid.uuid4().hex+"\n").encode())
        owner=identity.read_text().strip()
    if args.command=="identity":
        print(owner)
        return
    if not re.fullmatch(args.worker+"-[0-9a-f]{32}",owner):
        raise ValueError("state belongs to a different worker")
    if args.worker == "pc":
        sys.path.insert(0, str(HERE))
        import executor
        config = json.loads((HERE.parent / "config/testing.yaml").read_text())
        executor.admit("pc", config["local_match"])
        if (executor.fastchess_git(args.fastchess, "rev-parse", "HEAD") != executor.validation.FASTCHESS
                or not os.access(args.fastchess / "fastchess", os.X_OK)
                or executor.sha256(args.fastchess / "app/tests/data/openings.epd") != executor.validation.BOOK):
            raise ValueError("PC preflight failed to verify Fastchess and openings")
    with (args.state/"worker.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:
                outcome=tick(args,owner)
            except (RuntimeError,OSError,ValueError,subprocess.SubprocessError) as exc:
                if args.once:
                    raise
                print(type(exc).__name__ + ": testing service will retry its ledger",flush=True)
                outcome="blocked"
            if args.once:
                print(outcome)
                return
            time.sleep(60 if outcome in ("idle","blocked") else 3)


if __name__=="__main__":
    main()
