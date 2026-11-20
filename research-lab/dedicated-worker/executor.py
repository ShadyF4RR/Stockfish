#!/usr/bin/env python3
"""Execute one already leased, fixed-budget Stockfish screen on Linux."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import signal
import statistics
import subprocess
import sys
import tempfile
import time

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "continuous"))
import cloud_candidate
import validate_candidate as validation
from local_match import COMPARABLE, opening_key, results, sha256
from worker_resources import allocation, capacity, worker_lock

CALIBRATION_PAIRS = 2
PREFIX_FIELDS = ("experiment", "baseline", "candidate")
CLOCKS = {"4+0.04", "12+0.12"}


def read_request(path):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate request field")
        return result

    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("expected a small regular request file")
    raw = path.read_bytes()
    req = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(req, dict) or set(req) != {*PREFIX_FIELDS, "pairs", "seed", "time_control"}:
        raise ValueError("unexpected request fields")
    cloud_candidate.checked({k: req[k] for k in PREFIX_FIELDS})
    if (type(req["pairs"]) is not int or not 1 <= req["pairs"] <= 50
            or type(req["seed"]) is not int or not 1 <= req["seed"] < 2**32
            or not isinstance(req["time_control"], str) or req["time_control"] not in CLOCKS):
        raise ValueError("unsupported pair count, seed or time control")
    return req, hashlib.sha256(raw).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def memory_max(root=Path("/sys/fs/cgroup"), membership=Path("/proc/self/cgroup")):
    group_name = next((line[3:] for line in membership.read_text().splitlines()
                       if line.startswith("0::")), None)
    if group_name is None:
        raise ValueError("cgroup v2 membership is required")
    node = (root / group_name.lstrip("/")).resolve(strict=True)
    finite = []
    while node == root or root in node.parents:
        limit = node / "memory.max"
        if limit.is_file() and (value := limit.read_text().strip()) != "max":
            finite.append(int(value) // 1024**2)
        if node == root:
            return min(finite, default=None)
        node = node.parent
    raise ValueError("unresolvable service cgroup")


def admit(worker, cfg):
    if os.getuid() == 0:
        raise ValueError("the worker service must run under a dedicated non-root account")
    hosted = (worker == "cloud" and os.getenv("GITHUB_ACTIONS") == "true"
              and os.getenv("RUNNER_ENVIRONMENT") == "github-hosted")
    kernel = platform.release()
    vm_mib = int(re.search(r"^MemTotal:\s+(\d+) kB$", Path("/proc/meminfo").read_text(), re.M)[1]) // 1024
    cap = memory_max()
    if worker == "pc":
        if "microsoft" not in kernel.lower() or "wsl2" not in kernel.lower():
            raise ValueError("PC execution requires the enrolled WSL2 worker")
        if vm_mib > 12288 or cap is None or cap > 12288:
            raise ValueError("configure WSL2 memory <=12 GiB and a finite service MemoryMax <=12 GiB")
        cfg.update(reserve_cpus=0, reserve_mib=768, max_worker_cpus=8,
                   max_worker_mib=min(10240, vm_mib - 2048))
    elif hosted:
        cfg.update(reserve_cpus=1, reserve_mib=1536, max_worker_cpus=2, max_worker_mib=5120)
    elif cap is None:
        raise ValueError("cloud execution requires a memory-capped service or disposable hosted runner")
    profile = dict(count_vcpus=True, reserve_cpus=cfg["reserve_cpus"], reserve_mib=cfg["reserve_mib"])
    limits = capacity(cfg, profile)
    jobs = min(len(limits["cpu_ids"]), limits["usable_mib"] // 1200, 4)
    if jobs < 1 or limits["usable_mib"] < 2200:
        raise ValueError("insufficient capacity after worker admission")
    return limits, jobs, hosted, dict(kernel=kernel, vm_total_mib=vm_mib, cgroup_memory_max_mib=cap)


def layouts(limits, cfg, binary_size, primary_pairs):
    plans = []
    for threads in (1, 2, 4):
        try:
            maximum = allocation(limits, cfg, binary_size, threads)["concurrency"]
        except ValueError:
            continue
        # All slots measured here must actually fit both the calibration and primary batch.
        concurrent = min(maximum, 2 * CALIBRATION_PAIRS, 2 * primary_pairs)
        counts = sorted({1, concurrent}) if threads == 1 else [concurrent]
        plans.extend(dict(threads=threads, concurrency=c) for c in counts)
    if not plans:
        raise ValueError("no admitted calibration layout")
    return plans


def books(source, calibration, primary, seed, pairs):
    rows = [row.strip() for row in source.read_text().splitlines() if row.strip()]
    keys = [opening_key(row) for row in rows]
    if len(set(keys)) != len(rows) or len(rows) < pairs + CALIBRATION_PAIRS:
        raise ValueError("book lacks distinct calibration and primary opening pairs")
    ordered = sorted(rows, key=lambda row: hashlib.sha256(f"{seed}\0{row}".encode()).digest())
    calibration.write_text("\n".join(ordered[:CALIBRATION_PAIRS]) + "\n")
    primary.write_text("\n".join(ordered[CALIBRATION_PAIRS:CALIBRATION_PAIRS+pairs]) + "\n")


def counted_games(path):
    if not path.is_file():
        return 0
    return len(re.findall(r'^\[Result "(?:1-0|0-1|1/2-1/2)"\]$', path.read_text(), re.M))


def run_batch(sandbox, args, config, source_book, req, pairs, layout, base, candidate, target, tag):
    target.mkdir(parents=True, exist_ok=True)
    stage = sandbox.work / tag
    stage.mkdir()
    projected = stage / "testing.json"
    cfg = config["local_match"]
    cfg["checkpoint_games"] = tag.startswith("primary-")
    cfg.update(max_worker_cpus=len(sandbox.cpus), max_worker_mib=sandbox.memory_mib,
               reserve_cpus=0, reserve_mib=0, time_control=req["time_control"],
               timeout_seconds=max(1, int(sandbox.deadline - time.monotonic()) - 2))
    config["hardware_profiles"][config["default_hardware_profile"]] = dict(
        reserve_cpus=0, reserve_mib=0, count_vcpus=True)
    projected.write_text(json.dumps(config))
    # Only the current batch's spool is writable inside its match namespace.
    # A WSL restart must not throw away games already written by Fastchess.
    private = args.output.parent / "spool" / tag
    private.mkdir(parents=True, exist_ok=True)
    files = private / "results"
    if files.is_symlink():
        raise ValueError("unsafe match spool")
    mounts = ["--ro-bind", str(base), "/baseline", "--ro-bind", str(candidate), "/candidate",
              "--ro-bind", str(args.fastchess / "fastchess"), "/opt/fastchess/bin/fastchess",
              "--ro-bind", str(source_book), "/book.epd", "--ro-bind", str(projected), "/lab/config/testing.yaml",
              "--bind", str(private), "/screen"]
    validation.archive(source_book, target / "openings.epd")
    try:
        prior = files / "match.json"
        done = prior.is_file() and json.loads(prior.read_text()).get("status") == "screened"
        if not files.exists() or (cfg["checkpoint_games"] and not done):
            sandbox.run(["/lab/scripts/local-match.sh", "/baseline", "/candidate", "/book.epd", "/screen/results",
                     "--pairs", str(pairs), "--seed", str(req["seed"]), "--threads", str(layout["threads"]),
                     "--concurrency", str(layout["concurrency"])], mounts, target / "worker.log")
    finally:
        files = private / "results"
        if files.is_symlink():
            raise ValueError("unsafe match output path")
        for name in ("match.json", "games.pgn", "fastchess.log"):
            if (files / name).exists():
                validation.archive(files / name, target / name)
    expected_builds = [json.loads((tree / "build.json").read_text()) for tree in (base, candidate)]
    return verify_batch(target, source_book, req, pairs, layout, expected_builds, sha256(args.fastchess / "fastchess"))


def run_primary(sandbox, args, config, book, req, layout, base, candidate, out):
    # Each batch owns disjoint registered openings and a fixed spool. Re-entry
    # verifies completed batches. An ambiguous started batch is never replayed.
    rows = book.read_text().splitlines()
    target = out / "match"
    target.mkdir(exist_ok=True)
    validation.archive(book, target / "openings.epd")
    records = []
    games = []
    offset = 0
    batch_size = 5
    for first in range(0, len(rows), batch_size):
        subset = rows[first:first + batch_size]
        tag = f"primary-{first:02d}"
        batch_book = sandbox.work / (tag + ".epd")
        batch_book.write_text("\n".join(subset) + "\n")
        batch = out / "batches" / tag
        record = run_batch(sandbox, args, config, batch_book, req, len(subset), layout,
                           base, candidate, batch, tag)
        text = (batch / "games.pgn").read_text()
        games.append(re.sub(r'^\[Round "(\d+)"\]$',
                            lambda m: f'[Round "{offset + int(m[1])}"]', text, flags=re.M).strip())
        offset += len(subset)
        records.append(record)
        # Only complete and checked opening pairs enter the aggregate.
        combined = target / "games.pgn"
        temp = target / "games.tmp"
        temp.write_text("\n\n".join(games) + "\n")
        temp.replace(combined)
    observed = results(combined, len(rows), rows)
    record = dict(records[0])
    record.pop("command")
    record.update(observed, settings=dict(record["settings"], pairs=len(rows)),
                  batches=len(records), book_sha256=sha256(book), pgn_sha256=sha256(combined),
                  compute={key:sum(r["compute"][key] for r in records) for key in record["compute"]})
    write_json(target / "match.json", record)
    return record


def verify_batch(target, source_book, req, pairs, layout, expected_builds, manager_digest):
    record = json.loads((target / "match.json").read_text())
    pgn = target / "games.pgn"
    rows = source_book.read_text().splitlines()
    observed = results(pgn, pairs, rows)
    clocks = re.findall(r'^\[TimeControl "([^"]+)"\]$', pgn.read_text(), re.M)
    if clocks != [req["time_control"]] * (2*pairs):
        raise ValueError("PGN time control differs from the request")
    # Fastchess may clear an unavailable en-passant target. The move counters and
    # other position fields still identify the exact prescribed starting point.
    def position(row):
        return (opening_key(row), *row.split()[4:6])
    positions = {position(row) for row in rows}
    starts = re.findall(r'^\[FEN "([^"]+)"\]$', pgn.read_text(), re.M)
    if len(starts) != 2*pairs or any(position(fen) not in positions for fen in starts):
        raise ValueError("PGN start position differs from the selected book")
    expected = {"status": "screened", "book_sha256": sha256(source_book),
                "pgn_sha256": sha256(pgn), "fastchess_sha256": manager_digest,
                "baseline": expected_builds[0], "candidate": expected_builds[1], **observed}
    for key, value in expected.items():
        if record.get(key) != value:
            raise ValueError(f"unexpected {key} in {target.name}")
    settings = dict(layout, pairs=pairs, seed=req["seed"], time_control=req["time_control"], hash_mib=16)
    if any(record["settings"].get(k) != v for k, v in settings.items()):
        raise ValueError("match settings differ from admission")
    return record


def fastchess_git(tree, *args):
    # The installer owns this immutable checkout as root. Trust this exact supplied
    # path for Git's ownership check without altering the service account's config.
    return subprocess.check_output(["git", "-c", f"safe.directory={tree}", "-C", tree, *args],
                                   stderr=subprocess.DEVNULL, timeout=30).decode().strip()


def execute(args, out, work, report, req, deadline):
    config = json.loads((LAB / "config/testing.yaml").read_text())
    cfg = config["local_match"]
    cfg["hash_mib"] = 16
    limits, jobs, hosted, host = admit(args.worker, cfg)
    report["compute"] = dict(host=host, capacity=limits, jobs=jobs, max_minutes=args.max_minutes)
    fastchess = args.fastchess
    source_book = fastchess / "app/tests/data/openings.epd"
    if (fastchess_git(fastchess, "rev-parse", "HEAD^{commit}") != validation.FASTCHESS
            or fastchess_git(fastchess, "diff", "--name-only", "HEAD")
            or not os.access(fastchess / "fastchess", os.X_OK) or sha256(source_book) != validation.BOOK):
        raise ValueError("expected an exact, clean Fastchess checkout, built executable and pinned source book")
    report.update(fastchess_sha=validation.FASTCHESS, fastchess_sha256=sha256(fastchess / "fastchess"),
                  source_book_sha256=validation.BOOK)
    source = work / "sources"
    pinned = cloud_candidate.stage({k: req[k] for k in PREFIX_FIELDS}, source)
    report["source_paths"] = pinned["source_paths"]
    sandbox = validation.Sandbox(work, jobs, limits["cpu_ids"], limits["usable_mib"], hosted, deadline)
    nets = work / "nets"
    nets.mkdir()
    base = source / "baseline"
    new = source / "candidate"
    for tree, commit in ((base, req["baseline"]), (new, req["candidate"])):
        validation.networks(tree, commit, nets, args.nnue_dir)
    signature = validation.verify_bench_base(base)
    base_dir, baseline = validation.validate_side(sandbox, "baseline", base, req["baseline"], signature, out)
    new_dir, candidate = validation.validate_side(sandbox, "candidate", new, req["candidate"], None, out)
    if any(baseline["build"][k] != candidate["build"][k] for k in COMPARABLE):
        raise ValueError("unmatched build environments")
    def speed(rows):
        return statistics.median(r["nps"] for r in rows["benchmark"]["runs"])
    ratio = speed(candidate) / speed(baseline)
    report["benchmark"] = dict(baseline=signature, candidate=candidate["benchmark"]["signature"], nps_ratio=ratio)
    if ratio < validation.NPS_FLOOR and not (out / "calibration.json").exists():
        report.update(status="hold_performance", decision="hold_performance",
                      reason="Candidate median benchmark speed below the registered 95% floor.")
        return
    calibration_book, primary_book = work / "calibration.epd", work / "primary.epd"
    books(source_book, calibration_book, primary_book, req["seed"], req["pairs"])
    size = max((d / "stockfish").stat().st_size for d in (base_dir, new_dir))
    plans = layouts(limits, cfg, size, req["pairs"])
    if args.worker == "pc" and (len(plans) < 2 or not any(p["threads"] > 1 for p in plans)):
        raise ValueError("dedicated PC requires a feasible multi-thread alternative")
    calibration = dict(status="running", valid=False, pairs_per_layout=CALIBRATION_PAIRS, games=0,
                       baseline_selfplay=True, book_sha256=sha256(calibration_book), planned_layouts=plans, layouts=[])
    cal_file = out / "calibration.json"
    write_json(cal_file, calibration)
    order_hash = None
    for plan in plans:
        name = f"t{plan['threads']}-c{plan['concurrency']}"
        relative = Path("calibration") / name
        match = run_batch(sandbox, args, config, calibration_book, req, CALIBRATION_PAIRS, plan,
                          base_dir, base_dir, out / relative, name)
        if order_hash is not None and match["opening_order_sha256"] != order_hash:
            raise ValueError("calibration opening order differed between layouts")
        order_hash = match["opening_order_sha256"]
        elapsed = match["compute"]["wall_seconds"]
        if elapsed <= 0:
            raise ValueError("missing positive calibration duration")
        calibration["layouts"].append(dict(**plan, valid=True, games=match["games"], wall_seconds=elapsed,
             games_per_hour=3600*match["games"]/elapsed, evidence=str(relative/"match.json"), pgn=str(relative/"games.pgn")))
        calibration["games"] += match["games"]
        write_json(cal_file, calibration)
    best = max(calibration["layouts"], key=lambda p: p["games_per_hour"])
    selected = {key: best[key] for key in ("threads", "concurrency")}
    calibration.update(status="complete", valid=True, selected=selected, opening_order_sha256=order_hash)
    write_json(cal_file, calibration)
    report["selected_layout"] = selected
    match = run_primary(sandbox, args, config, primary_book, req, selected, base_dir, new_dir, out)
    report.update(status="screened", decision=match["decision"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--fastchess", type=lambda s: Path(s).resolve(), required=True)
    p.add_argument("--worker", choices=["pc", "cloud"], default="pc")
    p.add_argument("--nnue-dir", type=lambda s: Path(s).resolve())
    p.add_argument("--max-minutes", type=int, required=True, help="wall minutes reserved in this lease (1–120)")
    args = p.parse_args()
    if not 1 <= args.max_minutes <= 120:
        p.error("max-minutes must be between 1 and 120")
    out = args.output.resolve()
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (out / "result.json").exists():
        p.error("campaign already settled locally")
    start = time.monotonic()
    report = dict(schema_version=1, status="invalid", decision="invalid", rehearsal=False, worker=args.worker)
    cpu_start = resource.getrusage(resource.RUSAGE_CHILDREN)
    def expired(signum, frame):
        raise TimeoutError("leased worker wall time exhausted")
    signal.signal(signal.SIGALRM, expired)
    # Leave the supervisor time to collect evidence before its hard lease timeout.
    work_seconds = args.max_minutes * 60 - 12
    signal.alarm(work_seconds)
    try:
        req, digest = read_request(args.request)
        report.update(experiment=req["experiment"], baseline_sha=req["baseline"], candidate_sha=req["candidate"],
                      request_sha256=digest, pairs=req["pairs"], seed=req["seed"], time_control=req["time_control"])
        with worker_lock(), tempfile.TemporaryDirectory(prefix="stockfish-dedicated-") as tmp:
            execute(args, out, Path(tmp), report, req, start + work_seconds)
    except (OSError, KeyError, TypeError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
        report["reason"] = str(exc)
    finally:
        signal.alarm(0)
    end = resource.getrusage(resource.RUSAGE_CHILDREN)
    report.setdefault("compute", {}).update(wall_seconds=time.monotonic()-start,
        child_cpu_seconds=end.ru_utime+end.ru_stime-cpu_start.ru_utime-cpu_start.ru_stime)
    report["games"] = sum(counted_games(p) for p in out.glob("batches/*/games.pgn"))
    report["calibration_games"] = sum(counted_games(p) for p in out.glob("calibration/*/games.pgn"))
    report["actual_games"] = report["games"] + report["calibration_games"]
    for log in out.rglob("*.log"):
        if log.stat().st_size > 1024**2:
            with log.open("r+b") as stream:
                stream.truncate(1024**2)
                stream.seek(0, os.SEEK_END)
                stream.write(b"\n[worker log truncated at 1 MiB]\n")
    report["evidence_sha256"] = {str(path.relative_to(out)): sha256(path) for path in sorted(out.rglob("*"))
                                 if path.is_file() and not path.is_symlink()}
    write_json(out / "result.json", report)
    print(report["status"], report.get("reason", ""))
    return int(report["status"] == "invalid")


if __name__ == "__main__":
    sys.exit(main())
