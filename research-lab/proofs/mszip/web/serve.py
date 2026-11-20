#!/usr/bin/env python3
"""Serve the local HTML client and run a bounded native UCI search per request."""
import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading

HERE = Path(__file__).resolve().parent
FILES = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
         "/style.css": ("style.css", "text/css")}
NODES = 20000
FEN = re.compile(r"([prnbqkPRNBQK1-8]+(?:/[prnbqkPRNBQK1-8]+){7}) [wb] (?:[KQkq]{1,4}|-) "
                 r"(?:[a-h][36]|-) [0-9]{1,4} [1-9][0-9]{0,3}")


class EngineError(Exception):
    pass


def position_command(value):
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError("Enter startpos or a six-field FEN.")
    if value == "startpos":
        return "position startpos"
    match = FEN.fullmatch(value)
    if not match:
        raise ValueError("Enter startpos or a six-field FEN for standard chess.")
    board = match.group(1)
    if board.count("k") != 1 or board.count("K") != 1 or any(
        sum(int(p) if p.isdigit() else 1 for p in row) != 8 for row in board.split("/")
    ):
        raise ValueError("Each rank needs eight squares, and each side needs a king.")
    return "position fen " + value


async def read_until(engine, marker, result):
    while True:
        raw = await engine.stdout.readline()
        if not raw:
            raise EngineError("The engine exited before it completed the search.")
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("id name "):
            result["engine"] = line[8:][:100]
        if "Invalid FEN" in line:
            raise ValueError("Stockfish rejected this FEN. Check the position and side to move.")
        if line.startswith("info depth "):
            for name in ("depth", "nodes"):
                field = re.search(rf"\b{name} ([0-9]+)\b", line)
                if field:
                    result[name] = int(field.group(1))
        if marker == "bestmove":
            move = re.fullmatch(r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?|\(none\)|0000)(?: ponder [a-h][1-8][a-h][1-8][qrbn]?)?", line)
            if move:
                result["bestmove"] = move.group(1)
                return
        elif line == marker:
            return


async def search(executable, command):
    # EOF and quit reach the UCI engine through launchers that forward standard I/O.
    engine = await asyncio.create_subprocess_exec(str(executable), cwd=executable.parent,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    result = {}

    async def exchange():
        engine.stdin.write(b"uci\n")
        await read_until(engine, "uciok", result)
        engine.stdin.write(b"setoption name Threads value 1\nsetoption name Hash value 16\nisready\n")
        await read_until(engine, "readyok", result)
        engine.stdin.write(f"{command}\ngo nodes {NODES}\n".encode("ascii"))
        await read_until(engine, "bestmove", result)

    try:
        await asyncio.wait_for(exchange(), timeout=45)
        return result
    finally:
        if engine.returncode is None:
            try:
                engine.stdin.write(b"stop\nquit\n")
                await engine.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            engine.stdin.close()
            try:
                await asyncio.wait_for(engine.wait(), timeout=2)
            except asyncio.TimeoutError:
                engine.kill()
                await engine.wait()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def send(self, status, data, content_type="application/json"):
        if isinstance(data, dict):
            data = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def allowed_host(self):
        return self.headers.get_all("Host") == [self.server.address]

    def do_GET(self):
        if not self.allowed_host():
            return self.send(403, {"error": "Use the printed loopback URL."})
        if self.path not in FILES:
            return self.send(404, {"error": "Unknown page."})
        filename, mime = FILES[self.path]
        self.send(200, (HERE / filename).read_bytes(), mime)

    def do_POST(self):
        if not self.allowed_host() or self.headers.get_all("Origin") != ["http://" + self.server.address]:
            return self.send(403, {"error": "Open the client at the printed loopback URL."})
        if self.path != "/analyze":
            return self.send(404, {"error": "Unknown operation."})
        if self.headers.get_content_type() != "application/json":
            return self.send(415, {"error": "Expected application/json."})
        try:
            lengths = self.headers.get_all("Content-Length") or []
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,4}", lengths[0]) or int(lengths[0]) > 512:
                raise ValueError("Expected a JSON position of at most 512 bytes.")
            payload = json.loads(self.rfile.read(int(lengths[0])))
            if not isinstance(payload, dict) or set(payload) != {"position"}:
                raise ValueError("Expected one position field.")
            command = position_command(payload["position"])
        except (ValueError, UnicodeDecodeError):
            return self.send(400, {"error": "Enter startpos or a valid six-field FEN."})
        if not self.server.slot.acquire(blocking=False):
            return self.send(429, {"error": "A search is already running. Try again when it finishes."})
        try:
            result = asyncio.run(search(self.server.engine, command))
        except ValueError as exc:
            return self.send(422, {"error": str(exc)})
        except (OSError, EngineError, asyncio.TimeoutError):
            return self.send(502, {"error": "The engine stopped or timed out. Check the position and the configured executable."})
        finally:
            self.server.slot.release()
        self.send(200, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path, help="trusted local UCI executable or MSZIP launcher")
    parser.add_argument("--port", type=int, default=8765, help="loopback port, or 0 for a free port")
    args = parser.parse_args()
    engine = args.engine.expanduser().resolve()
    if not engine.is_file() or not os.access(engine, os.X_OK):
        parser.error("engine must be a local executable")
    if not 0 <= args.port <= 65535:
        parser.error("port outside 0..65535")
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.engine = engine
        server.slot = threading.Lock()
        server.address = f"127.0.0.1:{server.server_port}"
        print("http://" + server.address + "/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
