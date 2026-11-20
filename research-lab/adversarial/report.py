#!/usr/bin/env python3
"""Analyze a fully crossed, color-paired adversarial Stockfish screen."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import mean

SPLITS = ("discovery", "holdout")
CONTROL = "stockfish"
ALPHA = 0.05
PLAN = Path(__file__).resolve().with_name("analysis-plan.md")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(directory, name):
    path = (directory / name).resolve()
    require(directory.resolve() in path.parents, "file outside experiment evidence directory")
    return path


def from_white(result, color):
    value = {"1-0": 1.0, "0-1": 0.0, "1/2-1/2": 0.5}[result]
    return value if color == "white" else 1 - value


def wdl(values):
    counts = Counter(values)
    return [counts[1.0], counts[0.5], counts[0.0]]


def elo(score):
    if score in (0, 1):
        return None
    return 400 * math.log10(score / (1 - score))


def endpoint(values, bounds, comparisons, margin):
    # One independent observation per opening group, including for marginal estimates.
    delta = mean(values)
    width = bounds[1] - bounds[0]
    radius = width * math.sqrt(math.log(2 * comparisons / ALPHA) / (2 * len(values)))
    low, high = max(bounds[0], delta - radius), min(bounds[1], delta + radius)
    decision = "inconclusive"
    if low >= -margin and high <= margin:
        decision = "small_at_declared_resolution"
    elif low > 0:
        decision = "positive_local_signal"
    elif high < 0:
        decision = "negative_local_signal"
    return {"effect": delta, "interval": [low, high], "group_count": len(values), "decision": decision}


def extract_run(run, selected):
    games = {}
    for game in run["games"]:
        identifier, color = game["position_id"], game["engine_color"]
        require(identifier in selected and color in ("white", "black"), "unexpected starting position or color")
        entry = selected[identifier]
        require(game["family"] == entry["family"] and game["group"] == entry["group"] and
                game["fen"] == entry["fen"], "starting position does not match registration")
        require((identifier, color) not in games, "repeated opening/color within an arm")
        require(game["termination"] == "normal", "abnormal termination in registered cell")
        result = from_white(game["result"], color)
        require(game["engine_score"] == result, "perspective mismatch in recorded score")
        games[identifier, color] = result
    require(set(games) == {(i, c) for i in selected for c in ("white", "black")}, "incomplete color pairs")
    return games


def summarize(runs, opponents, clocks, positions):
    require(CONTROL in opponents and len(set(opponents)) == len(opponents) and len(opponents) >= 3,
            "require Stockfish and multiple independent registered opponents")
    require(len(set(clocks)) == len(clocks) and len(clocks) >= 2, "require distinct registered clocks")
    group_sets = [{e["family"] for e in positions[s].values()} for s in SPLITS]
    require(all(group_sets) and not group_sets[0].intersection(group_sets[1]), "discovery/holdout grouping overlap")
    fens = [{tuple(e["fen"].split()[:4]) for e in positions[s].values()} for s in SPLITS]
    require(not fens[0].intersection(fens[1]) and all(len(f) == len(positions[s]) for s, f in zip(SPLITS, fens)),
            "duplicate starting position")
    indexed = {}
    for run in runs:
        key = (run["split"], run["opponent"], run["time_control"], run["arm"])
        require(key not in indexed and key[0] in SPLITS, "duplicate or unknown run")
        indexed[key] = extract_run(run, positions[key[0]])
    expected = {(s, o, t, a) for s in SPLITS for o in opponents for t in clocks for a in ("candidate", "baseline")}
    require(set(indexed) == expected, "incomplete registered experiment matrix")
    independent = [o for o in opponents if o != CONTROL]
    # Each practical cell and marginal reports two endpoints. Each objective cell and marginal reports one.
    comparisons = len(SPLITS) * (2 * (len(independent) * len(clocks) + 1) + len(clocks) + 1)
    output = {"status": "complete", "simultaneous_confidence": 1 - ALPHA, "comparisons": comparisons,
              "total_games": sum(len(v) for v in indexed.values()), "splits": {}}
    for split in SPLITS:
        position_map = positions[split]
        group_keys = sorted({pos["family"] for pos in position_map.values()})
        cell_means = {}
        for opp in opponents:
            for tc in clocks:
                groups = defaultdict(lambda: defaultdict(list))
                for arm in ("candidate", "baseline"):
                    for (pos, _color), score in indexed[split, opp, tc, arm].items():
                        group = position_map[pos]["family"]
                        groups[group][arm + "_win"].append(int(score == 1))
                        groups[group][arm + "_score"].append(score)
                cell_means[opp, tc] = {g: {k: mean(vals) for k, vals in row.items()} for g, row in groups.items()}

        def comparison(cells, objective):
            rows = [{k: mean(cell_means[c][g][k] for c in cells) for k in
                     ("candidate_win", "baseline_win", "candidate_score", "baseline_score")} for g in group_keys]
            summary = {}
            for arm in ("candidate", "baseline"):
                summary[arm + "_wdl"] = wdl(v for o, t in cells for v in indexed[split, o, t, arm].values())
                for metric in ("score", "win"):
                    summary[arm + "_" + metric] = mean(row[arm + "_" + metric] for row in rows)
            if objective:
                values = [row["candidate_score"] - 0.5 for row in rows]
                margin = 1 / (1 + 10 ** (-3/400)) - 0.5
                primary = endpoint(values, (-0.5, 0.5), comparisons, margin)
                primary["logistic_elo"] = elo(primary["effect"] + 0.5)
                primary["elo_interval"] = [elo(min(1., max(0., e + 0.5))) for e in primary["interval"]]
                summary["score_vs_even"] = primary
            else:
                for metric in ("win", "score"):
                    values = [r["candidate_" + metric] - r["baseline_" + metric] for r in rows]
                    summary[metric + "_delta"] = endpoint(values, (-1, 1), comparisons, 0.02)
                summary["baseline_win_headroom"] = 1 - summary["baseline_win"]
            return summary

        objective = {t: comparison([(CONTROL, t)], True) for t in clocks}
        objective["aggregate"] = comparison([(CONTROL, t) for t in clocks], True)
        cells = [(op, t) for op in independent for t in clocks]
        practical = [{"opponent": op, "clock": t, **comparison([(op, t)], False)} for op, t in cells]
        output["splits"][split] = dict(positions=list(position_map), groups=group_keys, objective=objective,
                                       practical_cells=practical, practical_aggregate=comparison(cells, False))
    return output


def incomplete_contrasts(runs, opponents, clocks, positions):
    """Retain observed outcomes and bound the held-out experiment under arbitrary missing results."""
    by_cell = {(r["split"], r["opponent"], r["time_control"], r["arm"]): r["games"] for r in runs}
    n_intervals = len(SPLITS) * (2 * ((len(opponents)-1) * len(clocks) + 1) + len(clocks) + 1)
    output = {}
    for split in SPLITS:
        entries = positions[split]
        families = sorted({e["family"] for e in entries.values()})
        estimates = {}
        for o in opponents:
            for t in clocks:
                cells = {}
                meta = {}
                for a in ("baseline", "candidate"):
                    played = by_cell.get((split, o, t, a), [])
                    cells[a] = {(g["position_id"], g["engine_color"]): from_white(g["result"], g["engine_color"])
                                for g in played if g["termination"] == "normal"}
                    meta[a + "_wdl"] = wdl(from_white(g["result"], g["engine_color"]) for g in played)
                    meta[a + "_abnormal"] = sum(g["termination"] != "normal" for g in played)
                objective = o == CONTROL
                meta["registered_positions"] = len(entries)
                included = []
                ranges = defaultdict(lambda: defaultdict(list))
                for i, entry in entries.items():
                    sides = {a: [cells[a].get((i, c)) for c in ("white", "black")]
                             for a in ("candidate", "baseline")}
                    relevant = ("candidate",) if objective else ("candidate", "baseline")
                    if all(None not in sides[a] for a in relevant):
                        included.append(sides)
                    for metric in (("score",) if objective else ("win", "score")):
                        spans = {}
                        for a, two in sides.items():
                            vals = [int(v == 1) if v is not None and metric == "win" else v for v in two]
                            spans[a] = (mean(0 if v is None else v for v in vals),
                                        mean(1 if v is None else v for v in vals))
                        baseline = (0.5, 0.5) if objective else spans["baseline"]
                        lo, hi = spans["candidate"]
                        ranges[metric][entry["family"]].append((lo - baseline[1], hi - baseline[0]))
                meta["normal_complete_openings"] = len(included)
                meta["normal_pair_candidate_wdl"] = wdl(v for sides in included for v in sides["candidate"])
                meta["normal_pair_baseline_wdl"] = (None if objective else
                                                    wdl(v for sides in included for v in sides["baseline"]))
                meta["bounds"] = {}
                for metric, table in ranges.items():
                    meta["bounds"][metric] = [mean(mean(pair[j] for pair in table[f]) for f in families)
                                               for j in (0, 1)]
                for metric in (("score",) if objective else ("win", "score")):
                    func = (lambda v: float(v == 1)) if metric == "win" else float
                    if included:
                        meta["normal_pair_" + metric] = mean(mean(func(v) for v in pair["candidate"])
                                                             - (0.5 if objective else mean(func(v) for v in pair["baseline"]))
                                                             for pair in included)
                    else:
                        meta["normal_pair_" + metric] = None
                estimates[o, t] = meta

        def aggregate(keys, metrics, bounds):
            n = len(families)
            radius = (bounds[1]-bounds[0]) * math.sqrt(math.log(2*n_intervals/ALPHA) / (2*n))
            out = {}
            for metric in metrics:
                lo, hi = [mean(estimates[k]["bounds"][metric][j] for k in keys) for j in (0, 1)]
                out[metric] = {"sample_range": [lo, hi],
                               "uncertainty_envelope": [max(bounds[0], lo-radius), min(bounds[1], hi+radius)]}
            out["normal_complete_openings_across_cells"] = sum(estimates[k]["normal_complete_openings"] for k in keys)
            for arm in ("candidate", "baseline"):
                arm_counts = [estimates[k]["normal_pair_" + arm + "_wdl"] for k in keys]
                out[arm + "_normal_pair_wdl"] = ([sum(v[j] for v in arm_counts) for j in range(3)]
                                                  if all(v is not None for v in arm_counts) else None)
            return out

        def raw_rates(keys, arm):
            by_result = [sum(estimates[k][arm + "_wdl"][j] for k in keys) for j in range(3)]
            n = sum(by_result)
            return {"wdl": by_result, "fractions": [v/n for v in by_result] if n else None}
        output[split] = dict(cells=[dict(opponent=o, clock=t, **v) for (o, t), v in estimates.items()],
                             opening_families=families, objective=aggregate([(CONTROL,t) for t in clocks], ["score"], (-.5,.5)),
                             practical=aggregate([k for k in estimates if k[0] != CONTROL], ["win","score"], (-1.,1.)))
        output[split]["raw_arm_outcomes"] = {
            panel: {arm: raw_rates(keys, arm) for arm in ("candidate", "baseline")}
            for panel, keys in (("head_to_head", [(CONTROL,t) for t in clocks]),
                                ("independent", [k for k in estimates if k[0] != CONTROL]))}
    return output


def range_text(bounds):
    return "[" + ", ".join(f"{100*v:+.1f}" for v in bounds) + "]"


def pct(x):
    return f"{100*x:.1f}%"


def effect_text(e):
    return f"{100*e['effect']:+.1f} [{100*e['interval'][0]:+.1f}, {100*e['interval'][1]:+.1f}]"


def wdl_text(values):
    return "/".join(map(str, values))


def markdown(data):
    lines = ["# Adversarial Stockfish match screen", ""]
    if data["status"] != "complete":
        lines.append("**Both objective strength and practical win-rate effects remain inconclusive.**")
        if "observed" not in data:
            lines.extend(["", f"Evidence verification stopped with {data['reason']}."])
        if "observed" in data:
            lines += ["", f"Completed {data['completed_cells']} of {data['planned_cells']} registered cells "
                      f"without invalid games. The manifest records {data['observed_games']} of "
                      f"{data['planned_games']} games. Fastchess recorded {data['abnormal_games']} abnormal terminations. " 
                      "The registered decision rule requires valid results in every cell.",
                      "", "Each invalid or unobserved game is allowed any legal result in the sample ranges below. "
                      "The 95% simultaneous envelope adds fixed-sample opening-family uncertainty to those limits. "
                      "Values are percentage-point differences. A positive value favors the candidate.",
                      "", "| Split | Endpoint | Compatible sample range | 95% envelope |",
                      "|---|---|---:|---:|"]
            for s in ("holdout", "discovery"):
                out = data["sensitivity"][s]
                for desc, metric in (("H2H score minus 50%", out["objective"]["score"]),
                                     ("Independent-panel win delta", out["practical"]["win"]),
                                     ("Independent-panel score delta", out["practical"]["score"])):
                    lines.append(f"| {s} | {desc} | {range_text(metric['sample_range'])} | "
                                 f"{range_text(metric['uncertainty_envelope'])} |")
            holdout = data["sensitivity"]["holdout"]
            h2h = holdout["objective"]["candidate_normal_pair_wdl"]
            panel = holdout["practical"]
            lines += ["", f"On the holdout, candidate–Stockfish games in complete normal pairs recorded "
                      f"{wdl_text(h2h)} candidate W/D/L. For the matching opening/color quartets against independent engines, "
                      f"the candidate recorded {wdl_text(panel['candidate_normal_pair_wdl'])} and baseline recorded "
                      f"{wdl_text(panel['baseline_normal_pair_wdl'])}. These counts omit unmatched openings."]
            if holdout["objective"]["score"]["uncertainty_envelope"] == [-0.5, 0.5]:
                nfamilies = len(holdout["opening_families"])
                lines += ["", "For the registered direct comparison, the uncertainty envelope covers the entire 0–100% "
                          f"score range, corresponding to an unbounded logistic Elo interval. The {nfamilies} holdout "
                          "families provide too little independent evidence to distinguish a small gain from a regression."]
            lines += ["", "**Raw holdout win/draw/loss percentages**, including forfeits:", "",
                      "| Panel | Arm | Wins | Draws | Losses |", "|---|---|---:|---:|---:|"]
            for panel_name, arms in holdout["raw_arm_outcomes"].items():
                for arm, row in arms.items():
                    percentages = [pct(x) for x in row["fractions"]] if row["fractions"] else ["—"]*3
                    lines.append("| " + " | ".join([panel_name.replace("_"," "), arm, *percentages]) + " |")
            lines += ["", "### Observed cells", "",
                      "W/D/L includes every recorded outcome, including forfeits. Invalid counts follow the candidate/baseline order. "
                      "The complete-pair column counts openings with two normal candidate games for direct Stockfish play, "
                      "or four normal games for a practical comparison. The final column shows candidate score minus 50% "
                      "against Stockfish and candidate-minus-baseline outright win rate against each independent engine. "
                      "These selected-subset differences are descriptive.",
                      "", "| Split | Opponent | Clock | Candidate W/D/L | Baseline W/D/L | Invalid C/B | Complete pairs | Difference (pp) |",
                      "|---|---|---|---:|---:|---:|---:|---:|"]
            for s in ("holdout", "discovery"):
                for r in data["sensitivity"][s]["cells"]:
                    metric = "score" if r["opponent"] == CONTROL else "win"
                    value = r["normal_pair_" + metric]
                    delta = "—" if value is None else f"{100*value:+.1f}"
                    lines.append(f"| {s} | {r['opponent']} | {r['clock']} | {wdl_text(r['candidate_wdl'])} | "
                                 f"{wdl_text(r['baseline_wdl'])} | {r['candidate_abnormal']}/{r['baseline_abnormal']} | "
                                 f"{r['normal_complete_openings']}/{r['registered_positions']} | {delta} |")
            lines.extend(["", "Any all-win baseline cell has zero observed win-rate headroom. "
                          "An apparent shift on the remaining normal subset can also reflect which games timed out. "
                          "Only the holdout was eligible for primary decisions. These curated opening families and engine opponents "
                          "do not establish general Elo or human win rate.", "",
                          "[Registered analysis rules](../../adversarial/analysis-plan.md)"])
        return "\n".join(lines) + "\n"
    primary = data["splits"]["holdout"]
    h2h = primary["objective"]["aggregate"]
    practical = primary["practical_aggregate"]
    labels = dict(inconclusive="inconclusive", positive_local_signal="positive local signal",
                  negative_local_signal="negative local signal", small_at_declared_resolution="small at the declared resolution")
    lines += [f"The **objective-strength screen** is **{labels[h2h['score_vs_even']['decision']]}**. "
              f"Against pinned Stockfish, the holdout candidate scored {pct(h2h['candidate_score'])} "
              f"({wdl_text(h2h['candidate_wdl'])} W/D/L).",
              "", f"The **practical win-rate screen** is **{labels[practical['win_delta']['decision']]}**. "
              f"On the holdout panel, the candidate won {pct(practical['candidate_win'])} and the baseline won "
              f"{pct(practical['baseline_win'])}. The matched difference and simultaneous interval were "
              f"{effect_text(practical['win_delta'])} percentage points.",
              "", f"The complete matrix contains {data['total_games']} games. Positive differences favor the candidate. "
              "W/D/L counts are raw. Rates give equal weight to registered opening groups and cells. "
              "Intervals below are simultaneous 95% intervals in percentage points.", ""]
    for s in ("holdout", "discovery"):
        split = data["splits"][s]
        lines += [f"## {s.title()} · {len(split['positions'])} positions, {len(split['groups'])} opening groups", "",
                  "| Direct vs Stockfish | Candidate W/D/L | SF self-play W/D/L | Candidate score | Score minus 50% [CI] |",
                  "|---|---:|---:|---:|---:|"]
        for tc, row in split["objective"].items():
            lines.append(f"| {tc} | {wdl_text(row['candidate_wdl'])} | {wdl_text(row['baseline_wdl'])} | "
                         f"{pct(row['candidate_score'])} | {effect_text(row['score_vs_even'])} |")
        lines += ["", "| Opponent | Clock | Candidate W/D/L | Baseline W/D/L | Win delta [CI] | Score delta [CI] | Win headroom |",
                  "|---|---|---:|---:|---:|---:|---:|"]
        for row in [*split["practical_cells"], dict(split["practical_aggregate"], opponent="Panel", clock="All")]:
            lines.append(f"| {row['opponent']} | {row['clock']} | {wdl_text(row['candidate_wdl'])} | "
                         f"{wdl_text(row['baseline_wdl'])} | {effect_text(row['win_delta'])} | "
                         f"{effect_text(row['score_delta'])} | {pct(row['baseline_win_headroom'])} |")
        lines.append("")
    lines.extend(["Discovery is exploratory. A baseline win rate near 100% limits measurable improvement on that panel. "
                  "The independent unit is the opening group. Repeating a group at additional clocks or against more opponents "
                  "adds measurements within that group. The intervals assume independence across groups and make no "
                  "representativeness claim for the curated positions. A practical win-rate difference on this panel "
                  "is specific to the registered opponents, openings and clocks. General Elo requires broader validation.",
                  "", "[Registered analysis rules](../../adversarial/analysis-plan.md)"])
    return "\n".join(lines) + "\n"


def fen_identity(fen):
    fields = fen.split()
    require(len(fields) == 6, "expected full six-field FEN")
    if fields[3] != "-":
        # Normalize an en-passant target only when an independent rules implementation
        # confirms that no legal capture exists. Pinned pawns make adjacency insufficient.
        try:
            import chess
        except ImportError as exc:
            raise ValueError("install python-chess to normalize a FEN with an en-passant target") from exc
        fields = chess.Board(fen).fen(en_passant="legal").split()
    return " ".join(fields)


def verify_pgn(directory, run):
    path = inside(directory, run["pgn"])
    require(digest(path) == run["pgn_sha256"], "PGN checksum mismatch")
    observed = {}
    for block in re.split(r"\n\s*\n", path.read_text()):
        if not block.lstrip().startswith("["):
            continue
        tags = dict(re.findall(r'^\[(\w+) "([^"\n]*)"\]\s*$', block.strip(), re.M))
        arm, opponent = "arm_" + run["arm"], "vs_" + run["opponent"]
        require({tags["White"], tags["Black"]} == {arm, opponent}, "wrong engine labels in PGN")
        require(tags["TimeControl"] == run["time_control"], "wrong clock in PGN")
        color = "white" if tags["White"] == arm else "black"
        key = (int(tags["Round"]), color)
        require(key not in observed, "repeated color/round in PGN")
        observed[key] = (fen_identity(tags["FEN"]), tags["Result"], tags["Termination"])
    require(len(observed) == len(run["games"]), "unexpected PGN game count")
    for game in run["games"]:
        key = (game["round"], game["engine_color"])
        require(observed.pop(key, None) == (fen_identity(game["fen"]), game["result"], game["termination"]),
                "PGN and recorded observation differ")
    require(not observed, "unaccounted PGN games")


def analyze(directory):
    registration = directory / "preregistration.json"
    matches = directory / "matches.json"
    protocol = json.loads(registration.read_text())
    ledger = json.loads(matches.read_text())
    require(ledger["preregistration_sha256"] == digest(registration), "registration checksum mismatch")
    require(protocol["analysis_plan_sha256"] == digest(PLAN), "analysis plan checksum mismatch")
    corpus_path = PLAN.with_name("positions.json")
    require(protocol["corpus_sha256"] == ledger["corpus_sha256"] == digest(corpus_path), "corpus checksum mismatch")
    corpus = {e["id"]: e for e in json.loads(corpus_path.read_text())["entries"]}
    positions = {}
    for s in SPLITS:
        chosen = protocol["selected_positions"][s]
        positions[s] = {e["id"]: e for e in chosen}
        require(len(chosen) == len(positions[s]) and chosen, "invalid or empty split registration")
        for entry in chosen:
            original = corpus[entry["id"]]
            require(original["split"] == s and all(original[k] == entry[k] for k in ("group", "family", "fen")),
                    "registered position absent from the declared corpus split")
    for key in ("experiment", "baseline", "candidate", "opponents", "time_controls", "planned_games"):
        require(protocol[key] == ledger[key], "registered provenance mismatch: " + key)
    require(protocol["baseline"]["commit"] != protocol["candidate"]["commit"] and
            protocol["opponents"][CONTROL]["sha256"] == protocol["baseline"]["binary_sha256"],
            "invalid H2H control identity")
    for key in ("compiler", "compiler_binary_sha256", "arch", "hardware", "build_flags", "networks"):
        require(protocol["baseline"][key] == protocol["candidate"][key], "mismatched builds: " + key)
    allowed = {(c["split"], c["opponent"], c["time_control"], c["arm"]) for c in protocol["cells"]}
    observed_cells = set()
    observation = []
    for run in ledger["runs"]:
        key = tuple(run[k] for k in ("split", "opponent", "time_control", "arm"))
        require(key in allowed and key not in observed_cells, "unexpected or duplicate registered cell")
        observed_cells.add(key)
        if run["pgn_sha256"] and run["games"]:
            verify_pgn(directory, run)
        else:
            require(not run["games"] and run["status"] != "complete", "missing PGN evidence for recorded games")
        if run["status"] == "complete":
            extract_run(run, positions[run["split"]])
        observation.append({k: run[k] for k in ("split", "opponent", "time_control", "arm", "status")})
        observation[-1]["normal_wdl"] = wdl(from_white(g["result"], g["engine_color"])
                                            for g in run["games"] if g["termination"] == "normal")
    if ledger["status"] == "complete":
        require(observed_cells == allowed and all(r["status"] == "complete" for r in ledger["runs"]),
                "registered cells disagree with completion status")
        data = summarize(ledger["runs"], protocol["opponents"], protocol["time_controls"], positions)
        require(data["total_games"] == protocol["planned_games"], "expected registered game count")
    else:
        data = {"status": "unverified_or_incomplete", "reason": "campaign status " + ledger["status"],
                "observed": observation, "planned_games": protocol["planned_games"], "planned_cells": len(allowed),
                "completed_cells": sum(r["status"] == "complete" for r in ledger["runs"]),
                "observed_games": sum(len(r["games"]) for r in ledger["runs"])}
        data["sensitivity"] = incomplete_contrasts(ledger["runs"], protocol["opponents"], protocol["time_controls"], positions)
        data["abnormal_games"] = sum(g["termination"] != "normal" for r in ledger["runs"] for g in r["games"])
    data["matches_sha256"] = digest(matches)
    data["preregistration_sha256"] = digest(registration)
    data["analysis_plan_sha256"] = digest(PLAN)
    data["corpus_sha256"] = digest(corpus_path)
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path, help="folder containing preregistration.json, matches.json and PGNs")
    p.add_argument("--bench", type=Path, help="paired speed/bench evidence bound to the registered builds")
    p.add_argument("--json", type=Path, help="write the machine-readable report")
    p.add_argument("--markdown", type=Path, help="write the human-readable report")
    args = p.parse_args()
    try:
        data = analyze(args.directory)
        if args.bench:
            reg = json.loads((args.directory / "preregistration.json").read_text())
            bench = json.loads(args.bench.read_text())
            for side in ("baseline", "candidate"):
                require(bench[side] == reg[side]["commit"] and
                        bench["worker"][side]["build"]["binary_sha256"] == reg[side]["binary_sha256"],
                        "speed evidence does not match registered builds")
            speeds = {side: [r["nps"] for r in bench["runs"] if r["side"] == side]
                      for side in ("baseline", "candidate")}
            require(all(len(v) >= 2 and min(v) > 0 for v in speeds.values()), "missing repeated speed measurements")
            data["speed"] = {"nps": speeds, "change_pct": 100*(mean(speeds["candidate"])/mean(speeds["baseline"])-1),
                             "bench_sha256": digest(args.bench)}
    except (OSError, KeyError, IndexError, ValueError, TypeError) as exc:
        data = {"status": "unverified_or_incomplete", "reason": str(exc)}
    if args.json:
        args.json.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    body = markdown(data)
    if "speed" in data:
        speed = data["speed"]
        body += ("\n## Search speed\n\n"
                 f"Registered candidate and baseline averaged {mean(speed['nps']['candidate']):,.0f} and "
                 f"{mean(speed['nps']['baseline']):,.0f} nodes per second respectively "
                 f"({speed['change_pct']:+.2f}% candidate change). These short benchmarks on a shared worker "
                 "visited different search trees. [Per-run evidence](../EXP-TACTICAL-QUIETCHECK-001/bench.json)\n")
    if args.markdown:
        args.markdown.write_text(body)
    else:
        print(body, end="")
    return 0 if data["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
