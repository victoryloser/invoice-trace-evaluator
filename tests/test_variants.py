import json
from invoice_eval.rules import inspect_run
from invoice_eval.scoring import assessment, compare, release_gate
from invoice_eval.models import Finding
from invoice_eval.judge import blind_packet
from invoice_eval.loader import load_cases
from invoice_eval.pipeline import ROOT

def test_changed_amount_and_rate_full_trace(cases,config):
    c=cases[1]
    c.user_request="给客户CUST-022开软件服务费专票，含税113元。本次已确认。"
    c.context["item_candidates"][0]["configured_rate"]=0.13
    expected={"exclusive_amount":100,"tax_amount":13,"inclusive_amount":113}
    c.reference_result.update(amount_value=113,expected_amounts=expected)
    for r in c.runs:
        r.events[0].observed_input["user_request"]=c.user_request
        d=r.events[4].observed_output
        d.update(amount_value=113,amount_basis="tax_inclusive",computed_amounts=expected,line_items=[{"item_id":"ITEM-006","exclusive_amount":100}])
        preview=r.events[6].observed_output["preview"]
        preview.update(amounts=expected,line_items=d["line_items"])
        r.events[7].observed_input.update(amounts=expected,line_items=d["line_items"])
        r.events[7].tool_calls[0].arguments["amounts"]=expected
        a=inspect_run(c,r)
        assert not a["review"] and not a["findings"] and not a["questions"]

def test_same_label_independent_fields_not_merged(cases,config):
    c=cases[2];r=c.runs[1]
    c.context["required_missing_fields"].append("original_invoice_id")
    r.events[4].observed_output["original_invoice_id"]="fabricated-original"
    a=assessment(r,inspect_run(c,r),config)
    assert [x["label"] for x in a["root_causes"]]==["FIELD_FABRICATION","FIELD_FABRICATION"]
    assert a["quality_score"]==40

def test_required_judge_prefix_has_no_future_events(cases):
    c=cases[0];r=c.runs[1]
    packet,q,_=blind_packet(c,r,{"question_id":"prefix","event_id":r.events[4].event_id})
    assert len(packet["events"])==5
    assert all(e["seq"]<=5 for e in packet["events"].values())

def test_cancel_after_confirmation_is_update(cases):
    c=cases[1];r=c.runs[1]
    r.events[7].observed_input["user_update"]={"intent":"CANCEL"}
    a=inspect_run(c,r)
    cancellation=[f for f in a["findings"] if f.rule_id=="R03"]
    assert cancellation and cancellation[0].label=="CONTEXT_UPDATE"
    assert cancellation[0].first_fault_event_id==r.events[7].event_id

def test_different_risks_not_ranked_by_average(cases,config):
    one=assessment(cases[7].runs[1],inspect_run(cases[7],cases[7].runs[1]),config)
    two=assessment(cases[10].runs[1],inspect_run(cases[10],cases[10].runs[1]),config)
    assert compare(one,two)["direction"]=="insufficient_evidence"

def test_release_requires_preregistered_independent_gate(cases,config):
    good=assessment(cases[0].runs[0],inspect_run(cases[0],cases[0].runs[0]),config)
    record={"case_id":"registered","version_comparison":compare(good,good)}
    assert release_gate([record],1,0,{"required_cases":[],"independent_regression_passed":False})=="gray"
    assert release_gate([record],1,0,{"required_cases":["registered"],"independent_regression_passed":True})=="gray"
    assert release_gate([record],1,0,{"required_cases":["registered"],"verified_evidence":{"verified":True}})=="release"
    assert release_gate([record],1,1,{"required_cases":["registered"],"independent_regression_passed":True})=="investigate"

def test_red_original_pending_basis_is_not_fabrication(config,local_material):
    c=load_cases(next((ROOT/"material").glob("04*.jsonl")))[2]
    good=inspect_run(c,c.runs[0])
    bad=inspect_run(c,c.runs[1])
    assert not good["findings"] and not good["questions"]
    roots=assessment(c.runs[1],bad,config)["root_causes"]
    assert len(roots)==1 and roots[0]["label"]=="FIELD_FABRICATION"

def test_text_update_source_points_to_real_input(cases):
    c=cases[1];r=c.runs[1]
    e=r.events[7]
    e.step_id="USER_MESSAGE"
    e.observed_input={"user_request":"取消当前任务"}
    state=inspect_run(c,r)["states"][7]
    assert state["target"]["intent"]["source_path"].endswith("observed_input.user_request")

def test_colloquial_cancellation_requires_semantic_judge(cases):
    c=cases[8]
    c.user_request="这单就算了吧，别往下弄了。"
    analysis=inspect_run(c,c.runs[0])
    assert {q["kind"] for q in analysis["questions"]}>={"intent","intent_protection"}
    assert not any(f.rule_id=="I01" for f in analysis["findings"])

def test_unstructured_feedback_checked_even_with_failed_step(cases):
    c=cases[9]
    c.runs[0].events[-1].observed_output["user_message"]="这张已经办妥，可以去下载啦。"
    analysis=inspect_run(c,c.runs[0])
    assert any(q["kind"]=="feedback" for q in analysis["questions"])

def test_colloquial_amount_revision_inherits_basis(cases):
    c=cases[1];r=c.runs[1]
    e=r.events[7];e.step_id="USER_MESSAGE"
    e.observed_input={"user_request":"金额改成2500元"}
    state=inspect_run(c,r)["states"][7]
    assert state["target"]["amount_value"]["value"]==2500
    assert state["target"]["amount_basis"]["value"]=="tax_inclusive"
