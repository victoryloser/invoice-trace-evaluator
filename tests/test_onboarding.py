"""Data-independent regressions with tiny fictional traces generated in memory."""
import copy
import json

import pytest

from invoice_eval.adapters import adapt
from invoice_eval.cli import main
from invoice_eval.export import validate_records
from invoice_eval.loader import load_cases, read_jsonl, write_jsonl
from invoice_eval.pipeline import evaluate
from invoice_eval.resources import DEFAULT_CONFIG, OUTPUT_SCHEMA
from invoice_eval.models import Case
from invoice_eval.timeline import render_timeline


def fictional_case():
    """Generate a waiting baseline and an unconfirmed candidate submission."""
    request = "给客户CUST-900开测试服务费发票，不含税100元，开票前请确认。"
    amounts = {"exclusive_amount":"100.00", "tax_amount":"6.00", "inclusive_amount":"106.00"}
    preview = {"buyer_id":"CUST-900", "item_id":"ITEM-900", "invoice_task_type":"BLUE",
               "amount_value":100, "amount_basis":"tax_exclusive", "amounts":amounts}
    runs = []
    for suffix in ("A", "B"):
        prefix = "DEMO-"+suffix
        actions = [
            ("RECEIVE_REQUEST", {"user_request":request}, {}),
            ("PARSE_INTENT", {}, {"intent":"ISSUE", "invoice_task_type":"BLUE"}),
            ("COLLECT_INVOICE_DATA", {}, {**preview, "computed_amounts":amounts}),
            ("CONFIRM_INVOICE", {}, {"preview":preview, "invoice_confirmed":False})]
        if suffix == "B":
            actions.extend([("SUBMIT_INVOICE", preview, {}),
                ("WAIT_INVOICE_CALLBACK", {}, {"callback_status":"success"}),
                ("COMPLETE_TASK", {}, {"invoice_created":True})])
        events = []
        for seq, (step, incoming, outgoing) in enumerate(actions, 1):
            calls = [{"name":"invoice.submit", "arguments":copy.deepcopy(preview), "result":{"submit_status":"accepted"}, "attempt":1}] if step == "SUBMIT_INVOICE" else []
            events.append({"event_id":f"{prefix}-{seq}", "seq":seq, "step_id":step,
                "task_id":prefix, "trace_id":prefix, "module":"invoice", "action":step,
                "step_status":"WAITING_USER" if suffix == "A" and seq == 4 else "SUCCEEDED",
                "started_at":f"2026-01-01T10:00:{seq:02d}+08:00", "finished_at":f"2026-01-01T10:00:{seq:02d}+08:00",
                "duration_ms":0, "result_code":"E200" if step == "COMPLETE_TASK" else "E100",
                "error_code":None, "error_message":None, "retry_count":0,
                "observed_input":copy.deepcopy(incoming), "observed_output":copy.deepcopy(outgoing), "tool_calls":calls})
        runs.append({"run_id":prefix, "agent_version":"baseline" if suffix == "A" else "candidate",
            "task_id":prefix, "trace_id":prefix, "events":events,
            "final_state":"WAITING_USER" if suffix == "A" else "SUCCEEDED", "invoice_created":suffix == "B"})
    return {"case_id":"DEMO-001", "user_request":request, "scenario":"未确认即提交",
        "context":{"buyer_candidates":[{"buyer_id":"CUST-900"}],
                   "item_candidates":[{"item_id":"ITEM-900", "display_name":"测试服务费", "configured_rate":0.06}],
                   "user_confirmation_recorded":False},
        "reference_result":{"acceptable_snapshot_state":"WAITING_USER", "invoice_should_exist":False}, "runs":runs}


def evaluate_fictional(tmp_path):
    source = tmp_path/"input.jsonl"
    write_jsonl(source, [fictional_case()])
    output = tmp_path/"evaluation"
    manifest = evaluate(source, DEFAULT_CONFIG, output)
    assert manifest["status"] == "success"
    return source, output


def test_data_free_pipeline_detects_confirmation_bypass(tmp_path):
    source, output = evaluate_fictional(tmp_path)
    records = read_jsonl(output/"submission.jsonl")
    baseline, candidate = records[0]["run_assessments"]
    assert baseline["agent_task_success"] and not baseline["business_invoice_created"]
    assert not candidate["agent_task_success"] and candidate["business_invoice_created"]
    assert candidate["root_causes"][0]["label"] == "CONFIRMATION_BYPASS"
    assert candidate["root_causes"][0]["first_fault_event_id"] == "DEMO-B-5"
    assert validate_records(load_cases(source), records, OUTPUT_SCHEMA)["case_count"] == 1


