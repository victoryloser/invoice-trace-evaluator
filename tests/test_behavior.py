"""Behavior coverage uses actual branches, not scenario names or success totals."""
import copy
import json

import pytest

from invoice_eval.behavior import collect_behaviors, summarize_behaviors
from invoice_eval.cli import main
from invoice_eval.decisions import reconcile, synchronize_checks
from invoice_eval.loader import load_cases, write_jsonl
from invoice_eval.models import Case, JudgeResult
from invoice_eval.pipeline import evaluate
from invoice_eval.resources import BEHAVIORS, DEFAULT_CONFIG
from invoice_eval.rules import inspect_run
from test_onboarding import fictional_case


CATALOG = json.loads(BEHAVIORS.read_text(encoding="utf-8"))


def event(step, incoming=None, outgoing=None, status="SUCCEEDED", tools=()):
    return {"step_id": step, "observed_input": incoming or {}, "observed_output": outgoing or {},
            "step_status": status, "tool_calls": list(tools)}


def submit(status="accepted", key="stable", authorized=False):
    return event("SUBMIT_INVOICE", {"retry_authorized": authorized}, tools=[
        {"name": "invoice.submit", "arguments": {"idempotency_key": key}, "result": {"submit_status": status}}])


def case_with(actions, context=None, request=None, final_state="WAITING_USER", created=False):
    raw = fictional_case()
    raw["context"].update(context or {})
    raw["user_request"] = request or raw["user_request"]
    raw["reference_result"] = {"acceptable_snapshot_state": final_state, "invoice_should_exist": created}
    for run in raw["runs"]:
        seed = run["events"][0]
        run["events"] = [dict(copy.deepcopy(seed), **copy.deepcopy(action), seq=i, event_id=f"{run['run_id']}-{i}") for i, action in enumerate(actions, 1)]
        run.update(final_state=final_state, invoice_created=created)
    return Case.model_validate(raw)


def inspect(case, index=0):
    run = case.runs[index]
    analysis = inspect_run(case, run)
    synchronize_checks(analysis)
    rows = collect_behaviors(case, run, analysis, CATALOG, "baseline" if index == 0 else "candidate")
    return {r["behavior_id"]: r for r in rows}, analysis


@pytest.mark.parametrize("confirmed,status", [(False, "fail"), (True, "pass")])
def test_confirmation_boundary_is_covered_even_when_failed(confirmed, status):
    case = case_with([submit()], {"user_confirmation_recorded": confirmed})
    row = inspect(case)[0]["confirmation_gate"]
    assert row["covered"] and row["status"] == status
    assert row["occurrences"][0]["evidence_event_ids"] == ["DEMO-A-1"]


@pytest.mark.parametrize("key,user_text,stop", [
    ("cancel_stop", "取消当前任务", event("CANCEL_TASK", status="CANCELED")),
    ("hold_stop", "暂缓开具发票", event("CONFIRM_INVOICE", status="WAITING_USER")),
])
def test_stop_branches_require_a_response_and_keep_failures(key, user_text, stop):
    waiting = case_with([event("USER_MESSAGE")], request=user_text)
    row = inspect(waiting)[0][key]
    assert row["triggered"] and not row["covered"] and row["status"] == "not_covered"
    good = case_with([stop], request=user_text)
    assert inspect(good)[0][key]["status"] == "pass"
    bad = case_with([stop, submit()], request=user_text)
    assert inspect(bad)[0][key]["status"] == "fail"


def test_resuming_after_cancellation_ends_the_old_condition():
    case = case_with([event("CANCEL_TASK", status="CANCELED"),
                      event("USER_MESSAGE", {"user_update": {"intent": "ISSUE"}}), submit()], request="取消当前任务")
    assert inspect(case)[0]["cancel_stop"]["status"] == "pass"


def test_successful_cancel_action_uses_canceled_result_code():
    action = event("CANCEL_TASK")
    action["result_code"] = "E800"
    case = case_with([action], request="取消当前任务", final_state="CANCELED")
    assert inspect(case)[0]["cancel_stop"]["status"] == "pass"


def test_changed_content_requires_new_confirmation_and_repeated_updates_stay_visible():
    update = event("USER_MESSAGE", {"user_update": {"amount_value": 200}})
    wait = event("CONFIRM_INVOICE", outgoing={"preview": {"amount_value": 200}, "invoice_confirmed": False}, status="WAITING_USER")
    context = {"user_confirmation_recorded": True}
    good = case_with([update, wait], context)
    row = inspect(good)[0]["reconfirm_after_update"]
    assert row["status"] == "pass" and row["occurrences"][0]["trigger_event_ids"] == ["DEMO-A-1"]
    bad = case_with([update, submit()], context)
    assert inspect(bad)[0]["reconfirm_after_update"]["status"] == "fail"
    repeat = case_with([update, wait, event("USER_MESSAGE", {"user_update": {"amount_value": 300}})], context)
    row = inspect(repeat)[0]["reconfirm_after_update"]
    assert row["covered"] and row["status"] == "unknown" and len(row["occurrences"]) == 2
    identical = case_with([event("USER_MESSAGE", {"user_update": {"amount_value": 100}}), submit()], context)
    assert not inspect(identical)[0]["reconfirm_after_update"]["triggered"]


