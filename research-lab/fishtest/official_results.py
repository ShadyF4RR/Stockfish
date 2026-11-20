"""Read and verify public Fishtest results. All network operations are unauthenticated GETs."""
import hashlib
import json
import math
import re
import urllib.request
from datetime import datetime, timezone

HOST = "https://tests.stockfishchess.org"
RUN_ID = r"[0-9a-f]{24}"
STANDARD = {"stc": "10+0.1", "ltc": "60+0.6"}
API_PATH = re.compile(rf"/api/(?:get_(?:elo|run)/{RUN_ID}|active_runs|finished_runs\?page=[1-9][0-9]{{0,3}})")
MAX_BYTES = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check(ok, message):
    if not ok:
        raise ValueError(message)


def fetch_json(path):
    """Read one bounded, allowlisted endpoint using system TLS verification."""
    check(isinstance(path, str) and API_PATH.fullmatch(path), "unsupported Fishtest API path")
    req = urllib.request.Request(HOST + path, headers={"Accept": "application/json",
                                                      "User-Agent": "Stockfish-Research-Lab/1"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=15) as response:
            check(response.status == 200 and response.geturl() == req.full_url, "unexpected Fishtest response")
            check(response.headers.get_content_type() == "application/json", "expected Fishtest JSON")
            raw = response.read(MAX_BYTES + 1)
    except OSError as e:
        raise ValueError(f"official Fishtest read unavailable: {e}") from e
    check(len(raw) <= MAX_BYTES, "Fishtest response exceeds byte limit")
    value = json.loads(raw)
    check(isinstance(value, dict), "expected a Fishtest object")
    return value


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def canonical_id(ref):
    check(isinstance(ref, str), "expected official run ID or HTTPS view URL")
    match = re.fullmatch(rf"(?:{re.escape(HOST)}/tests/view/)?({RUN_ID})", ref)
    check(match, "expected official run ID or HTTPS view URL")
    return match.group(1)


def check_settings(args, packet):
    stage = packet["stage"]
    check(stage in STANDARD and packet["tc"] == STANDARD[stage], "unexpected standard stage time control")
    check("spsa" not in args, "SPSA results cannot verify a functional SPRT")
    for field, key in (("resolved_base", "base_sha"), ("resolved_new", "candidate_sha")):
        sha = packet[key]
        check(isinstance(sha, str) and re.fullmatch("[0-9a-f]{40}", sha) and args[field] == sha,
              f"official revision mismatch: {field}")
    check(packet["base_sha"] != packet["candidate_sha"], "baseline and candidate are identical")
    for field, key in (("base_signature", "base_signature"), ("new_signature", "candidate_signature")):
        bench = str(packet[key])
        check(re.fullmatch("[1-9][0-9]*", bench) and args[field] == bench, f"official bench mismatch: {field}")
    check(args["tests_repo"].rstrip("/").casefold() == packet["repo"].rstrip("/").casefold(),
          "official source repository mismatch")
    for field in ("tc", "threads", "base_options", "new_options", "book"):
        check(type(args[field]) is type(packet[field]) and args[field] == packet[field],
              f"official settings mismatch: {field}")
    check(args["new_tc"] == packet["tc"] and str(args["book_depth"]) == str(packet["book_depth"]),
          "official candidate TC or book depth mismatch")
    check(args["adjudication"] is packet.get("adjudication", True), "official adjudication mismatch")
    for opt in ("arch_filter", "compiler"):
        check(args.get(opt, "") == packet.get(opt, ""), f"unexpected official restriction: {opt}")
    if "server_sprt_game_limit" in packet:
        check(args["num_games"] == packet["server_sprt_game_limit"], "official game ceiling mismatch")
    if packet.get("username"):
        check(args["username"] == packet["username"], "official submitting account mismatch")
    sprt = args["sprt"]
    check(sprt["elo_model"] == packet["elo_model"] == "normalized", "unexpected SPRT model")
    for key in ("elo0", "elo1", "alpha", "beta"):
        check(numeric(sprt[key]) and sprt[key] == packet[key], f"official SPRT mismatch: {key}")
    check(sprt["alpha"] == sprt["beta"] == 0.05 and sprt["elo0"] < sprt["elo1"], "unexpected SPRT design")
    for key, bound in (("lower_bound", -math.log(19)), ("upper_bound", math.log(19))):
        check(numeric(sprt[key]) and math.isclose(sprt[key], bound), f"unexpected SPRT {key}")
    check(numeric(sprt["llr"]) and sprt["state"] in ("", "accepted", "rejected"), "invalid SPRT state")
    return {key: sprt[key] for key in ("state", "llr", "lower_bound", "upper_bound", "elo0", "elo1",
                                       "elo_model", "alpha", "beta")}


def summarize(run_id, packet, run):
    """Check one service snapshot against the predeclared test; reject mismatches."""
    check(run["_id"] == run_id, "official response has a different run ID")
    args = run["args"]
    sprt = check_settings(args, packet)
    for flag in ("finished", "failed", "deleted", "approved", "is_green"):
        check(type(run[flag]) is bool, f"missing or invalid official flag: {flag}")
    results = run["results"]
    wdl = {key: results[key] for key in ("wins", "draws", "losses")}
    penta = results["pentanomial"]
    check(isinstance(penta, list) and len(penta) == 5, "missing five game-pair result counts")
    check(all(type(count) is int and count >= 0 for count in [*wdl.values(), *penta]),
          "invalid official game count")
    games = sum(wdl.values())
    check(games == 2 * sum(penta) and wdl["wins"] - wdl["losses"] == 2*penta[4] + penta[3] - penta[1] - 2*penta[0]
          and penta[3] + penta[1] <= wdl["draws"] <= penta[3] + 2*penta[2] + penta[1],
          "official WDL and game-pair counts disagree")

    status = "inconclusive"
    if run["failed"] or sprt["state"] == "rejected":
        status = "failed"
    elif not run["finished"] and not run["deleted"] and sprt["state"] == "":
        status = "active"
    elif (run["finished"] and run["approved"] and not run["deleted"] and run["is_green"]
          and games > 0 and sprt["state"] == "accepted"):
        # The server applies overshoot corrections to its nominal LLR boundaries.
        status = "passed"

    canonical = json.dumps(run, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {
        "official_snapshot_sha256": hashlib.sha256(canonical).hexdigest(),
        "status": status, "run_id": run_id, "stage": packet["stage"], "url": HOST + "/tests/view/" + run_id,
        "base_sha": args["resolved_base"], "candidate_sha": args["resolved_new"],
        "base_signature": int(args["base_signature"]), "candidate_signature": int(args["new_signature"]),
        "wdl": wdl, "games": games, "pentanomial": penta, "sprt": sprt,
        "settings": {k: args[k] for k in ("tc", "new_tc", "threads", "base_options", "new_options", "book",
                                           "book_depth", "adjudication", "tests_repo", "username")},
        "approved": run["approved"], "finished": run["finished"], "deleted": run["deleted"],
        "failed": run["failed"], "elo": None, "ci": None, "elo_scale": "logistic",
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def inspect_run(ref, packet):
    """Fetch run identity/state and official SPRT Elo/95% CI when games are available."""
    run_id = canonical_id(ref)
    try:
        report = summarize(run_id, packet, fetch_json("/api/get_run/" + run_id))
        if report["games"] == 0:
            return report
        # get_elo returns a fresh run plus its computed statistics. Never mix snapshots.
        run = fetch_json("/api/get_elo/" + run_id)
        report = summarize(run_id, packet, run)
        elo = run["elo"]
        ci = elo["ci"]
        check(numeric(elo["elo"]) and isinstance(ci, list) and len(ci) == 2 and all(map(numeric, ci))
              and ci[0] <= elo["elo"] <= ci[1], "invalid official Elo estimate or interval")
        report.update(elo=elo["elo"], ci=ci)
        return report
    except (KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"missing or unsupported official Fishtest data: {e}") from e