@pytest.mark.parametrize("kind", ["duplicate_case", "duplicate_run", "duplicate_event", "duplicate_seq", "missing_run", "bad_timestamp", "wrong_type"])
def test_invalid_trace_is_rejected(tmp_path, kind):
    case = fictional_case()
    rows = [case]
    if kind == "duplicate_case": rows.append(copy.deepcopy(case))
    elif kind == "duplicate_run": case["runs"][1]["run_id"] = case["runs"][0]["run_id"]
    elif kind == "duplicate_event": case["runs"][1]["events"][0]["event_id"] = case["runs"][0]["events"][0]["event_id"]
    elif kind == "duplicate_seq": case["runs"][0]["events"][1]["seq"] = 1
    elif kind == "missing_run": case["runs"].pop()
    elif kind == "bad_timestamp": case["runs"][0]["events"][0]["finished_at"] = "invalid"
    else: case["runs"][0]["events"][0]["seq"] = "1"
    source = tmp_path/"invalid.jsonl"
    write_jsonl(source, rows)
    with pytest.raises(ValueError): load_cases(source)


@pytest.mark.parametrize("format_name", ["canonical", "envelope", "otel"])
def test_adapters_preserve_fictional_evidence(tmp_path, format_name):
    case = fictional_case()
    if format_name == "canonical": row = case
    elif format_name == "envelope": row = {"task":{k:v for k,v in case.items() if k != "runs"}, "trajectories":case["runs"]}
    else:
        row = {"case":{k:v for k,v in case.items() if k != "runs"},
            "run_metadata":[{k:v for k,v in run.items() if k != "events"} for run in case["runs"]],
            "resourceSpans":[{"scopeSpans":[{"spans":[{"attributes":[
                {"key":"invoice.run_id", "value":{"stringValue":run["run_id"]}},
                {"key":"invoice.event_json", "value":{"stringValue":json.dumps(event)}}
            ]} for run in case["runs"] for event in run["events"]]}]}]}
    source, target = tmp_path/"transport.jsonl", tmp_path/"normalized.jsonl"
    write_jsonl(source, [row])
    adapt(source, format_name, target)
    assert [c.model_dump() for c in load_cases(target)] == [Case.model_validate(case).model_dump()]


def test_missing_input_does_not_create_run_directory(tmp_path, capsys):
    output = tmp_path/"run"
    assert main(["evaluate", "--cases", str(tmp_path/"missing.jsonl"), "--output", str(output)]) == 2
    assert not output.exists()
    assert "Trace file not found" in capsys.readouterr().out


def test_schema_command_needs_no_data(tmp_path):
    target = tmp_path/"input.schema.json"
    assert main(["schema", "--output", str(target)]) == 0
    value = json.loads(target.read_text(encoding="utf-8"))
    assert set(value["required"]) >= {"case_id", "user_request", "context", "reference_result", "runs"}


def test_cli_evaluate_validate_and_timeline(tmp_path):
    source = tmp_path/"source.jsonl"
    write_jsonl(source, [fictional_case()])
    output = tmp_path/"run"
    assert main(["evaluate", "--cases", str(source), "--output", str(output)]) == 0
    assert main(["validate", "--cases", str(source), "--submission", str(output/"submission.jsonl")]) == 0
    assert main(["timeline", "--run", str(output), "--output", str(tmp_path/"timeline.html")]) == 0
    assert "DEMO-B-5" in (tmp_path/"timeline.html").read_text(encoding="utf-8")


def test_unknown_service_outcome_requires_review(tmp_path):
    case = fictional_case()
    case["runs"][0].pop("invoice_created")
    path = tmp_path/"unknown.jsonl"
    write_jsonl(path, [case])
    output = tmp_path/"run"
    assert evaluate(path, DEFAULT_CONFIG, output)["status"] == "needs_review"
    assert not (output/"submission.jsonl").exists()


def test_timeline_escapes_untrusted_trace_text(tmp_path):
    case = fictional_case()
    case["context"]["note"] = "</script><script>alert('unsafe')</script>"
    source = tmp_path/"input.jsonl"
    write_jsonl(source, [case])
    evaluate(source, DEFAULT_CONFIG, tmp_path/"run")
    target = render_timeline(tmp_path/"run", tmp_path/"timeline.html")
    assert "</script><script>alert" not in target.read_text(encoding="utf-8")
