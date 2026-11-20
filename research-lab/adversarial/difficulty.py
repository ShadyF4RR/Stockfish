#!/usr/bin/env python3
"""Measure bounded, position-specific opponent difficulty from real UCI searches."""
import argparse
import asyncio
import hashlib
import json
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from statistics import mean
from tempfile import NamedTemporaryFile


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def scarcity(best, values, legal, tolerance):
    """Identify the range of the fraction of legal moves outside the tolerance."""
    if not 1 <= len(values) <= legal:
        raise ValueError("invalid MultiPV coverage")
    failing = sum(best - value > tolerance + 1e-12 for value in values)
    return {"legal_moves": legal, "analyzed_moves": len(values),
            "viable_observed": len(values) - failing,
            "fraction_outside_tolerance": [failing / legal, (failing + legal - len(values)) / legal]}


def regret(best, chosen, tolerance):
    delta = best - chosen
    return {"signed_expected_score_loss": delta, "regret_expected_score": max(0.0, delta),
            "error": delta > tolerance + 1e-12}


def composite(instability, scarcity_bounds, forcing_share, error_rate):
    return [(instability + endpoint + forcing_share + error_rate) / 4 for endpoint in scarcity_bounds]


def score_data(info, board):
    import chess.engine as ce
    if info.get("upperbound") or info.get("lowerbound") or "score" not in info:
        raise ValueError("UCI search returned a bound or omitted its score")
    score = info["score"].pov(board.turn)
    mate = score.mate()
    native = info.get("wdl")
    wdl = None
    if native is not None:
        outcome = native.pov(board.turn)
        wdl = [outcome.wins, outcome.draws, outcome.losses]
        if sum(wdl) != 1000 or min(wdl) < 0:
            raise ValueError("malformed native WDL")
    if mate is not None:
        # Mate(0) and MateGiven both report 0, with opposite outcomes.
        expected = float(score > ce.Cp(0))
        convention = "mate_outcome"
    elif wdl is not None:
        expected = (wdl[0] + 0.5 * wdl[1]) / 1000
        convention = "engine_wdl"
    else:
        expected = score.wdl(model="sf16.1", ply=board.ply()).expectation()
        convention = "python_chess_sf16.1"
    return {"cp": score.score(), "mate": mate, "wdl": wdl,
            "expected_score": expected, "conversion": convention}


def ensure_comparable(scores):
    models = {score["conversion"] for score in scores if score["mate"] is None}
    if len(models) > 1:
        raise ValueError("cannot mix native WDL and modeled CP within an evaluation comparison")


def pv_forcing(board, pv, horizon=6):
    copy = board.copy(stack=False)
    events = []
    for move in pv[:horizon]:
        if not copy.is_legal(move):
            raise ValueError("illegal move in principal variation")
        events.append({"move": move.uci(), "capture": copy.is_capture(move),
                       "check": copy.gives_check(move), "promotion": bool(move.promotion)})
        copy.push(move)
    if not events:
        raise ValueError("empty principal variation")
    count = sum(any(p[key] for key in ("capture", "check", "promotion")) for p in events)
    return {"pv": events, "forcing_plies": count, "examined_plies": len(events),
            "forcing_share": count / len(events)}


def load_positions(corpus, split, limit):
    import chess
    doc = json.loads(corpus.read_text())
    if doc.get("schema_version") != 1:
        raise ValueError("unknown corpus schema")
    rows = [row for row in doc["entries"] if row["split"] == split]
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("empty split or duplicate position ID")
    selected = []
    for row in rows[:limit]:
        board = chess.Board()
        for move in row["moves"]:
            board.push_uci(move)
        if not board.is_valid() or board != chess.Board(row["fen"]):
            raise ValueError(f"FEN differs from move history at {row['id']}")
        selected.append((row, board))
    return selected


