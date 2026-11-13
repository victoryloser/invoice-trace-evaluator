import copy
import json
from pathlib import Path
import pytest
from invoice_eval.loader import write_json,write_jsonl,read_jsonl,digest
from invoice_eval.pipeline import ROOT,evaluate
from invoice_eval.rules import inspect_run
from invoice_eval.decisions import reconcile,synchronize_checks
from invoice_eval.models import JudgeResult,AuditResult,JudgeCause
from invoice_eval.judge import Judge,blind_packet
from invoice_eval.evidence import validate_judge
from invoice_eval.matching import match_causes
from invoice_eval.audit import align
from invoice_eval.metrics import compare_labels
from invoice_eval.acceptance import register,verify_acceptance,review_template,summarize_review
from invoice_eval.stability import stability

def semantic_case(cases):
    c=cases[11].model_copy(deep=True)
    c.user_request="给客户CUST-111开与软件有关的服务费用，不含税7200元，先确认。"
    return c

def fake_ask(verdict="fail",label="PRODUCT_MATCH"):
    def ask(self,case,run,q):
        e=next(e for e in run.events if e.event_id==q["event_id"])
        response=JudgeResult(status="success",question_id=q["question_id"],verdict=verdict,label=label if verdict=="fail" else None,first_fault_event_id=e.event_id if verdict=="fail" else None,evidence_event_ids=[e.event_id],field_paths=[f"events.{e.event_id}.observed_output.item_id"],explanation="protocol fixture",missing_evidence=[])
        self.records.append({"status":"success","valid_live":True,"cache_hit":False,"attempts":[{"usage":{}}],"run_id":run.run_id,"question_id":q["question_id"],"mapped_result":response.model_dump()})
        return response
    return ask

def test_unknown_root_known_outcome_export(cases,tmp_path):
    c=cases[0];r=c.runs[0]
    callback=cases[1].runs[1].events[8].model_copy(deep=True)
    callback.event_id="unlocated-service-success";callback.task_id=r.task_id;callback.trace_id=r.trace_id
    r.events.append(callback);r.invoice_created=True;r.final_state="SUCCEEDED"
    path=tmp_path/"cases.jsonl";write_jsonl(path,[c.model_dump()])
    output=tmp_path/"run";m=evaluate(path,ROOT/"configs/default.json",output)
    assert m["status"]=="success"
    a=read_jsonl(output/"submission.jsonl")[0]["run_assessments"][0]
    assert a["agent_task_success"] is False and a["business_invoice_created"] is True
    assert a["root_causes"][0]["label"]=="INSUFFICIENT_EVIDENCE"
    assert a["root_causes"][0]["first_fault_event_id"] is None
    assert a["quality_score"]==50

def test_unknown_checks_when_required_judge_disabled(cases,tmp_path):
    path=tmp_path/"cases.jsonl";write_jsonl(path,[semantic_case(cases).model_dump()])
    out=tmp_path/"run";m=evaluate(path,ROOT/"configs/default.json",out)
    assert m["status"]=="needs_review"
    for row in read_jsonl(out/"checks.jsonl"):
        assert next(x for x in row["checks"] if x["rule_id"]=="P01")["status"]=="unknown"

@pytest.mark.parametrize("states,expected",[(['unknown','pass'],'unknown'),(['pass','unknown'],'unknown'),(['fail','unknown'],'fail'),(['unknown','fail'],'fail')])
def test_same_rule_semantic_states_are_order_independent(states,expected):
    analysis={"checks":[{"rule_id":"P01","status":"pass"}],"findings":[],"questions":[{"question_id":str(i),"kind":"product"} for i in range(2)],"question_states":dict(zip(['0','1'],states))}
    synchronize_checks(analysis)
    assert analysis['checks'][0]['status']==expected

def test_timeline_keeps_individually_valid_unpaired_run(cases,tmp_path):
    from invoice_eval.timeline import render_timeline
    c=cases[1];c.runs[1].invoice_created=False
    path=tmp_path/'cases.jsonl';write_jsonl(path,[c.model_dump()])
    out=tmp_path/'run';evaluate(path,ROOT/'configs/default.json',out)
    html=render_timeline(out,tmp_path/'timeline.html').read_text(encoding='utf-8')
    payload=json.loads(html.split('<script id="data" type="application/json">')[1].split('</script>')[0])
    assert len(payload['individual'])==1 and payload['records']==[]

