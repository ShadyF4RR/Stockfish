"""Exercise a trusted release or MSZIP launcher through the pipes a UCI GUI uses."""
import argparse
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading


def check(command, env, directory):
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, env=env, cwd=directory) as proc:
        lines = queue.Queue()

        def drain():
            for line in proc.stdout:
                lines.put(line.strip())
            lines.put(None)

        threading.Thread(target=drain, daemon=True).start()

        def send(line):
            proc.stdin.write(line + "\n")
            proc.stdin.flush()

        def expect(pattern):
            import time
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                line = lines.get(timeout=max(0, deadline - time.monotonic()))
                if line is None:
                    raise RuntimeError(f"engine exited waiting for {pattern}")
                if re.fullmatch(pattern, line):
                    return
            raise TimeoutError(pattern)

        try:
            send("uci")
            expect(r"id name Stockfish 19")
            expect("uciok")
            send("isready")
            expect("readyok")
            send("ucinewgame")
            send("position startpos")
            send("go depth 4 searchmoves e2e4")
            expect(r"bestmove e2e4(?: ponder \S+)?")
            send("quit")
            if proc.wait(timeout=10):
                raise RuntimeError("nonzero exit after quit")
        finally:
            if proc.poll() is None:
                proc.kill()
    result = subprocess.run([*command, "bench"], cwd=directory, env=env, timeout=180,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
    nodes = re.findall(r"^Nodes searched\s*:\s*(\d+)\s*$", result.stdout, re.M)
    if nodes != ["2497913"]:
        raise ValueError(f"unexpected canonical Stockfish 19 bench {nodes}")
    print("Stockfish 19 named itself over UCI, returned readyok and legal bestmove e2e4, "
          "exited cleanly, and matched the release bench of 2,497,913 nodes.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("engine", type=Path)
    args = cli.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        check([str(args.engine.resolve(strict=True))],
              dict(os.environ, STOCKFISH_MSZIP_CACHE=str(Path(tmp) / "cache")), tmp)