@asynccontextmanager
async def uci(path, timeout):
    import chess.engine as ce
    transport, engine = await asyncio.wait_for(ce.popen_uci(str(path)), timeout)
    try:
        settings = {key: value for key, value in (("Hash", 16), ("Threads", 1),
                                                   ("OwnBook", False), ("NoobBook", False),
                                                   ("OnlineSyzygy", False), ("UCI_ShowWDL", True)) if key in engine.options}
        await asyncio.wait_for(engine.configure(settings), timeout)
        yield engine, {"filename": path.name, "sha256": digest(path), "uci_id": engine.id,
                       "uci_settings": settings}
    finally:
        try:
            await asyncio.wait_for(engine.quit(), min(timeout, 2))
        except (TimeoutError, OSError, ce.EngineError):
            pass
        finally:
            transport.close()


async def search(engine, board, depth, timeout, width=1, forced=None):
    import chess.engine as ce
    # Some UCI engines do not advertise MultiPV, including for K=1.
    option = engine.options.get("MultiPV")
    k = min(width, option.max if option and option.max else 1, board.legal_moves.count())
    raw = await asyncio.wait_for(engine.analyse(
        board, ce.Limit(depth=depth), multipv=k if option else None, game=object(),
        root_moves=[forced] if forced is not None else None), timeout)
    lines = raw if isinstance(raw, list) else [raw]
    result = []
    for rank, line in enumerate(lines, 1):
        pv = line.get("pv", [])
        if (not pv or not board.is_legal(pv[0]) or line.get("depth", -1) != depth
                or line.get("multipv", 1) != rank or (forced is not None and pv[0] != forced)):
            raise ValueError(f"unusable UCI depth {depth} line at rank {rank}: {line}")
        result.append({"move": pv[0].uci(), "depth": line["depth"],
                       "pv": [m.uci() for m in pv], "score": score_data(line, board)})
    if len(result) != k or len({line["move"] for line in result}) != k:
        raise ValueError("missing or duplicate MultiPV moves")
    return result


async def position(row, board, diagnostic, reference, opponents, args):
    import chess
    record = {key: row[key] for key in ("id", "split", "family", "group", "fen")}
    record["side_to_move"] = "white" if board.turn else "black"
    legal = board.legal_moves.count()
    if legal < 2 or board.is_game_over():
        return {**record, "excluded": "terminal or fewer than two legal choices", "legal_moves": legal}
    scans = [await search(diagnostic, board, depth, args.timeout, args.multipv)
             for depth in args.depths]
    ensure_comparable(line["score"] for scan in scans for line in scan)
    best = [scan[0]["score"]["expected_score"] for scan in scans]
    record["depth_scans"] = scans
    record["instability"] = max(best) - min(best)
    record["best_move_changes"] = sum(a[0]["move"] != b[0]["move"] for a, b in zip(scans, scans[1:]))
    final = scans[-1]
    record["scarcity"] = scarcity(best[-1], [s["score"]["expected_score"] for s in final],
                                  legal, args.threshold)
    record["tactical"] = pv_forcing(board, [chess.Move.from_uci(m) for m in final[0]["pv"]])
    top = (await search(reference, board, args.reference_depth, args.timeout))[0]["move"]
    rescored = {}
    async def score_move(move):
        if move not in rescored:
            result = await search(reference, board, args.reference_depth, args.timeout,
                                  forced=chess.Move.from_uci(move))
            rescored[move] = result[0]["score"]
        return rescored[move]
    record["reference_best"] = {"move": top, "score": await score_move(top)}
    record["opponents"] = {}
    for family, engine in opponents.items():
        chosen = (await search(engine, board, args.opponent_depth, args.timeout))[0]
        judged = await score_move(chosen["move"])
        ensure_comparable((rescored[top], judged))
        assessed = regret(rescored[top]["expected_score"], judged["expected_score"], args.threshold)
        record["opponents"][family] = {"move": chosen["move"], "opponent_score": chosen["score"],
                                       "reference_score": judged, **assessed}
    rate = mean(int(v["error"]) for v in record["opponents"].values())
    record["observed_panel_error_rate"] = rate
    record["difficulty_interval"] = composite(record["instability"],
        record["scarcity"]["fraction_outside_tolerance"], record["tactical"]["forcing_share"], rate)
    return record