def test_semantic_failure_updates_checks(cases,tmp_path,monkeypatch):
    monkeypatch.setattr(Judge,"ask",fake_ask())
    path=tmp_path/"cases.jsonl";write_jsonl(path,[semantic_case(cases).model_dump()])
    out=tmp_path/"run";m=evaluate(path,ROOT/"configs/default.json",out)
    assert m["status"]=="success"
    for row in read_jsonl(out/"checks.jsonl"):
        assert next(x for x in row["checks"] if x["rule_id"]=="P01")["status"]=="fail"

def test_model_pass_cannot_erase_verified_failure(cases):
    c=cases[0];r=c.runs[1];a=inspect_run(c,r)
    q={"question_id":"terminal","kind":"terminal","event_id":r.events[-1].event_id}
    result=JudgeResult(status="success",question_id="terminal",verdict="pass",label=None,first_fault_event_id=None,evidence_event_ids=[r.events[-1].event_id],field_paths=["context"],explanation="everything correct",missing_evidence=[])
    before=len(a["findings"]);d=reconcile(result,q,a,r)
    assert d["status"]=="conflict" and len(a["findings"])==before
    assert a["conflicts"] and not a["review"]

def test_model_fail_against_correct_arithmetic_requires_review(cases):
    c=cases[1];r=c.runs[1];a=inspect_run(c,r)
    q={"question_id":"terminal","kind":"terminal","event_id":r.events[-1].event_id}
    result=JudgeResult(status="success",question_id="terminal",verdict="fail",label="AMOUNT_CALCULATION",first_fault_event_id=r.events[4].event_id,evidence_event_ids=[r.events[4].event_id],field_paths=[f"events.{r.events[4].event_id}.observed_output.computed_amounts"],explanation="invented error",missing_evidence=[])
    assert reconcile(result,q,a,r)["status"]=="conflict"
    assert a["review"] and not a["findings"]

def test_unknown_root_without_known_outcome_remains_unknown(cases):
    c=semantic_case(cases);r=c.runs[0];a=inspect_run(c,r);q=a["questions"][0]
    result=JudgeResult(status="success",question_id=q["question_id"],verdict="fail",label="INSUFFICIENT_EVIDENCE",first_fault_event_id=None,evidence_event_ids=[q["event_id"]],field_paths=["context"],explanation="cannot identify problem",missing_evidence=["root cause"])
    assert reconcile(result,q,a,r)["status"]=="unresolved"
    synchronize_checks(a)
    assert next(x for x in a["checks"] if x["rule_id"]=="P01")["status"]=="unknown"

def test_relevant_event_id_with_irrelevant_field_rejected(cases):
    c=cases[0];r=c.runs[1]
    packet,q,_=blind_packet(c,r,{"question_id":"q","event_id":r.events[4].event_id})
    result=JudgeResult(status="success",question_id="q",verdict="fail",label="AMOUNT_BASIS",first_fault_event_id="ev005",evidence_event_ids=["ev005"],field_paths=["events.ev005.seq"],explanation="unsupported",missing_evidence=[])
    with pytest.raises(ValueError,match="label-relevant"):validate_judge(result,packet,q)

def test_global_exact_root_matching_before_fallback():
    gold=[{"label":"X","first_fault_event_id":"one"},{"label":"X","first_fault_event_id":"two"}]
    pred=[{"label":"X","first_fault_event_id":"two"},{"label":"X","first_fault_event_id":"three"}]
    pairs,missing,extra=match_causes(gold,pred,{"one":1,"two":2,"three":3})
    assert (1,0,True) in pairs and (0,1,False) in pairs and not missing and not extra