def test_confirmation_on_revised_submission_is_valid():
    action = submit()
    action["observed_input"].update(user_update={"amount_value": 200}, user_confirmation=True)
    case = case_with([action], {"user_confirmation_recorded": True})
    assert inspect(case)[0]["reconfirm_after_update"]["status"] == "pass"


@pytest.mark.parametrize("allow,status", [(False, "pass"), (True, "fail")])
def test_duplicate_risk_in_tool_result_triggers_without_a_scenario_label(allow, status):
    case = case_with([event("VALIDATE_DATA", outgoing={"validation_passed": allow}, tools=[
        {"name": "rules.validate", "arguments": {}, "result": {"risk_flags": ["DUPLICATE_INVOICE"]}}])])
    assert inspect(case)[0]["duplicate_block"]["status"] == status


def test_risk_without_handling_is_not_covered():
    case = case_with([event("RECEIVE_REQUEST")], {"risk_flags": ["DUPLICATE_INVOICE"]})
    row = inspect(case)[0]["duplicate_block"]
    assert row["triggered"] and not row["covered"]


@pytest.mark.parametrize("key,authorized,status", [("stable", True, "pass"), ("stable", False, "fail"), ("changed", True, "fail")])
def test_unknown_acceptance_requires_both_retry_guards(key, authorized, status):
    case = case_with([submit("timeout"), submit(key=key, authorized=authorized)])
    row = inspect(case)[0]["unknown_submit_recovery"]
    assert row["status"] == status
    assert row["occurrences"][0]["evidence_event_ids"] == ["DEMO-A-1", "DEMO-A-2"]


def test_timeout_itself_is_not_recovery_coverage():
    case = case_with([submit("unknown")])
    row = inspect(case)[0]["unknown_submit_recovery"]
    assert row["triggered"] and not row["covered"]
    case = case_with([submit("unknown"), event("TRANSFER_TO_HUMAN")])
    assert inspect(case)[0]["unknown_submit_recovery"]["status"] == "pass"


@pytest.mark.parametrize("step,status", [("FAIL_TASK", "pass"), ("COMPLETE_TASK", "fail")])
def test_failed_callback_needs_subsequent_feedback(step, status):
    callback = event("WAIT_INVOICE_CALLBACK", outgoing={"callback_status": "failed"})
    row = inspect(case_with([callback]))[0]["failed_callback_reporting"]
    assert row["triggered"] and not row["covered"]
    assert inspect(case_with([callback, event(step)]))[0]["failed_callback_reporting"]["status"] == status


def test_unknown_semantic_feedback_is_not_pass_and_can_be_resolved():
    case = case_with([event("WAIT_INVOICE_CALLBACK", outgoing={"callback_status": "failed"}),
                      event("FAIL_TASK", outgoing={"user_message": "已经办妥了"})])
    rows, analysis = inspect(case)
    assert rows["failed_callback_reporting"]["status"] == "unknown"
    question = next(q for q in analysis["questions"] if q["kind"] == "feedback")
    result = JudgeResult(status="success", question_id=question["question_id"], verdict="pass", label=None,
                         first_fault_event_id=None, evidence_event_ids=["DEMO-A-1", "DEMO-A-2"], field_paths=[],
                         explanation="人工构造的语义响应，用于测试状态对接。", missing_evidence=[])
    reconcile(result, question, analysis, case.runs[0])
    rows = collect_behaviors(case, case.runs[0], analysis, CATALOG, "baseline")
    assert next(r for r in rows if r["behavior_id"] == "failed_callback_reporting")["status"] == "pass"


@pytest.mark.parametrize("key,context,user_text,field", [
    ("buyer_clarification", {"buyer_disambiguation_required": True}, None, "buyer_id"),
    ("missing_field_clarification", {"required_missing_fields": ["amount_value"]}, None, "amount_value"),
    ("missing_field_clarification", {"required_missing_fields": ["buyer_id"]}, None, "buyer_id"),
    ("amount_basis_clarification", {"amount_basis_unspecified": True}, None, "amount_basis"),
    ("red_reference_required", {}, "开红票", "original_invoice_id"),
])
def test_clarification_branches_have_positive_and_negative_evidence(key, context, user_text, field):
    good = case_with([event("WAIT_USER_SUPPLEMENT", outgoing={"requested_fields": [field]}, status="WAITING_USER")], context, user_text)
    assert inspect(good)[0][key]["status"] == "pass"
    bad = case_with([submit()], context, user_text)
    assert inspect(bad)[0][key]["status"] == "fail"
    empty = case_with([event("COLLECT_INVOICE_DATA")], context, user_text)
    assert inspect(empty)[0][key]["status"] == "unknown"


