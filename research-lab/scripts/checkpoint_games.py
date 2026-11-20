"""Durable per-game identity for a bounded, color-reversed Fastchess batch."""
import json
import os
import re
import subprocess
import time


def write(path, data):
    temp = path.with_suffix(".tmp")
    with temp.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def completed(directory, white, fen, clock, round_number, opening_key):
    path = directory / "game.pgn"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024**2:
        raise ValueError("admitted game lacks a durable PGN: " + directory.name)
    text = path.read_text().strip()
    headers, sep, moves = text.partition("\n\n")
    tags = dict(re.findall(r'^\[(\w+) "([^"\n]*)"\]$', headers, re.M))
    result = tags.get("Result")
    if (not sep or result not in ("1-0","0-1","1/2-1/2") or moves.split()[-1:] != [result]
            or re.search(r'^\[', moves, re.M) or tags.get("Round") != "1"
            or tags.get("White") != white or {white,tags.get("Black")} != {"baseline","candidate"}
            or tags.get("Termination") != "normal" or tags.get("TimeControl") != clock
            or opening_key(tags.get("FEN","")) != opening_key(fen)):
        raise ValueError("unsettled game " + directory.name)
    return text.replace('[Round "1"]', f'[Round "{round_number}"]', 1)


def play(command, rows, out, record, deadline, apply_limits, opening_key):
    # Each fresh manager plays one immutable opening/color. Starting managers in
    # the main thread keeps preexec_fn safe. No command is loaded from a spool.
    settings = record["settings"]
    plan = record["allocation"]
    prefix = command[:command.index("-openings")]
    seal = {key:record[key] for key in ("fastchess_sha256","book_sha256")}
    for key in ("candidate","baseline"):
        seal[key] = record[key]["binary_sha256"]
    seal.update({k:settings[k] for k in ("pairs","seed","time_control","hash_mib","threads","concurrency")})
    seal_path = out / "plan.json"
    if seal_path.exists() and json.loads(seal_path.read_text()) != seal:
        raise ValueError("checkpoint belongs to a different match")
    if not seal_path.exists():
        if any(out.glob("game-*")):
            raise ValueError("missing plan for existing games")
        write(seal_path, json.dumps(seal, sort_keys=True).encode())
    entries = [(i, white, fen, out / f"game-{i:03d}-{white}") for i,fen in enumerate(rows,1)
               for white in ("candidate","baseline")]
    pending = []
    scores = {}
    uncertain = []
    for i,white,fen,directory in entries:
        if not directory.exists():
            pending.append((i,white,fen,directory))
            continue
        try:
            scores[i,white] = completed(directory,white,fen,settings["time_control"],i,opening_key)
        except (OSError, ValueError, IndexError):
            uncertain.append(directory.name)
    def checkpoint():
        data = "\n\n".join(scores[key] for key in sorted(scores)) + "\n"
        write(out / "games.pgn", data.encode())
    checkpoint()
    if uncertain:
        raise ValueError("interrupted game identities require reconciliation: " + ", ".join(uncertain))
    active = {}
    try:
        while pending or active:
            if time.monotonic() >= deadline:
                raise TimeoutError("campaign deadline reached")
            while pending and len(active) < plan["concurrency"]:
                i,white,fen,directory = pending.pop(0)
                directory.mkdir()
                write(directory / "book.epd", (fen+"\n").encode())
                args = prefix + ["-openings","file=book.epd","format=epd","order=sequential",
                                 "-srand",str(settings["seed"]),"-rounds","1","-games","1",
                                 "-concurrency","1","-pgnout","file=game.pgn","min=false"]
                if white == "baseline":
                    args.append("-reverse")
                with (directory / "fastchess.log").open("wb") as log:
                    proc = subprocess.Popen(args,cwd=directory,stdout=log,stderr=subprocess.STDOUT,
                                            env={"PATH":os.defpath,"HOME":str(directory),"LC_ALL":"C"},
                                            preexec_fn=lambda: apply_limits(plan))
                active[proc] = (i,white,fen,directory)
            finished = False
            for proc,(i,white,fen,directory) in list(active.items()):
                if proc.poll() is None:
                    continue
                if proc.returncode:
                    raise ValueError("Fastchess failed in " + directory.name)
                scores[i,white] = completed(directory,white,fen,settings["time_control"],i,opening_key)
                del active[proc]
                finished = True
            if finished:
                checkpoint()
            if active:
                time.sleep(.2)
    finally:
        for proc in active:
            if proc.poll() is None:
                proc.kill()
        for proc in active:
            proc.wait()
        # The enclosing namespace and delegated cgroup also drain engine children.
        with (out / "fastchess.log").open("wb") as log:
            for *_,directory in entries:
                path = directory / "fastchess.log"
                if path.is_file():
                    log.write(path.read_bytes()[:32768])
