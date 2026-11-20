"""Offline dispatch tests with a temporary real Git server; no engines or paid providers run."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "dedicated-worker"))
import dispatch as d


class Dispatch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.control = self.root/"control"
        self.remote = self.root/"fork.git"
        self.git("init","-b","research/baseline",str(self.control),cwd=self.root)
        self.git("init","--bare",str(self.remote))
        self.git("config","user.name","Fixture")
        self.git("config","user.email","test@example.invalid")
        (self.control/"src").mkdir()
        (self.control/"src/search.cpp").write_text("// base\n")
        self.git("add","."); self.git("commit","-m","base")
        base = self.git("rev-parse","HEAD")
        self.git("push",str(self.remote),"HEAD:"+d.BASE)
        self.git("switch","-c","experiment/EXP-PC")
        (self.control/"src/search.cpp").write_text("// test\n")
        self.git("commit","-am","one engine change")
        new = self.git("rev-parse","HEAD")
        self.git("push",str(self.remote),"HEAD:refs/heads/experiment/EXP-PC")
        self.manifest = dict(experiment="EXP-PC",baseline=base,candidate=new,placement="pc-pilot",
                             pairs=1,seed=17,time_control="4+0.04",max_minutes=30)
        self.bare=self.root/"reader.git"
        self.bare.mkdir()
        self.store=d.Store(self.bare,str(self.remote),d.REF)
        self.call("init",pc_owner="my-pc")
        self.call("submit",payload=self.manifest)
        self.assertEqual(self.call("schedule",run=101,event="schedule")["decision"],"waiting_for_pc")
        self.call("ready",owner="my-pc")
        self.call("schedule",run=102,event="schedule")

    def git(self,*args,cwd=None):
        return subprocess.check_output(["git",*args],cwd=cwd or self.control,stderr=subprocess.PIPE,text=True).strip()

    def call(self,action,**kw):
        return d.transact(self.store,SimpleNamespace(action=action,**kw))

    def start(self):
        claim=self.call("claim",worker="pc",owner="my-pc")
        self.fence={k:claim[k] for k in ("id","attempt","lease","owner")}
        self.assertEqual(self.call("record-start",**self.fence)["action"],"start_authorized")

    def evidence(self):
        m=self.manifest
        pgn="".join(f'[Round "1"]\n[White "{w}"]\n[Black "{b}"]\n[TimeControl "4+0.04"]\n'
                    '[Termination "normal"]\n[Result "1/2-1/2"]\n\n1. e4 e5 1/2-1/2\n\n'
                    for w,b in (("candidate","baseline"),("baseline","candidate"))).encode()
        layout={"threads":1,"concurrency":1}
        match=dict(status="screened",stage="C",settings=dict(pairs=1,seed=17,time_control="4+0.04",**layout),
                   baseline={"commit":m["baseline"]},candidate={"commit":m["candidate"]},games=2,
                   wins=0,draws=2,losses=0,pgn_sha256=d.sha256(pgn),decision="inconclusive")
        j=lambda x: (json.dumps(x)+"\n").encode()
        trial=dict(match,candidate={"commit":m["baseline"]})
        prefix="calibration/test/"
        calib={"valid":True,"status":"complete","selected":layout,"games":2,"layouts":[dict(layout,valid=True,
             games=2,games_per_hour=123,wall_seconds=50,evidence=prefix+"match.json",pgn=prefix+"games.pgn")]}
        payload={k:m[k] for k in d.REQUEST}
        blobs={"request.json":j(payload),"match/match.json":j(match),"match/games.pgn":pgn,
               "calibration.json":j(calib),prefix+"match.json":j(trial),prefix+"games.pgn":pgn}
        result=dict(worker="pc",status="screened",rehearsal=False,baseline_sha=m["baseline"],candidate_sha=m["candidate"],
                    calibration_games=2,games=2,actual_games=4,selected_layout=layout,decision="inconclusive",
                    request_sha256=d.sha256(blobs["request.json"]),evidence_sha256={k:d.sha256(v) for k,v in blobs.items()})
        blobs["result.json"]=j(result)
        receipt=dict(experiment=m["experiment"],baseline=m["baseline"],candidate=m["candidate"],worker="pc",
                     owner="my-pc",attempt=1,lease=self.fence["lease"],outcome="screened",games=2,calibration_games=2,
                     wall_minutes=2.2,rehearsal=False,calibrated=True,sha256={k:d.sha256(v) for k,v in blobs.items()})
        blobs["receipt.json"]=j(receipt)
        for rel,data in blobs.items():
            path=self.control/rel
            path.parent.mkdir(exist_ok=True,parents=True)
            path.write_bytes(data)
        self.git("add","."); self.git("commit","-m","Synthetic receipt fixture")
        ref=f"refs/heads/research/testing-evidence/{m['experiment']}/{receipt['lease']}"
        self.git("push",str(self.remote),"HEAD:"+ref)
        return dict(receipt,evidence_commit=self.git("rev-parse","HEAD"))

    def test_real_evidence_enables_only_enrolled_pc_and_replays_safely(self):
        self.assertEqual(self.call("claim",worker="cloud",owner="runner-1")["action"],"idle")
        self.assertEqual(self.call("claim",worker="pc",owner="stranger")["action"],"idle")
        self.start()
        self.assertEqual(self.call("claim",worker="pc",owner="my-pc")["action"],"resume")
        self.assertEqual(self.call("record-start",**self.fence)["action"],"recover_only")
        receipt=self.evidence()
        with self.assertRaisesRegex(ValueError,"mismatch"):
            self.call("complete",**self.fence,payload=dict(receipt,games=99))
        out=self.call("complete",**self.fence,payload=receipt)
        self.assertEqual(out["preferred_pc"]["owner"],"my-pc")
        tip=self.store.read()[0]
        self.assertEqual(self.call("complete",**self.fence,payload=receipt)["action"],"already_recorded")
        self.assertEqual(self.store.read()[0],tip)
        self.assertEqual(self.call("status")["usage"]["lifetime"],dict(wall_minutes=3,games=4))

    def test_scheduled_next_match_and_repeat_delivery(self):
        second = dict(self.manifest,experiment="EXP-NEXT",seed=29,placement="preferred")
        self.git("push",str(self.remote),"HEAD:refs/heads/experiment/EXP-NEXT")
        self.call("submit",payload=second)
        self.start()
        self.assertEqual(self.call("schedule",run=103,event="schedule")["decision"],"campaign_active")
        self.call("complete",**self.fence,payload=self.evidence())
        self.assertEqual(self.call("claim",worker="pc",owner="my-pc")["action"],"idle")
        for n in (104,105):
            self.assertEqual(self.call("schedule",run=n,event="schedule")["decision"],"dispatched EXP-NEXT")
        self.assertEqual(self.call("claim",worker="pc",owner="my-pc")["id"],"EXP-NEXT")
        _, state = self.store.read()
        self.assertEqual(len(state["jobs"]["EXP-NEXT"]["attempts"]),1)
        self.assertEqual(state["jobs"]["EXP-NEXT"]["delivery"]["run"],104)

    def test_cloud_requires_fresh_parent_allocation_and_exact_checkpoint(self):
        m=dict(self.manifest,experiment="EXP-CLOUD",placement="cloud",seed=18)
        self.git("push",str(self.remote),"HEAD:refs/heads/experiment/EXP-CLOUD")
        self.call("submit",payload=m)
        with patch.dict(os.environ,{"RESEARCH_PARENT_ID":"","RESEARCH_PARENT_LEASE":""}):
            self.assertEqual(self.call("claim",worker="cloud",owner="runner")["action"],"idle")
        now=datetime.now(timezone.utc)
        parent="EXP-CONT-123"
        lease="1"*32
        details={k:m[k] for k in ("experiment","baseline","candidate","max_minutes")}
        upstream=dict(schema=1,rehearsal=None,paused=None,items=[dict(id=parent,baseline=m["baseline"],status="running",
            attempts=[dict(lease=lease,budget={"runner_minutes":110,"cost_usd":0},
                           started_at=now.isoformat(),expires_at=(now+timedelta(hours=2)).isoformat(),
                           checkpoints=[dict(stage="testing-allocation",details=details)])])])
        def push_parent():
            (self.control/"queue.json").write_text(json.dumps(upstream))
            self.git("add","queue.json");self.git("commit","-m","Parent queue checkpoint")
            self.git("push",str(self.remote),"HEAD:refs/heads/research/queue")
        push_parent()
        with patch.dict(os.environ,{"RESEARCH_PARENT_ID":parent,"RESEARCH_PARENT_LEASE":lease}):
            claim=self.call("claim",worker="cloud",owner="runner")
            self.assertEqual(claim["id"],m["experiment"])
            upstream["items"][0]["status"]="failed"
            push_parent()
            with self.assertRaisesRegex(ValueError,"parent allocation"):
                self.call("record-start",**{k:claim[k] for k in ("id","attempt","lease","owner")})

    def test_interrupted_attempt_never_transfers_or_restarts(self):
        self.start()
        _,state=self.store.read()
        future=datetime.now(timezone.utc)+timedelta(days=3)
        cloud=SimpleNamespace(action="claim",worker="cloud",owner="runner-2")
        self.assertEqual(d.change(self.store,state,cloud,future)["reason"],"global_slot_occupied")
        with self.assertRaisesRegex(ValueError,"never replay"):
            self.call("reconcile",**self.fence,confirm_stopped=True,reason="old process stopped",never_started=True)
        with self.assertRaisesRegex(ValueError,"mismatch"):
            self.call("record-start",**dict(self.fence,lease="bad"))
        out=self.call("reconcile",**self.fence,confirm_stopped=True,reason="old process stopped",never_started=False)
        self.assertEqual(out["status"],"invalid")
        self.assertEqual(self.call("status")["usage"]["lifetime"],dict(wall_minutes=30,games=18))

    def test_source_rechecked_and_concurrent_claims_get_only_one_start(self):
        other=self.root/"second.git"; other.mkdir()
        second=d.Store(other,str(self.remote),d.REF)
        orig=self.store.save
        winner={}
        def race(tip,state,action):
            winner.update(d.transact(second,SimpleNamespace(action="claim",worker="pc",owner="my-pc")))
            orig(tip,state,action)
        with patch.object(self.store,"save",side_effect=race):
            loser=self.call("claim",worker="pc",owner="my-pc")
        self.assertEqual(loser["action"],"resume")
        self.assertEqual(loser["lease"],winner["lease"])
        self.git("commit","--allow-empty","-m","branch moved")
        self.git("push",str(self.remote),"HEAD:refs/heads/experiment/EXP-PC")
        fence={k:winner[k] for k in ("id","lease","attempt","owner")}
        with self.assertRaisesRegex(ValueError,"ref moved"):
            self.call("record-start",**fence)
        self.assertEqual(self.call("reconcile",**fence,confirm_stopped=True,reason="not started",never_started=True)["status"],
                         "pending")
        self.assertIsNone(self.call("status")["state"]["preferred_pc"])


if __name__=="__main__":
    unittest.main()