def test_clarification_then_user_supplies_field_is_not_a_violation():
    case = case_with([event("WAIT_USER_SUPPLEMENT", outgoing={"requested_fields": ["amount_value"]}),
                      event("USER_MESSAGE", {"user_update": {"amount_value": 200}}), submit()],
                     {"required_missing_fields": ["amount_value"]})
    assert inspect(case)[0]["missing_field_clarification"]["status"] == "pass"


@pytest.mark.parametrize("duplicated,status", [(False, "pass"), (True, "fail")])
def test_batch_compares_contents_and_multiplicity_not_just_count(duplicated, status):
    lines = [{"item_id": "ITEM-900", "exclusive_amount": 100}, {"item_id": "ITEM-900", "exclusive_amount": 200}]
    observed = [lines[0], lines[0]] if duplicated else list(reversed(lines))
    case = case_with([event("COLLECT_INVOICE_DATA", outgoing={"line_items": observed})], {"line_items": lines})
    assert inspect(case)[0]["batch_integrity"]["status"] == status


def test_scenario_and_plan_cannot_fake_coverage_or_change_scores(tmp_path):
    raw = fictional_case()
    raw["scenario"] = "取消、超时重试、失败回调、批量开票"
    raw["expected_behaviors"] = ["cancel_stop", "unknown_submit_recovery"]
    case = Case.model_validate(raw)
    rows = [r for index in range(2) for r in inspect(case, index)[0].values()]
    report = summarize_behaviors(rows, CATALOG)
    assert report["overall"]["coverage"] == {"numerator": 1, "denominator": 12, "value": 1/12}
    assert len(report["overall"]["planned_but_uncovered"]) == 4
    assert report["by_role"]["baseline"]["passed_behaviors"] == 1
    assert report["by_role"]["candidate"]["passed_behaviors"] == 0
    assert report["overall"]["behavior_pass_rate"]["denominator"] == 1
    assert report["overall"]["run_behavior_pass_rate"]["value"] == .5
    assert summarize_behaviors(rows * 3, CATALOG)["overall"]["coverage"] == report["overall"]["coverage"]
    plain = fictional_case()
    for label, source in [("plain", plain), ("planned", raw)]:
        path = tmp_path / f"{label}.jsonl"
        write_jsonl(path, [source])
        evaluate(path, DEFAULT_CONFIG, tmp_path / label)
    assert (tmp_path / "plain/submission.jsonl").read_bytes() == (tmp_path / "planned/submission.jsonl").read_bytes()


def test_no_coverage_has_no_invented_pass_rate():
    case = case_with([event("RECEIVE_REQUEST")])
    report = summarize_behaviors(list(inspect(case)[0].values()), CATALOG)
    assert report["overall"]["coverage"]["value"] == 0
    assert report["overall"]["behavior_pass_rate"]["value"] is None


@pytest.mark.parametrize("planned", [["typo"], ["cancel_stop", "cancel_stop"], "cancel_stop"])
def test_bad_plan_is_rejected_with_line_number(tmp_path, planned):
    raw = fictional_case()
    raw["expected_behaviors"] = planned
    source = tmp_path / "bad.jsonl"
    write_jsonl(source, [raw])
    with pytest.raises(ValueError, match=r"bad.jsonl:1"):
        load_cases(source)


def test_artifacts_include_pending_runs_and_report_regeneration(tmp_path):
    raw = fictional_case()
    raw["runs"][0].pop("invoice_created")
    source = tmp_path / "source.jsonl"
    write_jsonl(source, [raw])
    run = tmp_path / "run"
    manifest = evaluate(source, DEFAULT_CONFIG, run)
    assert manifest["status"] == "needs_review"
    assert manifest["behavior_catalog_version"] == "1.0"
    assert {"behavior_coverage.json", "behavior_coverage.md", "behavior_catalog.snapshot.json"} <= set(manifest["artifacts"])
    report = json.loads((run / "behavior_coverage.json").read_text(encoding="utf-8"))
    assert len(report["observations"]) == 24
    assert report["by_role"]["baseline"]["behaviors"][0]["status"] == "unknown"
    assert report["by_role"]["candidate"]["behaviors"][0]["status"] == "fail"
    assert "业务行为覆盖" in (run / "report.md").read_text(encoding="utf-8")
    before = (run / "behavior_coverage.md").read_bytes()
    assert main(["report", "--run", str(run)]) == 0
    assert before == (run / "behavior_coverage.md").read_bytes()
    target = tmp_path / "catalog.json"
    assert main(["behavior-catalog", "--output", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8")) == CATALOG
    from invoice_eval.timeline import render_timeline
    html = render_timeline(run, tmp_path / "timeline.html").read_text(encoding="utf-8")
    assert "当前任务的行为与证据" in html and 'id="behaviors"' in html


def test_reordered_events_and_renamed_scenarios_preserve_evidence():
    case = case_with([submit("timeout"), submit(authorized=True)])
    expected = inspect(case)[0]
    case.runs[0].events.reverse()
    case.scenario = "completely unrelated"
    assert inspect(case)[0] == expected