def test_cancel_risk_in_missed_label_count(tmp_path):
    gold={"case_id":"cancel","version_direction_on_this_case":"baseline_better","run_assessments":[{"run_id":"bad","agent_task_success":False,"business_invoice_created":False,"reference_snapshot_state":"CANCELED","root_causes":[{"label":"INTENT_CLASSIFICATION","first_fault_event_id":"parse"}]}]}
    pred={"case_id":"cancel","version_comparison":{"direction":"baseline_better"},"run_assessments":[{"run_id":"bad","agent_task_success":True,"business_invoice_created":False,"root_causes":[]}]}
    write_jsonl(tmp_path/"gold.jsonl",[gold]);write_jsonl(tmp_path/"pred.jsonl",[pred])
    assert compare_labels(tmp_path/"pred.jsonl",tmp_path/"gold.jsonl")["high_risk_missed"]==1

def test_valid_unpaired_run_counted(cases,tmp_path):
    c=cases[1];c.runs[1].invoice_created=False
    path=tmp_path/"cases.jsonl";write_jsonl(path,[c.model_dump()])
    out=tmp_path/"run";m=evaluate(path,ROOT/"configs/default.json",out)
    stats=json.loads((out/"metrics.json").read_text(encoding="utf-8"))
    assert stats["valid_runs"]==1 and stats["paired_runs"]==0 and stats["unpaired_valid_runs"]==1
    assert stats["failed_or_review_runs"]==1 and m["successful_runs"]==1
    assert not (out/"submission.jsonl").exists()

def good_pair(cases):
    c=cases[0].model_copy(deep=True)
    second=c.runs[1];clone=c.runs[0].model_copy(deep=True)
    clone.run_id=second.run_id;clone.task_id=second.task_id;clone.trace_id=second.trace_id
    for e in clone.events:e.event_id="second-"+e.event_id;e.task_id=clone.task_id;e.trace_id=clone.trace_id
    c.runs[1]=clone
    return c

def acceptance_fixture(cases,tmp_path,independent=True):
    c=good_pair(cases);path=tmp_path/"cases.jsonl";write_jsonl(path,[c.model_dump()])
    expected=[{"run_id":r.run_id,"agent_task_success":True,"business_invoice_created":False,"root_causes":[]} for r in c.runs]
    exp=tmp_path/"expected.jsonl";write_jsonl(exp,expected)
    reg=tmp_path/"registration.json";register(path,exp,reg,"test-fixture-reviewer",independent)
    run=tmp_path/"accepted";evaluate(path,ROOT/"configs/default.json",run)
    report=tmp_path/"evidence.json";verify_acceptance(reg,run,report)
    return path,report,run

def test_gate_loads_real_bound_acceptance(cases,config,tmp_path):
    path,report,_=acceptance_fixture(cases,tmp_path)
    config["release_gate"]={"required_cases":[cases[0].case_id],"evidence_files":[str(report)]}
    cp=tmp_path/"config.json";write_json(cp,config)
    out=tmp_path/"release";evaluate(path,cp,out)
    assert json.loads((out/"metrics.json").read_text(encoding="utf-8"))["release_recommendation"]=="release"

@pytest.mark.parametrize("change",["source","config","development","failed","coverage"])
def test_stale_or_nonindependent_evidence_cannot_release(cases,config,tmp_path,change):
    path,report,_=acceptance_fixture(cases,tmp_path,independent=change!="development")
    data=json.loads(report.read_text(encoding="utf-8"))
    if change=="source":data["source_hash"]="stale"
    if change=="config":data["decision_config_hash"]="different"
    if change=="failed":data["passed"]=False
    if change=="coverage":data["comparisons"].pop()
    data["evidence_id"]=digest({k:v for k,v in data.items() if k!="evidence_id"});write_json(report,data)
    config["release_gate"]={"required_cases":[cases[0].case_id],"evidence_files":[str(report)],"independent_regression_passed":True}
    cp=tmp_path/"config.json";write_json(cp,config)
    out=tmp_path/"release";evaluate(path,cp,out)
    assert json.loads((out/"metrics.json").read_text(encoding="utf-8"))["release_recommendation"]=="gray"

