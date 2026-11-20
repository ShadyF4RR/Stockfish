#!/usr/bin/env python3
"""Register and run a fixed panel of paired Stockfish and independent-engine games on Linux."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import sys
import tempfile
import time

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "scripts"))
sys.path.insert(0, str(LAB / "continuous"))
from local_match import COMPARABLE, START, opening_key, read_build, sha256
from worker_resources import allocation, capacity, worker_lock
from validate_candidate import Sandbox

PROFILES = {
    "stockfish": {"source": "research/baseline", "version": "1819b9ea", "options": ["Threads=1", "Ponder=false"]},
    "fruit": {"source": "Debian fruit_2.1.dfsg-9_amd64.deb", "version": "Fruit 2.1",
              "package_sha256": "fabbe937de701f7ca91396b4bd924784a139023bdc8af4dd37508ac6ebcf72d1",
              "options": ["OwnBook=false", "Ponder=false"]},
    "ethereal": {"source": "https://github.com/AndyGrant/Ethereal", "version": "V12.00",
                 "commit": "37ccbfe233ac418a49cb9a5c9f54d62b060d44ad", "options": ["Threads=1", "Ponder=false"]},
    "weiss": {"source": "https://github.com/TerjeKir/weiss", "version": "v2.0",
              "commit": "0db49c7270f5c6286a983390a5ea2780e1e0c9f4", "options": ["Threads=1", "NoobBook=false", "OnlineSyzygy=false"]},
}
CLOCKS = ["1+0.01", "3+0.03"]
SPLITS = ["discovery", "holdout"]
ARMS = ["baseline", "candidate"]
WALL = 18 * 60
SEED = 20261120
FASTCHESS_REV = "894616028492ae6114835195f14a899f6fa237d3"


def ordered(label, value):
    return hashlib.sha256(f"{SEED}|{label}|{value}".encode()).hexdigest()


def write(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def plan(args):
    corpus = json.loads(args.corpus.read_text())
    selected = {}
    for split in SPLITS:
        rows = []
        for group in ("tactical", "general", "quiet"):
            pool = [r for r in corpus["entries"] if r["split"] == split and r["group"] == group]
            chosen = min(pool, key=lambda r: ordered(split, r["id"]))
            rows.append({k: chosen[k] for k in ("id", "group", "family", "fen")})
        selected[split] = sorted(rows, key=lambda r: ordered("order", r["id"]))
    all_roots = [r for rows in selected.values() for r in rows]
    if len({opening_key(r["fen"]) for r in all_roots}) != len(all_roots):
        raise ValueError("repeated selected position")
    builds = {}
    for arm in ARMS:
        directory = getattr(args, arm)
        _, meta = read_build(directory)
        val = json.loads((directory / "validation.json").read_text())
        if (val["commit"] != meta["commit"] or val["binary_sha256"] != meta["binary_sha256"]
                or any(val.get(k) != "passed" for k in ("uci", "perft", "reprosearch"))):
            raise ValueError(f"incomplete {arm} correctness")
        builds[arm] = meta
    if builds["baseline"]["commit"] == builds["candidate"]["commit"]:
        raise ValueError("candidate and baseline commits coincide")
    for field in COMPARABLE:
        if builds["baseline"][field] != builds["candidate"][field]:
            raise ValueError(f"unmatched builds: {field}")
    refs = {name: dict(profile, sha256=sha256(path)) for (name, profile), path in
            ((item, engine(args, item[0])) for item in PROFILES.items())}
    cells = []
    for split in SPLITS:
        blocks = sorted(((name, clock) for name in PROFILES for clock in CLOCKS),
                        key=lambda cell: ordered(split, "/".join(cell)))
        for j, (name, tc) in enumerate(blocks):
            for arm in ARMS if j % 2 == 0 else reversed(ARMS):
                cells.append(dict(split=split, opponent=name, time_control=tc, arm=arm))
    return dict(schema_version=1, experiment="EXP-ADVERSARIAL-001", **builds, opponents=refs,
                seed=SEED, time_controls=CLOCKS, selected_positions=selected, cells=cells, planned_games=192,
                corpus_sha256=sha256(args.corpus), analysis_plan_sha256=sha256(args.analysis),
                fastchess_revision=FASTCHESS_REV, fastchess_sha256=sha256(args.fastchess),
                hash_mib=16, threads_per_engine=1, concurrency=2, timemargin_ms=100,
                wall_budget_seconds=WALL, cell_budget_seconds=120,
                invalid_policy="Keep all observed failures. No retries, substitutes or directional conclusions from incomplete cells.",
                bounds="Fixed seeds, allocation and matched openings/colors; family-clustered intervals in the preregistered analysis plan.")


def engine(args, name):
    return args.baseline / "stockfish" if name == "stockfish" else getattr(args, name)


def parse_games(path, cell, positions):
    games = []
    colors = set()
    for block in path.read_text().split("\n\n"):
        if not block.lstrip().startswith("["):
            continue
        tags = dict(re.findall(r'^\[(\w+) "([^"\n]*)"\]$', block.strip(), re.M))
        number = int(tags["Round"])
        if not 1 <= number <= len(positions):
            raise ValueError("unexpected round")
        root = positions[number-1]
        if opening_key(tags.get("FEN", START)) != opening_key(root["fen"]):
            raise ValueError("unregistered opening in PGN")
        arm, opp = "arm_" + cell["arm"], "vs_" + cell["opponent"]
        if {tags["White"], tags["Black"]} != {arm, opp}:
            raise ValueError("unexpected participants")
        color = "white" if tags["White"] == arm else "black"
        if (number, color) in colors:
            raise ValueError("duplicate assignment of color")
        colors.add((number, color))
        score = {"1-0": 1, "1/2-1/2": 0.5, "0-1": 0}[tags["Result"]]
        games.append(dict(position_id=root["id"], family=root["family"], group=root["group"], fen=root["fen"],
                          round=number, engine_color=color, result=tags["Result"],
                          engine_score=score if color == "white" else 1-score, termination=tags.get("Termination")))
    return games


def campaign(args, reg):
    path = args.out / "matches.json"
    if path.exists():
        raise ValueError("a match record already exists; inspect the preserved result")
    if reg != plan(args):
        raise ValueError("registered engine, corpus, settings, or analysis has changed")
    cfg = json.loads((LAB / "config/testing.yaml").read_text())
    match_cfg = cfg["local_match"]
    limits = capacity(match_cfg, cfg["hardware_profiles"]["cloud_detected"])
    sizes = [engine(args, name).stat().st_size for name in PROFILES] + [(args.candidate/"stockfish").stat().st_size]
    admission = allocation(limits, match_cfg, max(sizes), 1, reg["concurrency"])
    report = dict(schema_version=1, experiment=reg["experiment"], baseline=reg["baseline"],
                  candidate=reg["candidate"], opponents=reg["opponents"], time_controls=CLOCKS,
                  preregistration_sha256=sha256(args.out/"preregistration.json"), corpus_sha256=reg["corpus_sha256"],
                  admission=admission, runs=[], status="in_progress", planned_games=reg["planned_games"])
    write(path, report)
    start = time.monotonic()
    with worker_lock(), tempfile.TemporaryDirectory(prefix="sf-panel-") as temp:
        root = Path(temp)
        (root / "nets").mkdir()
        for index, cell in enumerate(reg["cells"]):
            if time.monotonic()-start >= WALL:
                break
            name = f"{index+1:02d}-{cell['split']}-{cell['opponent']}-{cell['arm']}"
            dest = args.out / "cells" / name
            dest.mkdir(parents=True)
            scratch = root / name
            scratch.mkdir()
            subset = reg["selected_positions"][cell["split"]]
            book = dest / "openings.epd"
            book.write_text("".join(r["fen"]+"\n" for r in subset))
            item = dict(**cell, pgn=f"cells/{name}/games.pgn", pgn_sha256=None, status="invalid", games=[])
            sb = Sandbox(root, 1, admission["cpu_ids"], limits["usable_mib"], False,
                         min(start+WALL, time.monotonic()+reg["cell_budget_seconds"]))
            armfile = getattr(args,cell["arm"]) / "stockfish"
            mounts = ["--ro-bind", str(armfile), "/engines/arm", "--ro-bind", str(engine(args,cell["opponent"])), "/engines/opponent",
                      "--ro-bind", str(args.fastchess), "/tools/fastchess", "--ro-bind", str(book), "/book.epd",
                      "--bind", str(scratch), "/game", "--chdir", "/game", "--setenv", "MALLOC_ARENA_MAX", "2"]
            command = ["prlimit", f"--as={admission['process_limit_mib']*1024**2}", "--", "/tools/fastchess",
                       "-engine", "cmd=/engines/arm", "name=arm_"+cell["arm"], "option.Threads=1", "option.Ponder=false",
                       "-engine", "cmd=/engines/opponent", "name=vs_"+cell["opponent"],
                       *["option."+option for option in reg["opponents"][cell["opponent"]]["options"]],
                       "-each", "proto=uci", "option.Hash=16", f"tc={cell['time_control']}", "timemargin=100",
                       "-openings", "file=/book.epd", "format=epd", "order=sequential", "-srand", str(SEED),
                       "-rounds", str(len(subset)), "-games", "2", "-repeat", "-concurrency", "2", "-report", "penta=true",
                       "-pgnout", "file=games.pgn", "min=false"]
            tick = time.monotonic()
            try:
                sb.run(command, mounts, dest/"fastchess.log")
                file = scratch/"games.pgn"
                if file.is_symlink() or not file.is_file() or file.stat().st_size > 1024**2:
                    raise ValueError("invalid PGN artifact")
                shutil.copyfile(file, dest/"games.pgn")
                item["pgn_sha256"] = sha256(dest/"games.pgn")
                item["games"] = parse_games(dest/"games.pgn", cell, subset)
                if len(item["games"]) != len(subset)*2 or any(g["termination"]!="normal" for g in item["games"]):
                    raise ValueError("missing game or abnormal termination")
                item["status"] = "complete"
            except (OSError, ValueError, KeyError, TimeoutError) as exc:
                item["reason"] = str(exc)
                file = scratch/"games.pgn"
                if not item["pgn_sha256"] and file.is_file() and not file.is_symlink() and file.stat().st_size <= 1024**2:
                    shutil.copyfile(file, dest/"games.pgn")
                    item["pgn_sha256"] = sha256(dest/"games.pgn")
            item["wall_seconds"] = time.monotonic()-tick
            report["runs"].append(item)
            write(path, report)
            print(f"{name}: {item['status']} ({len(item['games'])} games)", flush=True)
    full = len(report["runs"]) == len(reg["cells"]) and all(r["status"] == "complete" for r in report["runs"])
    report.update(status="complete" if full else "incomplete", wall_seconds=time.monotonic()-start)
    write(path, report)
    return 0 if full else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["register", "play"])
    for key in ("baseline", "candidate", "fruit", "ethereal", "weiss", "fastchess", "out"):
        p.add_argument("--"+key, required=True, type=lambda x: Path(x).resolve())
    p.add_argument("--corpus", type=Path, default=LAB/"adversarial/positions.json")
    p.add_argument("--analysis", type=Path, default=LAB/"adversarial/analysis-plan.md")
    args=p.parse_args()
    reg=args.out/"preregistration.json"
    if args.action=="register":
        args.out.mkdir(parents=True, exist_ok=True)
        with reg.open("x") as stream:
            json.dump(plan(args), stream, indent=2); stream.write("\n")
        return 0
    return campaign(args, json.loads(reg.read_text()))


if __name__ == "__main__":
    sys.exit(main())
