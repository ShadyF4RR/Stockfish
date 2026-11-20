#!/usr/bin/env python3
"""Run trusted Stage A–C drivers around two pinned engine revisions on disposable Linux."""
import argparse
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "scripts"))
from local_match import COMPARABLE, results, sha256
from worker_resources import capacity

FASTCHESS = "894616028492ae6114835195f14a899f6fa237d3"
BOOK = "421a4efcddb95c3e70b1f9ad6abbeab1edcb6ab8e9ae743d94c42a5d78a6e82a"
MARKER = b"// Research lab rehearsal: exercise the validation pipeline.\n"
NAMESPACE = b"namespace Stockfish {"
GIB = 1024 ** 3
SCREEN_PAIRS = 16  # Fixed before launch; one color reversal per opening.
WALL_LIMIT = 19 * 60
NPS_FLOOR = 0.95  # A hold for investigation on a noisy host, never an Elo decision.


def git(tree, *args):
    return subprocess.check_output(["git", "-C", tree, *args], stderr=subprocess.DEVNULL)


def head(tree):
    return git(tree, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()


def check_source(candidate, base, new, rehearsal):
    parents = git(candidate, "show", "-s", "--format=%P", new).decode().split()
    if new == base or parents != [base]:
        raise ValueError("candidate must be one distinct isolated commit on the pinned baseline")
    changes = git(candidate, "diff", "--name-status", "-z", "--no-renames", base, new).split(b"\0")[:-1]
    paths = []
    for status, name in zip(changes[::2], changes[1::2]):
        path = name.decode("ascii")
        if status != b"M" or not re.fullmatch(r"src/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.(?:cpp|h)", path):
            raise ValueError("ordinary workers accept only modifications to existing engine source")
        for ref in (base, new):
            if not git(candidate, "ls-tree", ref, "--", path).startswith(b"100644 blob "):
                raise ValueError("only regular source files may be changed")
        paths.append(path)
    if not paths:
        raise ValueError("empty candidate")
    if rehearsal:
        if len(paths) != 1:
            raise ValueError("rehearsal requires exactly one changed file")
        old = git(candidate, "show", f"{base}:{paths[0]}")
        edited = git(candidate, "show", f"{new}:{paths[0]}")
        if (old.count(NAMESPACE) != 1 or NAMESPACE not in old.splitlines() or
                edited != old.replace(NAMESPACE, MARKER + NAMESPACE)):
            raise ValueError("rehearsal source differs from the exact inert marker insertion")
    return paths


def pin_source(tree, sha, dest):
    # Standalone, sterile gitdir also works for source worktrees with external object stores.
    subprocess.run(["git", "init", "--quiet", dest], check=True)
    subprocess.run(["git", "-C", dest, "fetch", "--quiet", "--no-tags", "--depth=1",
                    str(tree), sha], check=True)
    subprocess.run(["git", "-C", dest, "checkout", "--quiet", "--detach", sha], check=True)
    if head(dest) != sha:
        raise ValueError("staged source did not match the tested commit")


def networks(tree, commit, cache, provided):
    header = git(tree, "show", commit + ":src/evaluate.h")
    names = re.findall(rb'^#define EvalFileDefaultName\w*\s+"(nn-[0-9a-f]{12}\.nnue)"', header, re.M)
    if not 1 <= len(names) <= 2:
        raise ValueError("unexpected number of declared networks")
    for raw in names:
        name = raw.decode()
        target = cache / name
        def correct(path):
            return path.is_file() and path.stat().st_size <= 512 * 1024 ** 2 and "nn-" + sha256(path)[:12] + ".nnue" == name
        if correct(target):
            continue
        if provided and correct(provided / name):
            shutil.copyfile(provided / name, target)
            continue
        for url in (f"https://tests.stockfishchess.org/api/nn/{name}",
                    f"https://github.com/official-stockfish/networks/raw/master/{name}"):
            run = subprocess.run(["curl", "-q", "--proto", "=https", "--proto-redir", "=https",
                                  "-fsSL", "--max-time", "75", "--max-filesize", str(512 * 1024**2),
                                  "-o", target, url], env={"PATH": os.defpath})
            if run.returncode == 0 and correct(target):
                break
        else:
            raise ValueError(f"could not fetch and verify upstream network {name}")


class Sandbox:
    def __init__(self, work, jobs, cpus, memory_mib, hosted, deadline):
        self.work, self.jobs, self.cpus = work, jobs, cpus
        self.memory_mib, self.deadline = memory_mib, deadline
        # Hosted Ubuntu supports a hard aggregate cgroup scope under the disposable runner.
        self.prefix = []
        if hosted:
            self.prefix = ["sudo", "-n", "systemd-run", "--quiet", "--scope",
                           "-p", f"MemoryMax={memory_mib}M", "-p", "MemorySwapMax=0",
                           "-p", f"CPUQuota={100 * len(cpus)}%", "-p", "TasksMax=256"]
        bwrap = [shutil.which("bwrap") or "/usr/bin/bwrap"]
        self.base = self.prefix + bwrap + ["--die-with-parent", "--new-session",
            "--unshare-ipc", "--unshare-pid", "--unshare-uts", "--unshare-net"]
        self.payload_prefix = []
        if not hosted:
            self.base += ["--unshare-user"]
        self.base += ["--cap-drop", "ALL"]
        if hosted:
            # Root creates namespaces so restricted Ubuntu userns policy does not
            # affect admission. Only this trusted setpriv process can drop to
            # the runner's host UID/GID; libcap bounding and ambient sets are cleared.
            for cap in ("CAP_SETUID", "CAP_SETGID", "CAP_SETPCAP"):
                self.base += ["--cap-add", cap]
            self.payload_prefix = ["/usr/bin/setpriv", "--reuid", str(os.getuid()),
                "--regid", str(os.getgid()), "--clear-groups", "--inh-caps=-all",
                "--ambient-caps=-all", "--bounding-set=-all", "--no-new-privs", "--"]
        self.base += ["--ro-bind", "/usr", "/usr"]
        for name in ("/bin", "/sbin", "/lib", "/lib64"):
            if Path(name).exists():
                self.base += ["--ro-bind", name, name]
        # Implicit bind-mount parents may be mode 0700, owned by the namespace
        # creator (root on Actions). The unprivileged engine must traverse these
        # synthetic directories; only the explicitly bound host objects are visible.
        for directory in ("/etc", "/sys/devices/system", "/sys/fs", "/opt/fastchess/bin"):
            self.base += ["--perms", "0755", "--dir", directory]
        for name in ("/etc/alternatives", "/etc/ld.so.cache", "/sys/devices/system/cpu", "/sys/fs/cgroup"):
            if Path(name).exists():
                self.base += ["--ro-bind", name, name]
        self.base += ["--proc", "/proc", "--dev", "/dev", "--perms", "1777", "--tmpfs", "/dev/shm",
            "--perms", "1777", "--tmpfs", "/tmp", "--perms", "1777", "--dir", "/tmp/home", "--ro-bind", str(LAB), "/lab",
            "--ro-bind", str(work / "nets"), "/nets", "--clearenv",
            "--setenv", "PATH", "/opt/fastchess/bin:/usr/bin:/bin", "--setenv", "HOME", "/tmp/home",
            "--setenv", "LC_ALL", "C", "--setenv", "JOBS", str(jobs),
            "--setenv", "NNUE_DIR", "/nets", "--setenv", "GIT_CONFIG_NOSYSTEM", "1",
            "--setenv", "GIT_CONFIG_GLOBAL", "/dev/null", "--setenv", "GIT_CONFIG_COUNT", "1",
            "--setenv", "GIT_CONFIG_KEY_0", "safe.directory", "--setenv", "GIT_CONFIG_VALUE_0", "*",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--chdir", "/tmp"]

    def run(self, args, mounts, log):
        cmd = self.base + mounts + ["--", *self.payload_prefix, "taskset", "-c", ",".join(map(str, self.cpus)), *args]
        env = {"PATH": os.defpath}
        timeout = max(0, self.deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("worker budget exhausted")
        def cap_process():
            cap = self.memory_mib * 1024**2
            _, hard = resource.getrlimit(resource.RLIMIT_AS)
            cap = cap if hard < 0 else min(cap, hard)
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
            resource.setrlimit(resource.RLIMIT_FSIZE, (GIB, GIB))
        with log.open("wb") as stream:
            with subprocess.Popen(cmd, stdout=stream, stderr=subprocess.STDOUT, env=env,
                                  preexec_fn=cap_process, start_new_session=True) as proc:
                try:
                    if proc.wait(timeout=timeout):
                        raise ValueError(f"{log.stem} failed; inspect {log.name}")
                finally:
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)


def archive(src, dest):
    if not src.is_file() or src.is_symlink() or src.stat().st_size > 16 * 1024**2:
        raise ValueError(f"invalid or oversized artifact {src.name}")
    dest.parent.mkdir(exist_ok=True, parents=True)
    shutil.copyfile(src, dest)


def verify_bench_base(tree):
    text = git(tree, "log", "-100", "--format=%B", "HEAD").decode(errors="replace")
    bench = re.search(r"^\s*bench[ :]+([1-9][0-9]{5,7})\s*$", text, re.I | re.M)
    if not bench:
        raise ValueError("fetch baseline history to verify its published bench")
    return int(bench[1])


def validate_side(sb, name, tree, commit, expected, output):
    area = sb.work / name
    area.mkdir()
    source = sb.work / (name + "-src")
    pin_source(tree, commit, source)
    mounts = ["--ro-bind", str(source), "/source", "--bind", str(area), "/stage"]
    cmds = [["build.sh", "/source", "/stage/build"], ["validate.sh", "/stage/build"],
            ["benchmark.sh", "/stage/build"] + ([str(expected)] if expected else [])]
    builddir = area / "build"
    for script, *params in cmds:
        if script != "build.sh":
            # Candidate processes cannot replace their binary, upstream tests or
            # an earlier stage's manifest while they execute in the private VM.
            frozen = ["stockfish", "upstream-tests", "build.json"]
            if script == "benchmark.sh":
                frozen.append("validation.json")
            for key in frozen:
                file = builddir / key
                if file.is_symlink() or not (file.is_dir() if key == "upstream-tests" else file.is_file()):
                    raise ValueError("unsafe path in sealed engine inputs")
            immutable = [value for key in frozen for value in
                         ("--ro-bind", str(builddir / key), "/stage/build/" + key)]
        else:
            immutable = []
        log = output / (name + "-" + script[:-3] + ".log")
        sb.run(["/lab/scripts/" + script, *params], mounts + immutable, log)
        if builddir.is_symlink() or (builddir / "stockfish").is_symlink():
            raise ValueError("unsafe build artifact path")
        tag = {"build.sh": "build", "validate.sh": "validation", "benchmark.sh": "benchmark"}[script]
        manifest = builddir / (tag + ".json")
        archive(manifest, output / name / manifest.name)
    rows = {tag: json.loads((output / name / (tag + ".json")).read_text())
            for tag in ("build", "validation", "benchmark")}
    digest = sha256(builddir / "stockfish")
    if not all(row["commit"] == commit and row["binary_sha256"] == digest for row in rows.values()):
        raise ValueError("mismatched commit or binary in worker results")
    if any(rows["validation"].get(key) != "passed" for key in ("perft", "uci", "reprosearch")):
        raise ValueError("incomplete Stage A evidence")
    runs = rows["benchmark"]["runs"]
    signature = rows["benchmark"]["signature"]
    if len(runs) != 2 or not all(r["nodes"] == signature and r["nps"] > 0 for r in runs):
        raise ValueError("incomplete canonical bench repeats")
    return builddir, rows


def execute(args, report, output, work):
    cfg = json.loads((LAB / "config/testing.yaml").read_text())
    match_cfg = cfg["local_match"].copy()
    hosted = os.getenv("GITHUB_ACTIONS") == "true" and os.getenv("RUNNER_ENVIRONMENT") == "github-hosted"
    if os.getenv("GITHUB_ACTIONS") == "true" and not hosted:
        raise ValueError("continuous Actions jobs require disposable GitHub-hosted runners")
    profile = cfg["hardware_profiles"][cfg["default_hardware_profile"]].copy()
    if hosted and os.getuid() == 0:
        raise ValueError("start the trusted worker as the ordinary hosted runner account")
    if hosted:
        # Small hosted VMs expose vCPUs. Both the reserved and assigned slots refer to vCPUs.
        profile.update(count_vcpus=True, reserve_cpus=1, reserve_mib=1536)
        match_cfg.update(reserve_cpus=1, reserve_mib=1536, max_worker_cpus=2, max_worker_mib=5120)
    limits = capacity(match_cfg, profile)
    memory = min(limits["usable_mib"], 5120)
    cpus = limits["cpu_ids"][:2]
    jobs = min(len(cpus), memory // 1200, 2)
    if jobs < 1 or memory < 2200:
        raise ValueError("insufficient worker capacity after OS reserves")
    report["compute"] = dict(capacity=limits, jobs=jobs, admitted_cpu_ids=cpus,
                             memory_ceiling_mib=memory, deadline_seconds=WALL_LIMIT)
    sb = Sandbox(work, jobs, cpus, memory, hosted, time.monotonic() + WALL_LIMIT)
    base, new = head(args.base), head(args.candidate)
    report.update(baseline_sha=base, candidate_sha=new,
                  source_paths=check_source(args.candidate, base, new, args.rehearsal))
    fc = args.fastchess_dir
    if not (args.rehearsal or args.build_only) and (fc is None or head(fc) != FASTCHESS or not os.access(fc / "fastchess", os.X_OK)
            or git(fc, "diff", "--name-only", "HEAD") or sha256(fc / "app/tests/data/openings.epd") != BOOK):
        raise ValueError("the prebuilt pinned Fastchess checkout and matching opening book are required")
    nets = work / "nets"
    nets.mkdir()
    for tree, ref in ((args.base, base), (args.candidate, new)):
        networks(tree, ref, nets, args.nnue_dir)
    ref = verify_bench_base(args.base)
    base_dir, baseline = validate_side(sb, "baseline", args.base, base, ref, output)
    new_dir, candidate = validate_side(sb, "candidate", args.candidate, new, ref if args.rehearsal else None, output)
    for field in COMPARABLE:
        if baseline["build"][field] != candidate["build"][field]:
            raise ValueError(f"unmatched build field {field}")
    def median_nps(rows):
        return statistics.median(sample["nps"] for sample in rows["benchmark"]["runs"])
    ratio = median_nps(candidate) / median_nps(baseline)
    report["benchmark"] = dict(baseline=ref, candidate=candidate["benchmark"]["signature"], nps_ratio=ratio,
                               hold_below=NPS_FLOOR)
    if args.rehearsal:
        report.update(status="validated_rehearsal", decision="validated_rehearsal",
                      reason="Verified exact standalone comment insertion and matching published bench on both builds.")
        return
    if args.build_only:
        report.update(status="validated_build", decision="build_only",
                      reason="Independent correctness and benchmark replication. No match campaign allocated.")
        return
    if ratio < NPS_FLOOR:
        report.update(status="hold_performance", decision="hold_performance",
                      reason="Candidate median benchmark speed fell below 95% of baseline; requires independent review.")
        return
    # CPU and RAM were already reserved on admission. Project just this assigned
    # slice into the no-network sandbox; local_match still enforces per-game limits.
    match_cfg.update(reserve_cpus=0, reserve_mib=0, max_worker_cpus=len(cpus),
                     max_worker_mib=memory, timeout_seconds=min(600, max(1, int(sb.deadline - time.monotonic()))))
    cfg["local_match"] = match_cfg
    cfg["hardware_profiles"][cfg["default_hardware_profile"]] = dict(reserve_cpus=0, reserve_mib=0)
    projected = work / "job-budget.json"
    projected.write_text(json.dumps(cfg))
    stage = work / "screen"
    stage.mkdir()
    book = fc / "app/tests/data/openings.epd"
    mounts = ["--ro-bind", str(base_dir), "/baseline", "--ro-bind", str(new_dir), "/candidate",
              "--ro-bind", str(fc / "fastchess"), "/opt/fastchess/bin/fastchess", "--ro-bind", str(book), "/book.epd",
              "--ro-bind", str(projected), "/lab/config/testing.yaml", "--bind", str(stage), "/screen"]
    sb.run(["/lab/scripts/local-match.sh", "/baseline", "/candidate", "/book.epd", "/screen/results",
            "--pairs", str(SCREEN_PAIRS), "--threads", "1"], mounts, output / "local-match.log")
    files = stage / "results"
    if files.is_symlink():
        raise ValueError("unsafe match output path")
    for name in ("match.json", "games.pgn", "fastchess.log"):
        archive(files / name, output / "match" / name)
    archive(book, output / "match/openings.epd")
    match = json.loads((files / "match.json").read_text())
    positions = {" ".join(line.split()[:4]) for line in book.read_text().splitlines() if line.strip()}
    observed = results(files / "games.pgn", SCREEN_PAIRS, positions)
    if (match["baseline"] != baseline["build"] or match["candidate"] != candidate["build"] or
            match["status"] != "screened" or match["book_sha256"] != BOOK or
            match["pgn_sha256"] != sha256(files / "games.pgn") or
            any(match.get(key) != value for key, value in observed.items())):
        raise ValueError("recorded match evidence disagrees with assigned engines or PGN")
    report.update(status="screened", decision=observed["decision"], games=2 * SCREEN_PAIRS,
                  fastchess_sha=FASTCHESS, reason="Fixed-budget Stage C screen. Public submission requires separate review.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "candidate", "output"):
        p.add_argument(name, type=lambda x: Path(x).resolve())
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--rehearsal", action="store_true")
    mode.add_argument("--build-only", action="store_true", help="replicate build and bench without allocating games")
    p.add_argument("--fastchess-dir", type=lambda x: Path(x).resolve())
    p.add_argument("--nnue-dir", type=lambda x: Path(x).resolve())
    args = p.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    now = time.monotonic()
    initial = resource.getrusage(resource.RUSAGE_CHILDREN)
    report = dict(schema_version=1, baseline_sha=None, candidate_sha=None, source_paths=[],
                  status="invalid", decision="invalid", rehearsal=args.rehearsal, games=0,
                  workflow={k: os.getenv("GITHUB_" + k.upper()) for k in
                            ("repository", "run_id", "run_attempt", "sha", "workflow_ref")})
    try:
        with tempfile.TemporaryDirectory(prefix="stockfish-validation-") as tmp:
            execute(args, report, out, Path(tmp))
    except (OSError, KeyError, TypeError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
        report["reason"] = str(exc)
    current = resource.getrusage(resource.RUSAGE_CHILDREN)
    report.setdefault("compute", {}).update(
        wall_seconds=time.monotonic() - now,
        child_cpu_seconds=(current.ru_utime - initial.ru_utime + current.ru_stime - initial.ru_stime))
    for log in out.glob("*.log"):
        if log.stat().st_size > 2 * 1024**2:
            with log.open("r+b") as stream:
                stream.truncate(2 * 1024**2)
                stream.seek(0, os.SEEK_END)
                stream.write(b"\n[worker log truncated at 2 MiB]\n")
    report["evidence_sha256"] = {p.relative_to(out).as_posix(): sha256(p) for p in sorted(out.rglob("*"))
                                  if p.is_file() and not p.is_symlink()}
    (out / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"{report['status']}: {report.get('reason', '')}")
    return int(report["status"] == "invalid")


if __name__ == "__main__":
    sys.exit(main())