def test_human_review_preserves_unreviewed_denominator(cases,tmp_path):
    _,_,run=acceptance_fixture(cases,tmp_path,False)
    path=tmp_path/"review.json";review=review_template(run,path)
    with pytest.raises(ValueError,match="human reviewer"):summarize_review(run,path,tmp_path/"summary.json")
    review.update(reviewer="test fixture",completed_at="2026-11-12T00:00:00+08:00")
    review["items"][0].update(supported=True,comment="fixture verifies original evidence")
    write_json(path,review);summary=summarize_review(run,path,tmp_path/"summary.json")
    assert summary["reviewed"]==1 and summary["pending"]==1 and summary["support_rate"]==1
    assert summary["independence"]=="development"

def test_all_root_alignment_exposes_missing_second_fault():
    roots=[{"label":"AMOUNT_BASIS","first_fault_event_id":"five","evidence_event_ids":["five"]},{"label":"CONFIRMATION_BYPASS","first_fault_event_id":"eight","evidence_event_ids":["eight"]}]
    compared=align(roots,roots[:1],{"five":5,"eight":8})
    assert not compared["same_causes"] and compared["only_left"]==roots[1:]

def test_fresh_stability_bypasses_read_and_write(cases,tmp_path,monkeypatch):
    c=semantic_case(cases);path=tmp_path/"cases.jsonl";write_jsonl(path,[c.model_dump()])
    policies=[];original=fake_ask("pass")
    def ask(self,*args):policies.append(self.cache_policy);return original(self,*args)
    monkeypatch.setattr(Judge,"ask",ask)
    report=stability(path,ROOT/"configs/default.json",tmp_path/"stability",2,True)
    assert policies==["bypass"]*4 and report["cache_hits"]==0
    assert report["total_judgments"]==4 and report["network_attempts"]==4

def test_fresh_judge_ignores_existing_cache_and_does_not_replace_it(cases,tmp_path,monkeypatch):
    from invoice_eval.loader import file_hash
    monkeypatch.setenv("FIXTURE_KEY","fixture")
    cfg={"mode":"live","base_url":"https://fixture.invalid/v1","model":"fixture","api_key_env":"FIXTURE_KEY"}
    count=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):
            return {"choices":[{"message":{"content":json.dumps({"status":"success","question_id":"q","verdict":"pass","label":None,"first_fault_event_id":None,"evidence_event_ids":["ev005"],"field_paths":["events.ev005.observed_output"],"explanation":str(len(count)),"missing_evidence":[]})}}]}
    def post(*a,**k):count.append(1);return Response()
    monkeypatch.setattr("invoice_eval.judge.httpx.post",post)
    c=cases[0];r=c.runs[0];q={"question_id":"q","event_id":r.events[4].event_id}
    cached=Judge(cfg,tmp_path,"rules");cached.ask(c,r,q)
    path=next(tmp_path.glob("*.json"));before=file_hash(path)
    fresh=Judge(cfg,tmp_path,"rules",cache_policy="bypass")
    first=fresh.ask(c,r,q);second=fresh.ask(c,r,q)
    assert len(count)==3 and first.explanation!=second.explanation
    assert all(not x["cache_hit"] for x in fresh.records) and file_hash(path)==before

def test_audit_schema_preserves_multiple_independent_roots(cases):
    c=cases[0];r=c.runs[1]
    packet,q,_=blind_packet(c,r,{"question_id":"all","kind":"audit_all","event_id":r.events[-1].event_id})
    causes=[JudgeCause(label="AMOUNT_BASIS",first_fault_event_id="ev005",evidence_event_ids=["ev005"],field_paths=["events.ev005.observed_output.amount_basis"],explanation="wrong basis"),JudgeCause(label="CONFIRMATION_BYPASS",first_fault_event_id="ev008",evidence_event_ids=["ev008"],field_paths=["events.ev008.observed_input.user_confirmed"],explanation="unconfirmed submission")]
    result=AuditResult(status="success",question_id="all",verdict="fail",causes=causes,evidence_event_ids=["ev005","ev008"],field_paths=["events.ev005.observed_output.amount_basis","events.ev008.observed_input.user_confirmed"],explanation="two independent failures",missing_evidence=[])
    assert len(validate_judge(result,packet,q).causes)==2
