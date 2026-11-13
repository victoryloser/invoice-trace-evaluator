import copy
import json
import pytest
import httpx
from invoice_eval.export import validate_records
from invoice_eval.judge import Judge
from invoice_eval.loader import read_jsonl
from invoice_eval.pipeline import ROOT,evaluate
from invoice_eval.metrics import compare_labels
from invoice_eval.stability import stability

@pytest.fixture
def evaluation(tmp_path,local_material):
    path=tmp_path/"run"
    evaluate(next((ROOT/"material").glob("02*.jsonl")),ROOT/"configs/default.json",path)
    return path

@pytest.mark.parametrize("change",["omit_case","duplicate_case","cross_run","score","duplicate_penalty"])
def test_export_integrity(cases,evaluation,change):
    records=read_jsonl(evaluation/"submission.jsonl")
    bad=records[0]["run_assessments"][1]
    if change=="omit_case":records.pop()
    if change=="duplicate_case":records[1]=records[0]
    if change=="cross_run":bad["root_causes"][0]["evidence_event_ids"].append("INV-T01-A-05")
    if change=="score":bad["quality_score"]=90
    if change=="duplicate_penalty":bad["penalty_items"].append(bad["penalty_items"][0])
    with pytest.raises(ValueError):validate_records(cases,records,next((ROOT/"material").glob("06*.json")))

def test_development_metrics_include_all_denominators(evaluation):
    result=compare_labels(evaluation/"submission.jsonl",next((ROOT/"material").glob("03*.jsonl")))
    assert result["agent_task_success_accuracy"]["denominator"]==28
    assert result["root_recall"]["denominator"]==15
    assert result["first_fault_joint_recall"]["denominator"]==15
    assert result["high_risk_missed"]==0
    assert result["disagreements"]

def test_network_failure_not_repaired_as_business_success(cases,tmp_path,monkeypatch):
    monkeypatch.setenv("TEST_KEY","test-only")
    judge=Judge({"mode":"live","base_url":"https://test.invalid/v1","model":"test","api_key_env":"TEST_KEY"},tmp_path,"rules")
    def fail(*args,**kwargs):raise httpx.ReadTimeout("test timeout")
    monkeypatch.setattr("invoice_eval.judge.httpx.post",fail)
    assert judge.ask(cases[0],cases[0].runs[0],{"question_id":"q","event_id":cases[0].runs[0].events[-1].event_id}) is None
    assert judge.records[0]["status"]=="failed"
    assert not list(tmp_path.glob("*.json"))

def test_evaluate_never_reads_expert_labels(tmp_path,monkeypatch,local_material):
    from pathlib import Path
    original=Path.open
    def checked(self,*args,**kwargs):
        if self.name.startswith("03_"):raise AssertionError("expert leakage")
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,"open",checked)
    m=evaluate(next((ROOT/"material").glob("04*.jsonl")),ROOT/"configs/default.json",tmp_path/"run")
    assert m["status"]=="success"

def test_repeat_stability(tmp_path,local_material):
    report=stability(next((ROOT/"material").glob("04*.jsonl")),ROOT/"configs/default.json",tmp_path/"stability",2)
    assert report["changed_conclusions"]==0
    assert report["comparisons"]==16
