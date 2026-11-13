import json
import pytest
from invoice_eval.evidence import validate_judge
from invoice_eval.export import validate_records
from invoice_eval.judge import Judge, blind_packet
from invoice_eval.loader import load_cases, write_jsonl
from invoice_eval.models import JudgeResult
from invoice_eval.pipeline import ROOT, evaluate

@pytest.mark.parametrize("kind", ["blank","bad_json","duplicate_case","duplicate_event","duplicate_seq","missing_run"])
def test_strict_input(cases,tmp_path,kind):
    path=tmp_path/"input.jsonl"
    value=cases[0].model_dump()
    if kind=="blank":path.write_text("\n",encoding="utf-8")
    elif kind=="bad_json":path.write_text("not-json\n",encoding="utf-8")
    elif kind=="duplicate_case":write_jsonl(path,[value,value])
    else:
        if kind=="duplicate_event":value["runs"][0]["events"][1]["event_id"]=value["runs"][0]["events"][0]["event_id"]
        if kind=="duplicate_seq":value["runs"][0]["events"][1]["seq"]=1
        if kind=="missing_run":value["runs"].pop()
        write_jsonl(path,[value])
    with pytest.raises(ValueError,match=r":1:|:2:"):load_cases(path)

@pytest.mark.parametrize("mode", ["disabled","mock","live"])
def test_no_key_never_valid(cases,tmp_path,mode,monkeypatch):
    monkeypatch.delenv("MISSING_JUDGE_KEY",raising=False)
    judge=Judge({"mode":mode,"api_key_env":"MISSING_JUDGE_KEY"},tmp_path,"rules")
    assert judge.ask(cases[0],cases[0].runs[0],{"question_id":"q","event_id":cases[0].runs[0].events[4].event_id}) is None
    assert not judge.records[0]["valid_live"]

def make_result():
    return JudgeResult(status="success",question_id="q",verdict="fail",label="AMOUNT_BASIS",first_fault_event_id="ev005",evidence_event_ids=["ev005"],field_paths=["events.ev005.observed_output.amount_basis"],explanation="basis differs",missing_evidence=[])

@pytest.mark.parametrize("change", ["label","cross_run","future","path","missing_evidence","question"])
def test_judge_evidence_contract(cases,change):
    c=cases[0];r=c.runs[1]
    packet,q,_=blind_packet(c,r,{"question_id":"q","event_id":r.events[4].event_id})
    result=make_result()
    if change=="label":result.label="UNKNOWN_LABEL"
    if change=="cross_run":result.evidence_event_ids=["INV-T01-A-05"]
    if change=="future":result.first_fault_event_id="ev008";result.evidence_event_ids=["ev008"]
    if change=="path":result.field_paths=["events.ev005.invented"]
    if change=="missing_evidence":result.evidence_event_ids=[]
    if change=="question":result.question_id="other"
    with pytest.raises(ValueError):validate_judge(result,packet,q)

def test_blinding(cases):
    c=cases[0];r=c.runs[1]
    packet,_,_=blind_packet(c,r,{"question_id":"q","event_id":r.events[4].event_id})
    text=json.dumps(packet)
    assert c.case_id not in text and r.run_id not in text and r.agent_version not in text

def test_full_offline_pipeline(tmp_path,local_material):
    for prefix,expected in [("02",14),("04",8)]:
        manifest=evaluate(next((ROOT/"material").glob(prefix+"*.jsonl")),ROOT/"configs/default.json",tmp_path/prefix)
        assert manifest["status"]=="success"
        assert manifest["validation"]["case_count"]==expected

def test_unresolved_never_published(cases,tmp_path):
    c=cases[1];c.runs[1].invoice_created=False
    path=tmp_path/"input.jsonl";write_jsonl(path,[c.model_dump()])
    output=tmp_path/"evaluation"
    m=evaluate(path,ROOT/"configs/default.json",output)
    assert m["status"]=="needs_review"
    assert not (output/"submission.jsonl").exists()
    assert (output/"review_queue.json").exists()
