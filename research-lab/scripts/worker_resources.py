"""Admission for a single Linux cgroup-v2 Fastchess worker."""
import contextlib
import fcntl
import os
from pathlib import Path
import resource
import tempfile

MIB = 1024 ** 2


def capacity(cfg, profile):
    # Select one logical CPU per physical core in the process's current affinity.
    cores = {}
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        key = tuple((topology / k).read_text().strip() for k in ("physical_package_id", "core_id"))
        cores.setdefault(key, cpu)
    count = min(len(cores), profile.get("max_machine_cpus", len(cores)))
    mem = {k: int(v.split()[0]) // 1024 for k, v in (line.split(":", 1)
           for line in Path("/proc/meminfo").read_text().splitlines())}
    total = min(mem["MemTotal"], profile.get("max_machine_mib", mem["MemTotal"]))
    available = mem["MemAvailable"]

    root = Path("/sys/fs/cgroup")
    groups = [line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines()
              if line.startswith("0::")]
    if not groups or not (root / "cgroup.controllers").is_file():
        raise ValueError("worker requires a mounted Linux cgroup v2 hierarchy")
    group = (root / groups[0].lstrip("/")).resolve(strict=True)
    if group != root and root not in group.parents:
        raise ValueError("unresolvable cgroup namespace")
    while True:
        if (group / "cpu.max").is_file():
            quota, period = (group / "cpu.max").read_text().split()
            if quota != "max":
                count = min(count, int(quota) // int(period))
        for name in ("memory.high", "memory.max"):
            if (group / name).is_file():
                limit = (group / name).read_text().strip()
                if limit != "max":
                    used = int((group / "memory.current").read_text())
                    total = min(total, int(limit) // MIB)
                    available = min(available, (int(limit) - used) // MIB)
        if group == root:
            break
        group = group.parent

    reserve_cpu = max(cfg["reserve_cpus"], profile["reserve_cpus"])
    reserve_mem = max(cfg["reserve_mib"], profile["reserve_mib"])
    slots = max(0, min(cfg["max_worker_cpus"], count - reserve_cpu))
    free = max(0, min(cfg["max_worker_mib"], min(total, available) - reserve_mem))
    return dict(cpu_ids=list(cores.values())[:slots], effective_cpus=count,
                total_mib=total, available_mib=min(available, total),
                reserve_cpus=reserve_cpu, reserve_mib=reserve_mem, usable_mib=free)


def allocation(limits, cfg, engine_bytes, threads, games=None):
    if threads < 1:
        raise ValueError("engine threads must be positive")
    # Embedded weights, decoded copies, hash, thread stacks/allocator workspace.
    # Enforce the allowance with RLIMIT_AS per process, including the manager.
    binary_mib = (engine_bytes + MIB - 1) // MIB
    process_mib = max(cfg["minimum_engine_mib"], 3 * binary_mib + 128)
    process_mib += cfg["hash_mib"] + 96 * threads
    # Both engines are resident per board. Only the side to move searches.
    memory_boards = (limits["usable_mib"] // process_mib - 1) // 2
    max_games = min(len(limits["cpu_ids"]) // threads, memory_boards)
    games = max_games if games is None else games
    if not 1 <= games <= max_games:
        raise ValueError(f"insufficient capacity for {games} games x {threads} threads "
                         f"(maximum {max(0, max_games)}, {limits['usable_mib']} MiB usable)")
    return dict(concurrency=games, threads=threads,
                cpu_ids=limits["cpu_ids"][:games * threads], process_limit_mib=process_mib,
                peak_budget_mib=(2 * games + 1) * process_mib)


def apply_limits(plan):
    os.sched_setaffinity(0, plan["cpu_ids"])
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    ceiling = plan["process_limit_mib"] * MIB
    if hard != resource.RLIM_INFINITY:
        ceiling = min(ceiling, hard)
    resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))


@contextlib.contextmanager
def worker_lock():
    # Coordinate across worktrees and avoid counting headroom twice on this worker.
    path = Path(tempfile.gettempdir()) / f"stockfish-research-worker-{os.getuid()}.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise ValueError("unexpected worker lock owner")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another match or calibration already owns this worker") from exc
        yield
    finally:
        os.close(fd)