def summarize(rows):
    eligible = [row for row in rows if "opponents" in row]
    if not eligible:
        return {"eligible_positions": 0}
    summary = {}
    for name in eligible[0]["opponents"]:
        plays = [row["opponents"][name] for row in eligible]
        errors = sum(play["error"] for play in plays)
        summary[name] = {"errors": errors, "choices": len(plays), "error_rate": errors / len(plays),
                         "mean_regret_expected_score": mean(p["regret_expected_score"] for p in plays)}
    return {"eligible_positions": len(eligible), "opponents": summary,
            "mean_difficulty_interval": [mean(row["difficulty_interval"][i] for row in eligible)
                                         for i in range(2)]}


async def measure(args):
    import chess
    corpus = args.corpus.resolve(strict=True)
    rows = load_positions(corpus, args.split, args.limit)
    specs = {}
    for spec in args.opponent:
        family, sep, executable = spec.partition("=")
        key = family.strip().casefold()
        if not sep or not key or key in specs or key == args.reference_family.strip().casefold():
            raise ValueError("use distinct opponent and reference engine families")
        specs[key] = Path(executable).resolve(strict=True)
    async with AsyncExitStack() as stack:
        async def start(path):
            return await stack.enter_async_context(uci(path.resolve(strict=True), args.timeout))
        diagnostic, diag_meta = await start(args.engine)
        reference, ref_meta = await start(args.reference)
        opponents, opp_meta = {}, {}
        seen = {ref_meta["sha256"]}
        for name, path in specs.items():
            opponents[name], opp_meta[name] = await start(path)
            if opp_meta[name]["sha256"] in seen:
                raise ValueError("reference and opponent families must use distinct binaries")
            seen.add(opp_meta[name]["sha256"])
        result = [await position(row, board, diagnostic, reference, opponents, args)
                  for row, board in rows]
    return {"schema_version": 1, "metric": "experimental_opponent_difficulty_v1",
            "corpus_sha256": digest(corpus), "split": args.split, "python_chess": chess.__version__,
            "settings": {"depths": args.depths, "reference_depth": args.reference_depth,
                         "opponent_depth": args.opponent_depth, "requested_multipv": args.multipv,
                         "threshold_expected_score": args.threshold, "per_search_timeout_s": args.timeout,
                         "ordering": "corpus order", "position_limit": args.limit},
            "engines": {"diagnostic": diag_meta, "reference": {**ref_meta, "family": args.reference_family},
                        "opponents": opp_meta}, "positions": result, "summary": summarize(result)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path(__file__).with_name("positions.json"))
    parser.add_argument("--split", choices=("discovery", "holdout"), default="discovery")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-family", required=True)
    parser.add_argument("--opponent", metavar="FAMILY=PATH", action="append", required=True)
    parser.add_argument("--depths", type=int, nargs="+", default=[6, 9, 12])
    parser.add_argument("--reference-depth", type=int, default=14)
    parser.add_argument("--opponent-depth", type=int, default=6)
    parser.add_argument("--multipv", type=int, default=4)
    parser.add_argument("--threshold", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (not 2 <= len(args.depths) <= 8 or sorted(set(args.depths)) != args.depths
            or not 1 <= min(args.depths) <= max(args.depths) <= 40
            or not 1 <= args.opponent_depth < args.reference_depth <= 40
            or not 2 <= args.multipv <= 32 or not 1 <= args.limit <= 1000
            or not 0 < args.threshold < 1 or not 0 < args.timeout <= 300
            or len(args.opponent) > 12 or not args.reference_family.strip()):
        parser.error("invalid search, threshold, engine-count or suite bounds")
    if args.output.exists():
        parser.error("choose a new output path")
    try:
        result = asyncio.run(measure(args))
        with NamedTemporaryFile(mode="w", dir=args.output.parent, delete=False) as tmp:
            try:
                json.dump(result, tmp, indent=2, allow_nan=False)
                tmp.write("\n")
                tmp.close()
                os.link(tmp.name, args.output)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
    except (ImportError, ValueError, OSError, RuntimeError, TimeoutError) as exc:
        parser.exit(1, f"measurement failed: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
