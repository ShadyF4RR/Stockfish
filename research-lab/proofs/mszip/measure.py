#!/usr/bin/env python3
"""Compare the official Linux SF19 executable with a trusted, self-extracting MSZIP launcher."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import statistics
import subprocess
import tempfile
import threading
import time

NATIVE_SHA = "0f83d24cc46d2c66c60f16001af5444873bc112b7d028594513426894c12da19"
NET_SHA = "1a298aa575a085434d29027978dc36867fe9c5bcea9376654b7a8eba1e52dfc2"
BENCH = 2497913
POSITIONS = ("startpos", "startpos moves e2e4 c7c5", "startpos moves d2d4 d7d5 c2c4")
MOTIONS = ("native", "packed_fresh", "packed_cached")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def memory(pid):
    lines = Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines()
    keys = {"Rss", "Pss", "Private_Clean", "Private_Dirty", "Shared_Clean", "Shared_Dirty"}
    return {key: int(value.split()[0]) for key, value in
            (line.split(":", 1) for line in lines if ":" in line) if key in keys}


class Monitor:
    """Sample the launcher's process group, including its children and exec transitions."""
    def __init__(self, leader):
        self.leader = leader
        self.peak_group = 0
        self.peak_by_executable = {}
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.watch, daemon=True)

    def watch(self):
        while not self.stop.is_set():
            total = 0
            for entry in Path("/proc").iterdir():
                if not entry.name.isdecimal():
                    continue
                pid = int(entry.name)
                try:
                    if os.getpgid(pid) != self.leader:
                        continue
                    name = Path(os.readlink(entry / "exe")).name
                    resident = int((entry / "statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024
                except (OSError, ValueError, IndexError):
                    continue
                total += resident
                self.peak_by_executable[name] = max(self.peak_by_executable.get(name, 0), resident)
            self.peak_group = max(self.peak_group, total)
            self.stop.wait(0.01)


def trial(exe, cache, native_sha, cpu, timeout, full, packed):
    def expire(*_):
        raise TimeoutError("engine trial exceeded its deadline")

    def constrained():
        os.sched_setaffinity(0, {cpu})

    def send(command):
        proc.stdin.write(command + "\n")
        proc.stdin.flush()

    def until(prefix):
        lines = []
        for line in proc.stdout:
            lines.append(line.rstrip())
            if line.strip().startswith(prefix):
                return lines
        raise ValueError(f"missing {prefix}; last output: {lines[-3:]}")

    def search(position, nodes):
        send("ucinewgame\nisready")
        until("readyok")
        send(f"position {position}\ngo nodes {nodes}")
        lines = until("bestmove ")
        infos = [line for line in lines if " nodes " in line and " nps " in line and " score " in line]
        last = infos[-1]
        def field(name):
            return int(re.search(r"\b" + name + r" (\d+)", last).group(1))
        return dict(nodes=field("nodes"), nps=field("nps"), search_ms=field("time"),
                    score=re.search(r"score (cp -?\d+|mate -?\d+)", last).group(1),
                    bestmove=lines[-1].split()[1], depth=field("depth"))

    cache = cache.resolve()
    cache.mkdir(mode=0o700, exist_ok=True)
    record = {}
    env = {"PATH": os.defpath, "HOME": str(cache.parent), "LC_ALL": "C",
           "STOCKFISH_MSZIP_CACHE": str(cache), "GOMAXPROCS": "1"}
    start = time.monotonic()
    proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=env, cwd=cache.parent,
                            preexec_fn=constrained, start_new_session=True)
    monitor = Monitor(proc.pid)
    monitor.thread.start()
    signal.signal(signal.SIGALRM, expire)
    signal.alarm(timeout)
    try:
        send("uci")
        lines = until("uciok")
        if "id name Stockfish 19" not in lines:
            raise ValueError("unexpected engine UCI identity")
        record["uci_seconds"] = time.monotonic() - start
        send("setoption name Threads value 1\nsetoption name Hash value 16\nisready")
        until("readyok")
        record["ready_seconds"] = time.monotonic() - start
        # Linux launcher replaces itself with the restored engine before UCI ready.
        running = Path(f"/proc/{proc.pid}/exe").resolve(strict=True)
        expected_path = cache / ("stockfish-" + native_sha) if packed else exe
        if running != expected_path:
            raise ValueError(f"unexpected exec target {running}")
        record["ready_memory_kib"] = memory(proc.pid)
        record["first_search"] = search("startpos", 100000)
        record["first_bestmove_seconds"] = time.monotonic() - start
        record["search_memory_kib"] = memory(proc.pid)
        if full:
            record["steady_searches"] = [search(pos, 250000) for pos in POSITIONS]
            scores = []
            for pos in POSITIONS:
                send("position " + pos + "\neval\nisready")
                scores.extend(line.strip() for line in until("readyok")
                              if line.startswith(("NNUE evaluation", "Final evaluation")))
            if len(scores) != len(POSITIONS) * 3:
                raise ValueError("incomplete evaluation transcript")
            record["eval"] = scores
            send("bench")
            output = "\n".join(until("Nodes/second"))
            record["bench_nodes"] = int(re.search(r"Nodes searched\s*:\s*(\d+)", output).group(1))
            record["bench_nps"] = int(re.search(r"Nodes/second\s*:\s*(\d+)", output).group(1))
            if record["bench_nodes"] != BENCH:
                raise ValueError("published SF19 bench signature mismatch")
        # Hash after timing the engine; the follow-up is excluded from measured wall/CPU.
        send("quit")
        _, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        if proc.returncode:
            raise ValueError("engine exited unsuccessfully")
        record["wall_seconds"] = time.monotonic() - start
    finally:
        signal.alarm(0)
        monitor.stop.set()
        monitor.thread.join()
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        proc.stdin.close()
        proc.stdout.close()
    if digest(running) != native_sha:
        raise ValueError("executable loaded by UCI does not match official bytes")
    record.update(launched_executable=exe.name, running_executable=running.name,
                  kernel_max_process_rss_kib=usage.ru_maxrss,
                  sampled_group_peak_rss_kib=monitor.peak_group,
                  sampled_peak_rss_by_image_kib=monitor.peak_by_executable,
                  user_cpu_s=usage.ru_utime, system_cpu_s=usage.ru_stime,
                  minor_faults=usage.ru_minflt, major_faults=usage.ru_majflt,
                  disk_input_512b=usage.ru_inblock, disk_output_512b=usage.ru_oublock)
    return record


def summarize(samples):
    fields = ("uci_seconds", "ready_seconds", "first_bestmove_seconds", "kernel_max_process_rss_kib",
              "sampled_group_peak_rss_kib", "user_cpu_s", "system_cpu_s", "bench_nps")
    summary = {}
    for key in MOTIONS:
        rows = [r for r in samples if r["mode"] == key]
        summary[key] = {field: statistics.median(r[field] for r in rows if field in r) for field in fields}
        rates = [1000 * sum(s["nodes"] for s in r["steady_searches"]) /
                 sum(s["search_ms"] for s in r["steady_searches"]) for r in rows if "steady_searches" in r]
        summary[key]["steady_suite_nps"] = statistics.median(rates)
    return summary


def paired_speed(samples):
    def rate(row):
        suite = row["steady_searches"]
        return 1000 * sum(s["nodes"] for s in suite) / sum(s["search_ms"] for s in suite)

    groups = {}
    for row in samples:
        groups.setdefault(row["block"], {})[row["mode"]] = rate(row)
    n = len(groups)
    # Exact binomial sign interval for the median paired ratio. Small n can
    # require unbounded endpoints to attain 95% rather than overstating coverage.
    k = 0
    for i in range(1, n // 2 + 1):
        if 2 * sum(math.comb(n, j) for j in range(i)) / 2**n <= 0.05:
            k = i
    coverage = 1 - 2 * sum(math.comb(n, j) for j in range(k)) / 2**n
    summary = {}
    for key in ("packed_fresh", "packed_cached"):
        ratios = sorted(m[key] / m["native"] for m in groups.values())
        bounds = [ratios[k - 1], ratios[-k]] if k else [None, None]
        summary[key] = dict(median_ratio=statistics.median(ratios), interval=bounds,
                            coverage=coverage, blocks=n, method="paired sign order statistics")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("launcher", type=Path)
    parser.add_argument("report", type=Path, help="new JSON file")
    parser.add_argument("--blocks", type=int, default=10, choices=range(3, 11))
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    args = parser.parse_args()
    native = args.native.resolve(strict=True)
    packed = args.launcher.resolve(strict=True)
    if args.cpu not in os.sched_getaffinity(0) or digest(native) != NATIVE_SHA or packed == native:
        parser.error("need an allowed CPU, official sf_19 Linux universal binary, and its separate launcher")
    manifest = json.loads((packed.parent / "manifest.json").read_text())
    launcher_digest = digest(packed)
    if (manifest.get("codec") != "MSZIP" or manifest.get("platform") != "linux/amd64"
            or manifest.get("input_sha256") != NATIVE_SHA or manifest.get("launcher_sha256") != launcher_digest):
        parser.error("launcher and its build.sh MSZIP manifest must identify the official release")
    if args.report.exists():
        parser.error("output exists")
    cpu = Path("/proc/cpuinfo").read_text().split("\n\n")[0]
    report = dict(schema_version=1, native_sha256=NATIVE_SHA, launcher_sha256=launcher_digest,
                  cpu=args.cpu, kernel=platform.release(), cpuinfo=cpu,
                  default_nnue_sha256=NET_SHA, positions=POSITIONS,
                  page_cache="uncontrolled; empty launcher cache is first-use, filesystem pages may be warm",
                  pmu_devices=sorted(p.name for p in Path("/sys/bus/event_source/devices").iterdir()),
                  samples=[])
    signature = None
    with tempfile.TemporaryDirectory(prefix="sf19-mszip-measure-") as tmp:
        root = Path(tmp)
        cached = root / "warm"
        trial(packed, cached, NATIVE_SHA, args.cpu, 45, False, True)
        for block in range(args.blocks):
            order = MOTIONS[block % 3:] + MOTIONS[:block % 3]
            if block % 2:
                order = tuple(reversed(order))
            for key in order:
                cache = root / key if key == "native" else cached if key == "packed_cached" else root / f"new-{block}"
                row = trial(native if key == "native" else packed, cache, NATIVE_SHA, args.cpu,
                            60, True, key != "native")
                identity = (row["first_search"]["bestmove"], row["first_search"]["score"], row["eval"],
                            [(s["nodes"], s["score"], s["bestmove"]) for s in row["steady_searches"]])
                if signature is not None and identity != signature:
                    raise ValueError("evaluation, searched nodes, best moves or scores differ")
                signature = identity
                report["samples"].append(dict(block=block + 1, mode=key, **row))
                if key == "packed_fresh":
                    shutil.rmtree(cache)
            print(f"completed block {block + 1}/{args.blocks}", flush=True)
    report["medians"] = summarize(report["samples"])
    report["paired_speed"] = paired_speed(report["samples"])
    args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
