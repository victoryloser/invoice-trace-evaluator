import copy
import pytest
from invoice_eval.rules import inspect_run
from invoice_eval.scoring import assessment, compare
from invoice_eval.state import amounts

def result(case, index, config):
    run = case.runs[index]
    analysis = inspect_run(case, run)
    assert not analysis["review"]
    assert not analysis["questions"]
    return assessment(run, analysis, config)

@pytest.mark.parametrize("index,bad_run,labels", [(0,1,{"AMOUNT_BASIS","CONFIRMATION_BYPASS"}),(1,0,{"AMOUNT_BASIS"}),(2,1,{"FIELD_FABRICATION"}),(3,0,{"ENTITY_RESOLUTION"}),(4,1,{"DUPLICATE_CONTROL"}),(5,0,{"INTENT_CLASSIFICATION"}),(6,1,{"MULTI_ITEM_OMISSION"}),(7,1,{"TOOL_ERROR_HANDLING"}),(8,0,{"INTENT_CLASSIFICATION","CONFIRMATION_BYPASS"}),(9,1,{"CALLBACK_MISINTERPRETATION"}),(10,1,{"CONFIRMATION_BYPASS"}),(11,0,{"PRODUCT_MATCH"}),(12,0,{"AMOUNT_CALCULATION"}),(13,1,{"TOOL_ARGUMENT_ERROR"})])
def test_public_business_constraints(cases,config,index,bad_run,labels):
    bad=result(cases[index],bad_run,config)
    good=result(cases[index],1-bad_run,config)
    assert {c["label"] for c in bad["root_causes"]} == labels
    assert not bad["agent_task_success"]
    assert good["agent_task_success"]

def test_propagation_and_independent_risk(cases,config):
    bad=result(cases[0],1,config)
    assert bad["quality_score"] == 20
    assert len(bad["penalty_items"]) == 2
    basis=bad["root_causes"][0]
    assert basis["first_fault_event_id"].endswith("-05")
    assert any(e.endswith("-08") for e in basis["evidence_event_ids"])

@pytest.mark.parametrize("value,basis,rate,expected", [(100,"tax_exclusive",0.06,{"exclusive_amount":"100.00","tax_amount":"6.00","inclusive_amount":"106.00"}),(106,"tax_inclusive",0.06,{"exclusive_amount":"100.00","tax_amount":"6.00","inclusive_amount":"106.00"}),(0.05,"tax_exclusive",0.1,{"exclusive_amount":"0.05","tax_amount":"0.01","inclusive_amount":"0.06"})])
def test_decimal(value,basis,rate,expected):
    assert amounts(value,basis,rate)==expected

def test_reorder_and_irrelevant_event(cases,config):
    c=cases[0]
    before=result(c,1,config)
    extra=c.runs[1].events[0].model_copy(deep=True)
    extra.event_id="unrelated"
    extra.seq=99
    extra.step_id="OBSERVATION"
    extra.observed_output={"note":"unrelated"}
    c.runs[1].events.append(extra)
    c.runs[1].events.reverse()
    after=result(c,1,config)
    assert after["root_causes"]==before["root_causes"]
    assert after["quality_score"]==before["quality_score"]

def test_renaming_has_no_effect(cases,config):
    c=cases[0]
    before=result(c,1,config)
    c.case_id="opaque"
    c.scenario="unrelated"
    for r in c.runs:
        r.run_id="new-"+r.run_id
        for e in r.events:
            e.event_id="new-"+e.event_id
    after=result(c,1,config)
    assert after["quality_score"]==before["quality_score"]
    assert [x["label"] for x in after["root_causes"]]==[x["label"] for x in before["root_causes"]]

def test_two_independent_business_errors(cases,config):
    c=cases[0]
    r=c.runs[0]
    r.events[4].observed_output["buyer_id"]="wrong-buyer"
    r.events[4].observed_output["computed_amounts"]["tax_amount"]=1
    a=result(c,0,config)
    assert {x["label"] for x in a["root_causes"]}=={"ENTITY_RESOLUTION","AMOUNT_CALCULATION"}
    assert a["quality_score"]==40

def test_same_count_different_batch_contents(cases,config):
    c=cases[6]
    c.runs[0].events[4].observed_output["line_items"][1]=copy.deepcopy(c.runs[0].events[4].observed_output["line_items"][0])
    a=result(c,0,config)
    assert a["root_causes"][0]["label"]=="MULTI_ITEM_OMISSION"

def test_late_update_not_backdated_and_invalidates_confirmation(cases,config):
    c=cases[1]
    r=c.runs[1]
    # At submission a new user instruction arrives. Reference is final-only.
    c.reference_result["amount_value"]=2000
    r.events[7].observed_input["user_update"]={"amount_value":2000}
    analysis=inspect_run(c,r)
    assert analysis["states"][4]["target"]["amount_value"]["value"]==1060
    assert analysis["states"][7]["target"]["amount_value"]["value"]==2000
    assert not analysis["states"][7]["confirmed"]
    assert any(f.rule_id=="C01" and f.first_fault_event_id==r.events[7].event_id for f in analysis["findings"])

def test_confirmation_arrives_before_submit(cases,config):
    c=cases[10]
    r=c.runs[1]
    r.events[7].observed_input["user_confirmation"]=True
    analysis=inspect_run(c,r)
    assert not any(f.rule_id=="C01" for f in analysis["findings"])

def test_stable_idempotency_alone_not_sufficient(cases):
    c=cases[7]; r=c.runs[1]
    for t in r.events[7].tool_calls:t.arguments["idempotency_key"]="fixed"
    assert any(f.rule_id=="R02" for f in inspect_run(c,r)["findings"])
    r.events[7].observed_input["retry_authorized"]=True
    assert not any(f.rule_id=="R02" for f in inspect_run(c,r)["findings"])

def test_conflicting_service_facts_block(cases):
    c=cases[1]; r=c.runs[1]
    r.invoice_created=False
    a=inspect_run(c,r)
    assert a["created"] is None
    assert a["review"]

def test_hold_does_not_become_cancel(cases):
    c=cases[0]; c.user_request=c.user_request.replace("开票前先让我确认", "暂缓开具")
    a=inspect_run(c,c.runs[0])
    assert a["states"][0]["target"]["intent"]["value"]=="HOLD"

def test_callback_first_fault_requires_misreport(cases,config):
    a=result(cases[9],1,config)
    assert a["root_causes"][0]["first_fault_event_id"].endswith("-10")

def test_equal_bad_candidates_defer(cases,config):
    bad=result(cases[0],1,config)
    assert compare(bad,bad)["direction"]=="equal"
    assert compare(bad,bad)["release_recommendation"]=="defer"
