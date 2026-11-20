"""Run a bounded, fixed-sample opening-paired Stage C screen."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import signal
import subprocess
import sys
import time

from worker_resources import allocation, apply_limits, capacity, worker_lock

CONFIG = Path(__file__).resolve().parents[1] / "config/testing.yaml"
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
COMPARABLE = ("arch", "compiler", "compiler_binary_sha256", "hardware", "build_flags")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_build(directory):
    binary = (directory / "stockfish").resolve(strict=True)
    manifest = json.loads((directory / "build.json").read_text())
    if not re.fullmatch(r"[0-9a-f]{40}", manifest["commit"]):
        raise ValueError(f"{directory}: missing full commit hash")
    if not os.access(binary, os.X_OK) or sha256(binary) != manifest["binary_sha256"]:
        raise ValueError(f"{directory}: missing executable or binary checksum mismatch")
    for key in (*COMPARABLE, "networks"):
        if key not in manifest:
            raise ValueError(f"{directory}: missing manifest field {key}")
    return binary, manifest


def elo(score):
    # A null endpoint represents an unbounded interval in that direction.
    return 400 * math.log10(score / (1 - score)) if 0 < score < 1 else None


def results(pgn, pairs, book_positions):
    rounds = {}
    for block in pgn.read_text().split("\n\n"):
        if not block.lstrip().startswith("["):
            continue
        tags = dict(re.findall(r'^\[(\w+) "([^"\n]*)"\]$', block.strip(), re.M))
        if tags.get("Termination") != "normal":
            raise ValueError(f"invalid game termination: {tags.get('Termination')}")
        white = tags["White"]
        if {white, tags["Black"]} != {"candidate", "baseline"}:
            raise ValueError("unexpected engine names")
        score = {"1-0": 2, "1/2-1/2": 1, "0-1": 0}[tags["Result"]]
        position = tags.get("FEN", START)
        rounds.setdefault(int(tags["Round"]), []).append(
            (white, position, score if white == "candidate" else 2 - score)
        )

    if set(rounds) != set(range(1, pairs + 1)):
        raise ValueError("incomplete or unexpected rounds")
    outcomes = [0, 0, 0]
    pentanomial = [0] * 5
    used = set()
    suite = []
    for _, games in sorted(rounds.items()):
        if len(games) != 2:
            raise ValueError("each round must have two games")
        first, second = games
        position = " ".join(first[1].split()[:4])
        if first[0] == second[0] or first[1] != second[1]:
            raise ValueError("pair must replay the same opening with colors reversed")
        if position in used or position not in book_positions:
            raise ValueError("repeated or unknown opening across rounds")
        used.add(position)
        suite.append(first[1])
        outcomes[first[2]] += 1
        outcomes[second[2]] += 1
        pentanomial[first[2] + second[2]] += 1

    losses, draws, wins = outcomes
    mean = (wins + draws / 2) / (2 * pairs)
    # Hoeffding's two-sided bound on the independent [0,1] opening-pair means.
    # This remains conservative even if every observed pair scores identically.
    margin = math.sqrt(math.log(2 / 0.05) / (2 * pairs))
    low, high = max(0, mean - margin), min(1, mean + margin)
    decision = "inconclusive"
    if high < 0.5:
        decision = "reject"
    elif low > 0.5:
        decision = "escalate"
    return dict(games=2 * pairs, wins=wins, draws=draws, losses=losses, score=mean,
                pentanomial=pentanomial, elo=elo(mean), elo_ci95=[elo(low), elo(high)],
                score_ci95=[low, high], interval_method="fixed-sample paired Hoeffding",
                opening_order_sha256=hashlib.sha256("\n".join(suite).encode()).hexdigest(), decision=decision)


def run(args, cfg, plan, limits, out, timeout):
    pairs = args.pairs
    if not 1 <= pairs <= cfg["max_pairs"] or not 1 <= args.seed < 2**32:
        raise ValueError("pairs exceed the configured limit or seed outside [1, 2^32)")
    if timeout <= 0 or cfg["hash_mib"] <= 0:
        raise ValueError("time and hash budgets must be positive")
    deadline = time.monotonic() + timeout
    fastchess = Path(shutil.which("fastchess") or "").resolve()
    if not fastchess.is_file():
        raise ValueError("fastchess executable required on PATH")
    baseline_path, baseline = read_build(args.baseline)
    candidate_path, candidate = read_build(args.candidate)
    for key in COMPARABLE:
        if baseline[key] != candidate[key]:
            raise ValueError(f"build manifests disagree on {key}")
    book = args.book.resolve(strict=True)
    book_hash = sha256(book)
    positions = [" ".join(line.split()[:4]) for line in book.read_text().splitlines() if line.strip()]
    if len(set(positions)) != len(positions) or len(positions) < pairs:
        raise ValueError("book needs distinct positions and at least one opening per pair")

    out.mkdir(parents=True, exist_ok=False)
    command = [str(fastchess), "-engine", f"cmd={candidate_path}", "name=candidate",
               "-engine", f"cmd={baseline_path}", "name=baseline",
               "-each", "proto=uci", f"tc={cfg['time_control']}", f"option.Threads={plan['threads']}",
               "option.Ponder=false",
               f"option.Hash={cfg['hash_mib']}", "-openings", f"file={book}", "format=epd",
               "order=random", "-srand", str(args.seed), "-rounds", str(pairs), "-games", "2",
               "-repeat", "-concurrency", str(plan["concurrency"]), "-report", "penta=true",
               "-pgnout", "file=games.pgn", "min=false"]
    record = dict(schema_version=1, stage="C", status="running", baseline=baseline,
                  candidate=candidate, settings=dict(cfg, pairs=pairs, seed=args.seed, threads=plan["threads"],
                                concurrency=plan["concurrency"], timeout_seconds=timeout),
                  hardware_profile=args.profile, capacity=limits, allocation=plan,
                  runner=platform.uname()._asdict(), command=command, book_sha256=book_hash,
                  fastchess_sha256=sha256(fastchess), networks_changed=baseline["networks"] != candidate["networks"])
    record_path = out / "match.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    try:
        with (out / "fastchess.log").open("wb") as log:
            # Start in a fresh directory with no inherited GitHub or API credentials.
            with subprocess.Popen(command, cwd=out, stdout=log, stderr=subprocess.STDOUT,
                                  env={"PATH": os.defpath, "LC_ALL": "C", "HOME": str(out),
                                       "MALLOC_ARENA_MAX": "2"},
                                  preexec_fn=lambda: apply_limits(plan), start_new_session=True) as proc:
                try:
                    if proc.wait(timeout=max(0, deadline - time.monotonic())) != 0:
                        raise ValueError("Fastchess failed; see fastchess.log")
                finally:
                    # Include engine children if the match manager exits unexpectedly.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        if sha256(book) != book_hash:
            raise ValueError("opening book changed during the run")
        for binary, build in ((candidate_path, candidate), (baseline_path, baseline)):
            if sha256(binary) != build["binary_sha256"]:
                raise ValueError("engine binary changed during the run")
        pgn = out / "games.pgn"
        record.update(results(pgn, pairs, set(positions)), status="screened", pgn_sha256=sha256(pgn))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        record.update(status="invalid", decision="invalid", reason=f"{type(exc).__name__}: {exc}")
    finally:
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        record["compute"] = dict(wall_seconds=time.monotonic() - start,
                                 child_cpu_seconds=(usage.ru_utime - usage_before.ru_utime
                                                    + usage.ru_stime - usage_before.ru_stime))
        temporary = out / "match.json.tmp"
        temporary.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
        temporary.replace(record_path)
    print(f"{record['status']}: {record.get('reason', record['decision'])} ({record_path})")
    return record


def execute(args, config):
    cfg = config["local_match"]
    hardware = config["hardware_profiles"][args.profile]
    limits = capacity(cfg, hardware)
    size = max((directory / "stockfish").stat().st_size for directory in (args.candidate, args.baseline))
    out = args.output.resolve()
    if not args.calibrate:
        args.pairs = cfg["default_pairs"] if args.pairs is None else args.pairs
        games = cfg["concurrency"] if args.concurrency is None else args.concurrency
        if games == "auto":
            games = min(allocation(limits, cfg, size, args.threads)["concurrency"], args.pairs * 2)
        plan = allocation(limits, cfg, size, args.threads, games)
        rec = run(args, cfg, plan, limits, out, cfg["timeout_seconds"])
        return 0 if rec["status"] == "screened" else 1

    if args.concurrency is not None or args.threads != 1:
        raise ValueError("calibration determines both concurrency and threads")
    single = allocation(limits, cfg, size, 1)
    dual = allocation(limits, cfg, size, 2)
    if single["concurrency"] < 2:
        raise ValueError("calibration needs capacity for at least two simultaneous games")
    # Feed each allocation at least one batch; identical openings, colors and TC.
    minimum_pairs = (single["concurrency"] + 1) // 2
    args.pairs = max(minimum_pairs, cfg["calibration_pairs"]) if args.pairs is None else args.pairs
    if args.pairs < minimum_pairs or 2 * args.pairs > cfg["max_pairs"]:
        raise ValueError("calibration must fill the worker and respect the total pair limit")
    out.mkdir(parents=True, exist_ok=False)
    deadline = time.monotonic() + min(cfg["timeout_seconds"], cfg["calibration_timeout_seconds"])
    report = dict(status="incomplete", profile=args.profile, measurements=[])
    path = out / "throughput.json"
    expected = None
    try:
        for target in (single, dual):
            limits = capacity(cfg, hardware)
            plan = allocation(limits, cfg, size, target["threads"], target["concurrency"])
            label = f"{plan['concurrency']}games-{plan['threads']}threads"
            record = run(args, cfg, plan, limits, out / label, int(deadline - time.monotonic()))
            if record["status"] != "screened":
                raise ValueError(f"{label}: {record.get('reason', 'invalid games')}")
            signature = (record["opening_order_sha256"], record["book_sha256"], record["fastchess_sha256"],
                         record["candidate"]["binary_sha256"], record["baseline"]["binary_sha256"])
            if expected is not None and signature != expected:
                raise ValueError("input binaries, runner, or openings changed between allocations")
            expected = signature
            seconds = record["compute"]["wall_seconds"]
            report["measurements"].append(dict(games=record["games"], concurrency=plan["concurrency"],
                threads=plan["threads"], games_per_hour=record["games"] * 3600 / seconds,
                wall_seconds=seconds, evidence=f"{label}/match.json"))
        report.update(status="complete", fastest_observed=max(report["measurements"],
                      key=lambda entry: entry["games_per_hour"]))
    except (ValueError, OSError, KeyError, KeyboardInterrupt) as exc:
        report["reason"] = str(exc)
    finally:
        path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['status']}: {path}")
    return 0 if report["status"] == "complete" else 1


def main():
    def terminate(signum, frame):
        raise KeyboardInterrupt("terminated")

    signal.signal(signal.SIGTERM, terminate)
    config = json.loads(CONFIG.read_text())
    cfg = config["local_match"]
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Needs Linux cgroup v2, Fastchess (see .github/workflows/games.yml for the pinned version), "
        "build.sh output dirs and a deduplicated EPD/FEN book. Output must be a new directory. "
        "A completed screen exits 0 regardless of decision. Invalid/incomplete results exit 1; "
        "input errors exit 2. Results never authorize promotion."))
    for arg in ("baseline", "candidate", "book", "output"):
        parser.add_argument(arg, type=Path)
    parser.add_argument("--pairs", type=int, default=None,
                        help=f"fixed color-reversed pairs (default {cfg['default_pairs']}, max {cfg['max_pairs']})")
    parser.add_argument("--seed", type=int, default=cfg["seed"], help="positive uint32 opening seed")
    parser.add_argument("--profile", choices=config["hardware_profiles"],
                        default=config["default_hardware_profile"], help="capacity limit on this worker")
    parser.add_argument("--threads", type=int, default=1, help="search threads per active board")
    parser.add_argument("--concurrency", type=int, help="simultaneous games, subject to admission")
    parser.add_argument("--calibrate", action="store_true", help="measure fixed-pair games/hour at "
                        "one and two threads using the admitted concurrency; writes throughput.json")
    try:
        args = parser.parse_args()
        with worker_lock():
            return execute(args, config)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"local-match: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
