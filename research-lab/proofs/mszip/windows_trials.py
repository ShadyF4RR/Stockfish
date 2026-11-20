"""Bounded paired measurements for an official Windows release and its MSZIP launcher."""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import tempfile
import threading
import time


class Entry(c.Structure):
    _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
                ("heap", c.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
                ("exe", w.WCHAR * 260)]


class Memory(c.Structure):
    _fields_ = [("size", w.DWORD), ("faults", w.DWORD), ("peak_ws", c.c_size_t),
                ("working_set", c.c_size_t), ("remaining_counters", c.c_size_t * 6)]


def function(dll, name, restype, *argtypes):
    fn = getattr(dll, name)
    fn.argtypes, fn.restype = argtypes, restype
    return fn


class WorkingSetSampler:
    """Sum contemporaneous resident working sets for the root and its descendants."""

    def __init__(self):
        kernel = c.WinDLL("kernel32", use_last_error=True)
        psapi = c.WinDLL("psapi", use_last_error=True)
        self.snapshot = function(kernel, "CreateToolhelp32Snapshot", w.HANDLE, w.DWORD, w.DWORD)
        self.first = function(kernel, "Process32FirstW", w.BOOL, w.HANDLE, c.POINTER(Entry))
        self.next = function(kernel, "Process32NextW", w.BOOL, w.HANDLE, c.POINTER(Entry))
        self.close = function(kernel, "CloseHandle", w.BOOL, w.HANDLE)
        self.open = function(kernel, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
        self.memory = function(psapi, "GetProcessMemoryInfo", w.BOOL, w.HANDLE, c.POINTER(Memory), w.DWORD)

    def sample(self, root_pid):
        entries = {}
        snapshot = self.snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if snapshot == w.HANDLE(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = Entry()
            entry.size = c.sizeof(entry)
            valid = self.first(snapshot, c.byref(entry))
            while valid:
                entries.setdefault(entry.parent, []).append(entry.pid)
                valid = self.next(snapshot, c.byref(entry))
        finally:
            self.close(snapshot)
        queue = [root_pid]
        resident = counted = 0
        while queue:
            pid = queue.pop()
            queue.extend(entries.get(pid, []))
            handle = self.open(0x1010, False, pid)  # Query limited information + VM read.
            if handle:
                try:
                    counters = Memory()
                    counters.size = c.sizeof(counters)
                    if self.memory(handle, c.byref(counters), counters.size):
                        resident += counters.working_set
                        counted += 1
                finally:
                    self.close(handle)
        return resident, counted


def run(exe, env, directory, sampler, kind, block):
    began = time.perf_counter()
    with subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=env, cwd=directory, text=True, encoding="utf-8", errors="replace") as proc:
        peak = [0, 0]
        errors = []
        done = threading.Event()

        def measure():
            try:
                while not done.is_set():
                    total, count = sampler.sample(proc.pid)
                    peak[0] = max(peak[0], total)
                    peak[1] = max(peak[1], count)
                    done.wait(0.05)
            except OSError as err:
                errors.append(str(err))

        thread = threading.Thread(target=measure, daemon=True)
        thread.start()
        watchdog = threading.Timer(90, proc.kill)
        watchdog.start()
        seen = {}
        try:
            for sent, pattern, label in (("uci", "uciok", "uciok_ms"),
                                          ("isready", "readyok", "readyok_ms")):
                proc.stdin.write(sent + "\n")
                proc.stdin.flush()
                for line in proc.stdout:
                    if line.strip() == pattern:
                        seen[label] = (time.perf_counter() - began) * 1000
                        break
                if label not in seen:
                    raise RuntimeError(f"{kind}: early EOF waiting for {pattern}")
            proc.stdin.write("bench\n")
            proc.stdin.flush()
            for line in proc.stdout:
                match = re.fullmatch(r"(Nodes searched|Nodes/second|Total time \(ms\))\s*:\s*(\d+)", line.strip())
                if match:
                    seen[match[1]] = int(match[2])
                    if len(seen) == 5:
                        break
            if seen.get("Nodes searched") != 2497913 or seen.get("Nodes/second", 0) <= 0:
                raise RuntimeError(f"Unexpected canonical bench: {seen}")
            proc.stdin.write("quit\n")
            proc.stdin.flush()
            if proc.wait(timeout=10):
                raise RuntimeError(f"{kind}: unsuccessful exit")
        finally:
            if proc.poll() is None:
                proc.kill()
            watchdog.cancel()
            done.set()
            thread.join(5)
    complete = not errors and peak[1] >= (2 if kind == "mszip" else 1)
    return dict(kind=kind, block=block, **seen, max_observed_processes=peak[1],
                sampled_tree_peak_working_set_bytes=peak[0] if complete else None,
                sample_errors=errors, wall_seconds=time.perf_counter() - began)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for field in ("original", "launcher", "output"):
        cli.add_argument(field, type=Path)
    args = cli.parse_args()
    binaries = dict(native=args.original.resolve(strict=True), mszip=args.launcher.resolve(strict=True))
    hashes = {k: digest(v) for k, v in binaries.items()}
    if hashes["native"] != "45bc8e4969147db9c2eb533810637994619bff0eacc81ccfd9854394901bcbd0":
        raise ValueError("unexpected Stockfish 19 Windows image")
    report = dict(environment=platform.platform(), cpu=platform.processor(), sha256=hashes,
                  cache="fresh launcher install, then fresh processes using the populated install; OS file cache uncontrolled",
                  ram="Maximum sampled sum of process-tree working sets at 50 ms intervals; shared pages may be counted twice. Null if a required process was never observed.",
                  trials=[])
    sampler = WorkingSetSampler()
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, STOCKFISH_MSZIP_CACHE=str(Path(tmp) / "cache"))
        for block, order in (("prime", ["native"]), ("fresh-install", ["mszip"]),
                             ("warm-1", ["native", "mszip"]), ("warm-2", ["mszip", "native"]),
                             ("warm-3", ["native", "mszip"])):
            for kind in order:
                report["trials"].append(run(binaries[kind], env, tmp, sampler, kind, block))
    report["warm_medians"] = {}
    for kind in binaries:
        rows = [r for r in report["trials"] if r["block"].startswith("warm-") and r["kind"] == kind]
        report["warm_medians"][kind] = {
            key: statistics.median(r[key] for r in rows) if all(r[key] is not None for r in rows) else None
            for key in ("readyok_ms", "Nodes/second", "sampled_tree_peak_working_set_bytes")}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"sha256": hashes, "warm_medians": report["warm_medians"],
                      "fresh_install_ms_to_readyok": report["trials"][1]["readyok_ms"]}, indent=2))


if __name__ == "__main__":
    main()
