import json
import pytest
from invoice_eval.adapters import normalize
from invoice_eval.judge import Judge
from invoice_eval.loader import digest
from invoice_eval.timeline import render_timeline
from invoice_eval.pipeline import ROOT, evaluate

@pytest.mark.parametrize("format_name",["canonical","envelope","otel"])
def test_adapters_preserve_events(cases,format_name):
    c=cases[0].model_dump()
    if format_name=="canonical":rows=[c]
    elif format_name=="envelope":rows=[{"task":{k:v for k,v in c.items() if k!="runs"},"trajectories":c["runs"]}]
    else:
        rows=[{"case":{k:v for k,v in c.items() if k!="runs"},"run_metadata":[{k:v for k,v in r.items() if k!="events"} for r in c["runs"]],"resourceSpans":[{"scopeSpans":[{"spans":[{"attributes":[{"key":"invoice.run_id","value":{"stringValue":r["run_id"]}},{"key":"invoice.event_json","value":{"stringValue":json.dumps(e)}}]} for r in c["runs"] for e in r["events"]]}]}]}]
    assert normalize(rows,format_name)==[c]

class Response:
    def __init__(self,raw):self.raw=raw
    def raise_for_status(self):pass
    def json(self):return {"choices":[{"message":{"content":self.raw}}],"usage":{"total_tokens":12}}

def good_response():
    return json.dumps({"status":"success","question_id":"q","verdict":"fail","label":"AMOUNT_BASIS","first_fault_event_id":"ev005","evidence_event_ids":["ev005"],"field_paths":["events.ev005.observed_output.amount_basis"],"explanation":"wrong basis","missing_evidence":[]})

def setup_judge(monkeypatch,tmp_path):
    monkeypatch.setenv("TEST_KEY","test-only")
    return Judge({"mode":"live","model":"test-model","base_url":"https://test.invalid/v1","api_key_env":"TEST_KEY"},tmp_path,"rule-version")

def test_structured_repair_and_cache(cases,tmp_path,monkeypatch):
    judge=setup_judge(monkeypatch,tmp_path)
    responses=iter(["broken",good_response()])
    monkeypatch.setattr("invoice_eval.judge.httpx.post",lambda *a,**kw:Response(next(responses)))
    q={"question_id":"q","event_id":cases[0].runs[1].events[4].event_id}
    first=judge.ask(cases[0],cases[0].runs[1],q)
    assert first.first_fault_event_id==cases[0].runs[1].events[4].event_id
    assert len(judge.records[0]["attempts"])==2
    again=judge.ask(cases[0],cases[0].runs[1],q)
    assert again==first and judge.records[-1]["cache_hit"]

@pytest.mark.parametrize("raw",["broken",json.dumps({"status":"skipped"}),good_response().replace("AMOUNT_BASIS","invented")])
def test_bad_model_outputs_not_success(cases,tmp_path,monkeypatch,raw):
    judge=setup_judge(monkeypatch,tmp_path)
    monkeypatch.setattr("invoice_eval.judge.httpx.post",lambda *a,**kw:Response(raw))
    assert judge.ask(cases[0],cases[0].runs[1],{"question_id":"q","event_id":cases[0].runs[1].events[4].event_id}) is None
    assert not judge.records[-1]["valid_live"]
    assert len(judge.records[-1]["attempts"])==2

def test_timeline_embeds_data_safely(tmp_path,local_material):
    run=tmp_path/"run"
    evaluate(next((ROOT/"material").glob("04*.jsonl")),ROOT/"configs/default.json",run)
    path=render_timeline(run,tmp_path/"timeline.html")
    content=path.read_text(encoding="utf-8")
    assert "showEvent" in content and "confirmation_fingerprint" in content
    assert "INV-V08" in content

def test_dual_disagreement_does_not_overwrite_assessment(tmp_path,config,monkeypatch,local_material):
    from invoice_eval.audit import audit
    from invoice_eval.models import AuditResult, JudgeCause
    from invoice_eval.loader import write_json
    run=tmp_path/"evaluated"
    evaluate(next((ROOT/"material").glob("02*.jsonl")),ROOT/"configs/default.json",run)
    before=(run/"submission.jsonl").read_bytes()
    config["judge"]["model"]="judge-one"
    config["secondary_judge"]={**config["judge"],"model":"judge-two"}
    path=tmp_path/"dual.json";write_json(path,config)
    def answer(self,case,trajectory,question):
        fail=self.config["model"]=="judge-two"
        event=trajectory.events[4].event_id
        causes=[JudgeCause(label="AMOUNT_BASIS",first_fault_event_id=event,evidence_event_ids=[event],field_paths=[f"events.{event}.observed_output"],explanation="simulated cause")] if fail else []
        return AuditResult(status="success",question_id=question["question_id"],verdict="fail" if fail else "pass",causes=causes,evidence_event_ids=[event],field_paths=[f"events.{event}.observed_output"],explanation="simulated disagreement for aggregation test",missing_evidence=[])
    monkeypatch.setattr(Judge,"ask",answer)
    report=audit(run,path,tmp_path/"audit",["INV-T01"])
    assert report["disagreement_pairs"]==2 and report["valid_pairs"]==2
    assert (run/"submission.jsonl").read_bytes()==before
