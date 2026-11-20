#!/usr/bin/env python3
"""Linux/Python 3.9+. Compare two verified executable + NNUE copies on one CPU.
Usage: runtime.py ORIGINAL_EXE RESTORED_EXE ORIGINAL_NET RESTORED_NET > runtime.json
Runs only trusted binaries supplied by the operator. Writes no files.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import subprocess
import sys
import time

POSITIONS = ["startpos", "startpos moves e2e4 c7c5", "startpos moves d2d4 d7d5 c2c4"]


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def probe(mode, exe, net):
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    before = time.monotonic()
    if mode == "ready":
        with subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as proc:
            proc.stdin.write("uci\nisready\n")
            proc.stdin.flush()
            for line in proc.stdout:
                if line.strip() == "readyok":
                    break
            else:
                raise RuntimeError("Missing readyok")
            elapsed = time.monotonic() - before
            proc.communicate("quit\n", timeout=5)
            if proc.returncode:
                raise RuntimeError("UCI shutdown failed")
        fields = {"seconds_to_readyok": elapsed}
    elif mode == "eval":
        commands = [f"setoption name EvalFile value {net}"]
        for position in POSITIONS:
            commands.extend(["position " + position, "eval"])
        out = subprocess.run([exe], input="\n".join(commands + ["quit", ""]), text=True,
                             capture_output=True, check=True, timeout=45)
        scores = [line.strip() for line in out.stdout.splitlines()
                  if line.startswith(("NNUE evaluation", "Final evaluation"))]
        if len(scores) != len(POSITIONS) * 3:
            raise ValueError("Incomplete NNUE evaluation transcript")
        fields = {"scores": scores}
    else:
        out = subprocess.run([exe, "bench"], capture_output=True, text=True, check=True, timeout=90)
        log = out.stdout + out.stderr
        fields = {}
        for key, label in [("nodes", "Nodes searched"), ("nps", "Nodes/second")]:
            values = re.findall(r"^" + label + r"\s*:\s*(\d+)$", log, re.M)
            if len(values) != 1:
                raise ValueError("Incomplete bench output")
            fields[key] = int(values[0])
    used = resource.getrusage(resource.RUSAGE_CHILDREN)
    return dict(fields, wall_seconds=time.monotonic() - before, cpu_seconds=used.ru_utime + used.ru_stime,
                peak_rss_kib=used.ru_maxrss)


def compare(args):
    original, restored, original_net, restored_net = [Path(arg).resolve(strict=True) for arg in args]
    hashes = [digest(path) for path in [original, restored, original_net, restored_net]]
    if hashes[0] != hashes[1] or hashes[2] != hashes[3]:
        raise ValueError("SHA-256 mismatch between original and restored inputs")
    report = {"executable_sha256": hashes[0], "network_sha256": hashes[2], "positions": POSITIONS,
              "machine": platform.machine(), "kernel": platform.release(),
              "affinity_cpu": min(os.sched_getaffinity(0)), "page_cache": "uncontrolled; inputs pre-read for hashing",
              "samples": [], "perf_event_sources": sorted(p.name for p in Path('/sys/bus/event_source/devices').iterdir())}
    sides = {"original": (original, original_net), "restored": (restored, restored_net)}
    for mode in ["ready", "eval", "bench"]:
        order = ("original", "restored") if mode == "eval" else ("original", "restored", "restored", "original") * 2
        for side in order:
            # A fresh helper gives each engine invocation its own peak RSS observation.
            run = subprocess.run([sys.executable, __file__, "--probe", mode, *sides[side]],
                                 text=True, capture_output=True, timeout=100, check=True)
            report["samples"].append(dict(mode=mode, artifact=side, **json.loads(run.stdout)))
    if len({s["nodes"] for s in report["samples"] if s["mode"] == "bench"}) != 1:
        raise ValueError("Bench signature differs")
    evaluations = [s["scores"] for s in report["samples"] if s["mode"] == "eval"]
    if evaluations[0] != evaluations[1]:
        raise ValueError("NNUE evaluations differ")
    return report


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--probe":
        print(json.dumps(probe(*sys.argv[2:])))
    elif len(sys.argv) == 5:
        print(json.dumps(compare(sys.argv[1:]), indent=2))
    else:
        sys.exit(__doc__)
