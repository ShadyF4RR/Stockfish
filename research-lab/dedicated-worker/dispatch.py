#!/usr/bin/env python3
"""Serialize approved Stockfish tests across the dedicated PC and cloud workers."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "continuous"))
from coordinator_queue import QueueStore, existing_fork_git_auth, require

FORK = "ShadyF4RR/Stockfish"
REF = "refs/heads/research/testing-queue"
BASE = "refs/heads/research/baseline"
ID = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,62}")
OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
FILE = re.compile(r"[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*")
FIELDS = {"experiment","baseline","candidate","placement","pairs","seed","time_control","max_minutes"}
REQUEST = ("experiment", "baseline", "candidate", "pairs", "seed", "time_control")
CALIBRATION_CAP = 16
LIMITS = {"daily": {"wall_minutes": 220, "games": 232}, "lifetime": {"wall_minutes": 1100, "games": 1160}}


def validate(m):
    require(isinstance(m, dict) and set(m) == FIELDS, "unexpected request fields")
    require(ID.fullmatch(m["experiment"]) and SHA.fullmatch(m["baseline"]) and SHA.fullmatch(m["candidate"])
            and m["baseline"] != m["candidate"], "invalid experiment ID or immutable revisions")
    require(m["placement"] in ("pc-pilot","preferred","cloud") and m["time_control"] == "4+0.04",
            "unapproved placement or clock")
    for k, lo, hi in (("pairs",1,50),("seed",1,2**32-1),("max_minutes",1,110)):
        require(type(m[k]) is int and lo <= m[k] <= hi, "invalid " + k)
    return m


def stamp(now):
    return now.isoformat(timespec="seconds")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def first_state():
    return dict(schema=1, paused=None, jobs={}, active=None, enrolled_pc=None, preferred_pc=None, limits=LIMITS)


class Store(QueueStore):
    def fetch_exact(self, ref, sha):
        self.git("fetch", "--depth=2", "--no-tags", "--recurse-submodules=no", self.remote, ref)
        observed = self.git("rev-parse", "FETCH_HEAD^{commit}").decode().strip()
        require(observed == sha, "published ref moved or different commit at " + ref)

    def parent_allocation(self, m, now):
        identifier = os.environ.get("RESEARCH_PARENT_ID", "")
        lease = os.environ.get("RESEARCH_PARENT_LEASE", "")
        if not ID.fullmatch(identifier) or not re.fullmatch("[0-9a-f]{32}",lease):
            return None
        self.git("fetch","--depth=1","--no-tags","--recurse-submodules=no",self.remote,"refs/heads/research/queue")
        sha = self.git("rev-parse","FETCH_HEAD^{commit}").decode().strip()
        upstream = json.loads(self.blob(sha,"queue.json",24*1024*1024))
        require(upstream.get("schema")==1 and upstream["rehearsal"] is None and not upstream["paused"],
                "parent queue not available for production allocation")
        item = next((i for i in upstream["items"] if i["id"]==identifier and i["status"]=="running"),None)
        if not item or not item["attempts"] or item["baseline"] != m["baseline"]:
            return None
        a = item["attempts"][-1]
        if a["lease"] != lease or a["budget"]["cost_usd"] != 0:
            return None
        expires=datetime.fromisoformat(a["expires_at"])
        elapsed=max(0,math.ceil((now-datetime.fromisoformat(a["started_at"])).total_seconds()/60))
        if (expires-now).total_seconds() < m["max_minutes"]*60 or a["budget"]["runner_minutes"]-elapsed < m["max_minutes"]:
            return None
        for ck in a["checkpoints"]:
            payload=ck["details"]
            if (ck["stage"]=="testing-allocation" and isinstance(payload,dict)
                    and all(payload.get(k)==m[k] for k in ("experiment","baseline","candidate"))
                    and type(payload.get("max_minutes")) is int and payload["max_minutes"] >= m["max_minutes"]):
                return {"id":identifier,"lease_sha256":sha256(lease.encode()),"allocation_minutes":payload["max_minutes"]}
        return None

    def verify_sources(self, m):
        self.fetch_exact(BASE, m["baseline"])
        self.fetch_exact("refs/heads/experiment/" + m["experiment"], m["candidate"])
        parents = self.git("rev-list", "--parents", "-1", m["candidate"]).decode().split()
        require(parents == [m["candidate"], m["baseline"]], "candidate must be one child of approved baseline")
        raw = self.git("diff", "--raw", "--no-renames", "-z", m["baseline"], m["candidate"], "--").split(b"\0")
        require(len(raw) > 1 and len(raw) % 2 == 1 and raw[-1] == b"", "empty or invalid candidate patch")
        for mode, name in zip(raw[:-1:2], raw[1:-1:2]):
            require(mode.startswith(b":100644 100644 ") and
                    re.fullmatch(rb"src/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.(?:cpp|h)", name),
                    "candidate must change existing regular engine sources only")

    def blob(self, sha, path, limit):
        require(FILE.fullmatch(path) and not {".",".."}.intersection(PurePosixPath(path).parts),
                "unsafe evidence path")
        entry = self.git("ls-tree", "-z", sha, "--", path)
        match = re.fullmatch(rb"100644 blob ([a-f0-9]{40})\t" + re.escape(path.encode()) + b"\0", entry)
        require(match, "missing regular evidence file: " + path)
        oid = match.group(1).decode()
        require(int(self.git("cat-file", "-s", oid)) <= limit, "oversize evidence file: " + path)
        return self.git("cat-file", "blob", oid)


def pgn_counts(data, clock, pairs):
    by_round = {}
    wdl = [0,0,0]
    for block in re.split(r"\n\s*\n", data.decode().replace("\r\n","\n")):
        if not block.lstrip().startswith("["):
            continue
        tags = dict(re.findall(r'^\[(\w+) "([^"\n]*)"\]\s*$',block.strip(),re.M))
        white = tags["White"]
        require({white,tags["Black"]} == {"baseline","candidate"} and
                tags["Termination"] == "normal" and tags["TimeControl"] == clock, "invalid game in uploaded PGN")
        n = int(tags["Round"])
        fen = tags.get("FEN","startpos")
        require(white not in by_round.setdefault(n,{}) and 1 <= n <= pairs, "duplicate or unexpected game")
        by_round[n][white] = fen
        score = {"1-0":2,"0-1":0,"1/2-1/2":1}[tags["Result"]]
        wdl[2-score if white == "candidate" else score] += 1
    require(len(by_round)==pairs and all(set(colors)=={"baseline","candidate"} and len(set(colors.values()))==1
            for colors in by_round.values()), "opening or color pairing incomplete")
    return wdl


def verify_evidence(store, m, receipt):
    commit = receipt["evidence_commit"]
    require(SHA.fullmatch(commit), "expected evidence commit SHA")
    ref = "refs/heads/research/testing-evidence/" + m["experiment"] + "/" + receipt["lease"]
    store.fetch_exact(ref, commit)
    uploaded = json.loads(store.blob(commit, "receipt.json", 128_000))
    require(uploaded == {k:v for k,v in receipt.items() if k != "evidence_commit"}, "remote receipt mismatch")
    digests = receipt["sha256"]
    required = {"request.json","result.json","calibration.json","match/match.json","match/games.pgn"}
    require(isinstance(digests,dict) and 4 <= len(digests) <= 128 and required <= digests.keys(),
            "missing evidence digests")
    blobs = {}
    size = 0
    for path, digest in digests.items():
        require(DIGEST.fullmatch(digest), "invalid evidence digest")
        data = store.blob(commit, path, 16_000_000)
        size += len(data)
        require(size <= 48_000_000 and sha256(data)==digest, "evidence size or digest mismatch")
        blobs[path] = data
    result = json.loads(blobs["result.json"])
    require(json.loads(blobs["request.json"]) == {k:m[k] for k in REQUEST} and
            digests["request.json"] == result["request_sha256"], "executor request differs from approved manifest")
    require(isinstance(result["evidence_sha256"],dict) and all(digests.get(k)==v for k,v in result["evidence_sha256"].items()),
            "executor evidence inventory disagrees with uploaded receipt")
    require(result["status"] == "screened" and result["rehearsal"] is False and
            result["worker"] == receipt["worker"] and result["baseline_sha"] == m["baseline"] and
            result["candidate_sha"] == m["candidate"], "engine evidence does not match claim")
    require(result["games"] == receipt["games"] == 2*m["pairs"] and
            result["actual_games"] == result["games"] + result["calibration_games"] and
            result["calibration_games"] == receipt["calibration_games"], "game budget or count mismatch")
    require(result["decision"] in ("reject","inconclusive","escalate"), "unknown screening decision")
    c = json.loads(blobs["calibration.json"])
    require(receipt["calibrated"] is True and c["status"]=="complete" and c["valid"] is True and
            c["selected"] == result["selected_layout"] and c["games"] == result["calibration_games"]
            and 0 < c["games"] <= CALIBRATION_CAP, "incomplete calibration")
    layouts = c["layouts"]
    require(len(layouts) >= 1 and sum(l["games"] for l in layouts) == c["games"] and
            any(all(l[k]==c["selected"][k] for k in ("threads","concurrency")) for l in layouts),
            "selected layout lacks completed trials")
    def verify_match(file, pgn, pairs):
        match = json.loads(blobs[file])
        require(match["status"] == "screened" and match["stage"] == "C" and
                match["games"] == 2*pairs and match["settings"]["pairs"] == pairs
                and digests[pgn] == match["pgn_sha256"], "incomplete or unbound match")
        wdl = pgn_counts(blobs[pgn], match["settings"]["time_control"], pairs)
        require(wdl == [match["wins"],match["draws"],match["losses"]], "WDL disagrees with PGN")
        return match
    for l in layouts:
        require(l["valid"] is True and type(l["games"]) is int and l["games"]>0 and l["games"]%2==0
                and l["games_per_hour"] > 0 and l["wall_seconds"] > 0, "invalid calibration trial")
        trial = verify_match(l["evidence"], l["pgn"], l["games"]//2)
        require(trial["candidate"]["commit"] == trial["baseline"]["commit"] == m["baseline"]
                and all(trial["settings"][k] == l[k] for k in ("threads","concurrency")),
                "calibration did not use the registered baseline and layout")
    match = verify_match("match/match.json","match/games.pgn",m["pairs"])
    require(match["baseline"]["commit"]==m["baseline"] and match["candidate"]["commit"]==m["candidate"] and
            match["settings"]["time_control"]==m["time_control"] and match["settings"]["seed"]==m["seed"] and
            all(match["settings"][k]==c["selected"][k] for k in ("threads","concurrency")) and
            match["decision"]==result["decision"], "unregistered production match settings")
    return commit


def usage(state, now):
    day = stamp(now)[:10]
    total = {k:{"wall_minutes":0,"games":0} for k in LIMITS}
    for job in state["jobs"].values():
        for a in job["attempts"]:
            charge = a.get("actual", a["reserved"])
            for period in LIMITS:
                if period=="daily" and not a["claimed_at"][:10] <= day <= a.get("ended_at",stamp(now))[:10]:
                    continue
                for key,n in charge.items(): total[period][key] += n
    return total


def fits(state, amount, now):
    used = usage(state, now)
    return all(used[p][k]+n<=state["limits"][p][k] for p in LIMITS for k,n in amount.items())


def envelope(job, a, action):
    return dict(action=action, id=job["manifest"]["experiment"], experiment=job["manifest"]["experiment"],
                manifest=job["manifest"], attempt=a["attempt"], lease=a["lease"], owner=a["owner"],
                worker=a["worker"], started="started_at" in a, expires_at=a["expires_at"])


def bound(state, x):
    job = state["jobs"].get(x.id)
    require(job and job["attempts"], "unknown experiment or attempt")
    a = job["attempts"][-1]
    require((x.owner,x.lease,x.attempt) == (a["owner"],a["lease"],a["attempt"]), "owner/attempt/lease mismatch")
    return job,a


def change(store, state, x, now):
    action = x.action
    if action == "init" and getattr(x,"pc_owner",None):
        require(state["enrolled_pc"] in (None,x.pc_owner), "a different PC is already enrolled")
        state["enrolled_pc"] = x.pc_owner
    if action == "schedule":
        # A cron tick publishes one durable delivery. The restricted PC pulls it.
        # Existing deliveries and leases survive retries and runner disconnects.
        owner = state["enrolled_pc"]
        ready = state.get("ready_pc") or {}
        decision = "waiting_for_pc"
        settings = json.loads((Path(__file__).resolve().parents[1] / "config/agents.yaml").read_text())
        if settings["autonomous_execution_enabled"] is not True or settings["paid_api_budget_usd"] != 0:
            decision = "disabled"
        elif state["paused"] or state["active"]:
            decision = "paused" if state["paused"] else "campaign_active"
        elif owner and ready.get("owner") == owner and now - datetime.fromisoformat(ready["at"]) < timedelta(minutes=15):
            decision = "no_eligible_match"
            for job in sorted(state["jobs"].values(), key=lambda j: j["submitted_at"]):
                m = job["manifest"]
                if job["status"] != "pending" or m["placement"] == "cloud":
                    continue
                if not fits(state, {"wall_minutes":m["max_minutes"], "games":2*m["pairs"]+CALIBRATION_CAP}, now):
                    continue
                if job.get("delivery", {}).get("owner") != owner:
                    job["delivery"] = {"owner":owner, "run":x.run, "at":stamp(now)}
                decision = "dispatched " + m["experiment"]
                break
        state["last_schedule"] = dict(run=x.run, event=x.event, at=stamp(now), decision=decision)
        if x.event == "schedule":
            state["last_cron"] = dict(state["last_schedule"])
        return state["last_schedule"]
    if action == "ready":
        if x.owner != state["enrolled_pc"]:
            return {"action":"waiting_for_enrollment"}
        prior = state.get("ready_pc") or {}
        if prior.get("owner") != x.owner or now-datetime.fromisoformat(prior["at"]) >= timedelta(minutes=8):
            state["ready_pc"] = {"owner":x.owner, "at":stamp(now)}
        return {"action":"ready"}
    if action == "enroll-pc":
        if state["enrolled_pc"] == x.owner:
            return {"enrolled_pc":x.owner,"preferred_pc":state["preferred_pc"]}
        state.pop("ready_pc",None)
        require(not state["active"], "reconcile the active job before changing enrollment")
        if x.owner != state["enrolled_pc"]:
            state.update(enrolled_pc=x.owner,preferred_pc=None)
        return {"enrolled_pc":state["enrolled_pc"],"preferred_pc":state["preferred_pc"]}
    if action in ("status","init"):
        return dict(state=state, usage=usage(state,now))
    if action in ("pause","resume"):
        state["paused"] = x.reason if action=="pause" else None
        return {"paused":state["paused"]}
    if action=="submit":
        m = validate(x.payload)
        key = m["experiment"]
        if key in state["jobs"]:
            require(state["jobs"][key]["manifest"]==m, "experiment already has a different manifest")
            return {"experiment":key,"replayed":True}
        require(not any(all(j["manifest"][k]==m[k] for k in ("baseline","candidate","pairs","seed","time_control"))
                        for j in state["jobs"].values()), "duplicate campaign")
        store.verify_sources(m)
        state["jobs"][key] = dict(manifest=m, status="pending", submitted_at=stamp(now), attempts=[])
        return {"experiment":key,"status":"pending"}
    if action=="claim":
        if x.worker == "pc" and x.owner != state["enrolled_pc"]:
            return {"action":"idle","reason":"pc_not_enrolled"}
        active = state["active"]
        if active:
            j = state["jobs"][active]
            a = j["attempts"][-1]
            if (a["worker"],a["owner"]) == (x.worker,x.owner):
                return envelope(j,a,"resume")
            return {"action":"idle","reason":"global_slot_occupied"}
        if state["paused"]: return {"action":"idle","reason":"paused"}
        for j in sorted(state["jobs"].values(),key=lambda j:(j["submitted_at"],j["manifest"]["experiment"])):
            m=j["manifest"]
            if j["status"]!="pending": continue
            preferred=state["preferred_pc"] if m["placement"]=="preferred" else None
            worker = "cloud" if m["placement"] == "cloud" else "pc"
            if worker == "pc" and j.get("delivery", {}).get("owner") != x.owner:
                continue
            if x.worker != worker or (preferred and x.owner!=preferred["owner"]): continue
            parent = store.parent_allocation(m,now) if worker == "cloud" else None
            if worker == "cloud" and not parent:
                continue
            reserve={"wall_minutes":m["max_minutes"], "games":2*m["pairs"]+CALIBRATION_CAP}
            if not fits(state,reserve,now): continue
            a=dict(attempt=len(j["attempts"])+1,lease=uuid.uuid4().hex,owner=x.owner,worker=x.worker,
                   claimed_at=stamp(now),expires_at=stamp(now+timedelta(minutes=m["max_minutes"])),
                   reserved=reserve,parent=parent)
            j["attempts"].append(a)
            j["status"]="active"
            state["active"]=m["experiment"]
            return envelope(j,a,"run")
        return {"action":"idle","reason":"no_eligible_or_budgeted_job"}
    job,a=bound(state,x)
    m=job["manifest"]
    if action in ("complete","fail") and "receipt_digest" in a:
        require(a["receipt_digest"]==sha256(json.dumps(x.payload,sort_keys=True).encode()),"conflicting terminal receipt")
        return dict(action="already_recorded", status=job["status"], experiment=x.id)
    require(state["active"]==x.id and job["status"]=="active", "attempt is no longer active")
    if action=="record-start":
        if "started_at" in a:
            return envelope(job,a,"recover_only")
        require(not state["paused"] and datetime.fromisoformat(a["expires_at"])>now, "paused or expired; reconcile")
        if a["worker"]=="cloud":
            parent=store.parent_allocation(m,now)
            require(parent and parent==a["parent"], "parent allocation no longer available")
        store.verify_sources(m)
        a["started_at"]=stamp(now)
        return envelope(job,a,"start_authorized")
    if action=="reconcile":
        require(x.confirm_stopped and x.reason.strip(), "confirm the old worker is stopped and reconcile its evidence")
        # A started campaign is terminal after reconciliation. Only a demonstrably
        # unstarted slot may return to pending for a fresh, fenced attempt.
        retry=x.never_started and "started_at" not in a
        require(not x.never_started or retry, "start already recorded; never replay games")
        a.update(ended_at=stamp(now),reconciled=x.reason)
        if retry: a["actual"]={"wall_minutes":0,"games":0}
        job["status"]="pending" if retry else "invalid"
        state["active"]=None
        return {"status":job["status"]}
    receipt=x.payload
    require(all(receipt.get(k)==v for k,v in {
        "experiment":x.id,"baseline":m["baseline"],"candidate":m["candidate"],"worker":a["worker"],
        "owner":a["owner"],"attempt":a["attempt"],"lease":a["lease"]}.items()), "receipt identity mismatch")
    if action == "fail" and "started_at" in a:
        require(receipt.get("stopped") is True, "ambiguous live process; keep the slot until reconciliation")
    expected="screened" if action=="complete" else "invalid"
    require(receipt.get("outcome")==expected, "receipt outcome mismatch")
    if action=="complete":
        require("started_at" in a and receipt["rehearsal"] is False, "missing actual run start")
        require(type(receipt["wall_minutes"]) in (int,float) and math.isfinite(receipt["wall_minutes"]) and
                receipt["wall_minutes"]>0, "invalid wall usage")
        commit=verify_evidence(store,m,receipt)
        a["actual"]={"wall_minutes":math.ceil(receipt["wall_minutes"]),"games":receipt["games"]+receipt["calibration_games"]}
        if any(a["actual"][k] > a["reserved"][k] for k in a["actual"]): state["paused"]="actual usage exceeded reservation"
        if a["worker"]=="pc" and not state["paused"]:
            state["preferred_pc"]={"owner":a["owner"],"experiment":x.id,"evidence_commit":commit}
    a.update(ended_at=stamp(now),outcome=expected,receipt_digest=sha256(json.dumps(receipt,sort_keys=True).encode()),
             evidence_commit=receipt.get("evidence_commit"))
    job["status"]=expected
    state["active"]=None
    return {"status":expected,"preferred_pc":state["preferred_pc"],"paused":state["paused"]}


def transact(store,x):
    for race in range(3):
        tip,state=store.read()
        if state is None:
            require(x.action=="init","initialize the testing queue before use")
            state=first_state()
        require(state["schema"]==1 and state["limits"]==LIMITS,"unrecognized testing queue policy")
        before=json.dumps(state,sort_keys=True)
        reply=change(store,state,x,datetime.now(timezone.utc))
        if tip and json.dumps(state,sort_keys=True)==before: return reply
        try:
            store.save(tip,state,x.action)
            return reply
        except RuntimeError:
            if race==2 or store.read()[0]==tip: raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    cmd=p.add_subparsers(dest="action",required=True)
    for name in ("init","status","pause","resume","schedule","ready","enroll-pc","submit","claim","record-start","complete","fail","reconcile"):
        sub=cmd.add_parser(name)
        sub.add_argument("--out",type=Path)
        if name=="init": sub.add_argument("--pc-owner")
        if name=="submit": sub.add_argument("--manifest",type=Path,required=True)
        if name=="claim": sub.add_argument("--worker",choices=("pc","cloud"),required=True)
        if name == "schedule":
            sub.add_argument("--run", required=True, type=int)
            sub.add_argument("--event", choices=("schedule","workflow_dispatch","push"), required=True)
        if name in ("ready","enroll-pc","claim","record-start","complete","fail","reconcile"): sub.add_argument("--owner",required=True)
        if name in ("record-start","complete","fail","reconcile"):
            sub.add_argument("--id",required=True)
            sub.add_argument("--lease",required=True)
            sub.add_argument("--attempt",type=int,required=True)
        if name in ("complete","fail"): sub.add_argument("--receipt",type=Path,required=True)
        if name in ("pause","reconcile"): sub.add_argument("--reason",required=True)
        if name=="reconcile":
            sub.add_argument("--confirm-stopped",action="store_true",required=True)
            sub.add_argument("--never-started",action="store_true")
    x=p.parse_args()
    if getattr(x,"pc_owner",None): require(OWNER.fullmatch(x.pc_owner),"invalid enrolled owner")
    if hasattr(x,"owner"): require(OWNER.fullmatch(x.owner),"invalid owner")
    if hasattr(x,"reason"): require(0<len(x.reason.strip())<=1000,"invalid reason")
    file=getattr(x,"manifest",None) or getattr(x,"receipt",None)
    if file:
        require(file.stat().st_size<=128000,"oversized request")
        x.payload=json.loads(file.read_text())
    require(os.environ.get("GITHUB_REPOSITORY",FORK)==FORK,"unexpected repository")
    if x.action in ("init","submit","schedule","enroll-pc","pause","resume","reconcile") and os.environ.get("GITHUB_ACTIONS") == "true":
        ref = os.environ.get("GITHUB_WORKFLOW_REF","")
        require(os.environ.get("GITHUB_REPOSITORY")==FORK and os.environ.get("GITHUB_REF")=="refs/heads/master"
                and ref.startswith(FORK + "/.github/workflows/") and ref.endswith("@refs/heads/master")
                and os.environ.get("GITHUB_EVENT_NAME") not in ("pull_request","pull_request_target"),
                "administrative ingress must run trusted fork default-branch code")
    token=os.environ.get("STOCKFISH_WORKER_TOKEN")
    directory=os.environ.get("CREDENTIALS_DIRECTORY")
    if not token and directory:
        key=Path(directory)/"github-token"
        if key.is_file(): token=key.read_text().strip()
    auth=None if token else existing_fork_git_auth()
    with tempfile.TemporaryDirectory() as temp:
        reply=transact(Store(temp,"https://github.com/"+FORK+".git",REF,token,auth),x)
    data=json.dumps(reply,sort_keys=True,indent=2,allow_nan=False)+"\n"
    if x.out: x.out.write_text(data)
    else: print(data,end="")


if __name__=="__main__":
    try: main()
    except (OSError,ValueError,KeyError,AttributeError,TypeError,RuntimeError,subprocess.SubprocessError) as exc:
        sys.exit(f"testing dispatch: {exc}")
