"""Recorded outcomes retain provenance and never imply a live service check."""
import copy
import json

import pytest

from invoice_eval.business_evidence import business_evidence_report, invoice_evidence, render_business_evidence
from invoice_eval.export import validate_records
from invoice_eval.loader import load_cases, read_jsonl, write_jsonl
from invoice_eval.models import Case
from invoice_eval.pipeline import evaluate
from invoice_eval.report import render_report
from invoice_eval.resources import DEFAULT_CONFIG, OUTPUT_SCHEMA
from invoice_eval.rules import inspect_run
from invoice_eval.timeline import render_timeline
from test_onboarding import evaluate_fictional, fictional_case


def case():
    return Case.model_validate(fictional_case())


def no_outcome(run):
    run.invoice_created = None
    for event in run.events:
        event.observed_output.pop("callback_status", None)
        event.tool_calls = [t for t in event.tool_calls if t.name != "invoice.callback"]


@pytest.mark.parametrize("created", [True, False])
def test_run_summary_is_recorded_input_not_verified_external_fact(created):
    run = case().runs[0]
    run.invoice_created = created
    result = invoice_evidence(run)
    assert result["status"] == "consistent" and result["invoice_created"] is created
    assert result["sources"] == [{"kind": "run_summary", "source_path": "/invoice_created", "event_id": None,
                                  "recorded_at": None, "recorded_value": created, "invoice_created": created}]
    assert result["external_verification"] == {"status": "not_performed", "invoice_created": None}


@pytest.mark.parametrize("status,created", [("success", True), ("failed", False)])
@pytest.mark.parametrize("kind", ["output", "tool"])
def test_callback_sources_are_explicit_with_recoverable_paths(kind, status, created):
    raw = fictional_case()
    run = raw["runs"][0]
    run["invoice_created"] = None
    event = run["events"][1]
    if kind == "output":
        event["observed_output"]["callback_status"] = status
    else:
        event["tool_calls"] = [{"name": "invoice.callback", "arguments": {}, "result": {"status": status}}]
    parsed = Case.model_validate(raw).runs[0]
    result = invoice_evidence(parsed)
    assert result["invoice_created"] is created
    source = result["sources"][0]
    assert source["event_id"] == event["event_id"]
    value = parsed.model_dump()
    for part in source["source_path"].split("/")[1:]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    assert value == status
    assert result["external_verification"]["status"] == "not_performed"


def test_source_indices_refer_to_original_event_array_not_sorted_position():
    run = case().runs[1]
    run.events.reverse()
    result = invoice_evidence(run)
    source = next(s for s in result["sources"] if s["kind"] == "callback_output")
    assert source["source_path"] == "/events/1/observed_output/callback_status"
    assert source["event_id"] == "DEMO-B-6"


def test_agent_success_and_submit_acceptance_do_not_supply_missing_outcome():
    c = case()
    run = c.runs[1]
    no_outcome(run)
    result = invoice_evidence(run)
    assert result["status"] == "missing" and result["invoice_created"] is None
    assert result["sources"] == []
    assert len(result["non_decisive_signals"]) == 2
    assert all(s["used_for_conclusion"] is False for s in result["non_decisive_signals"])
    analysis = inspect_run(c, run)
    assert analysis["created"] is None and analysis["review"]


def test_conflicting_sources_are_unknown_even_with_more_positive_records():
    c = case()
    run = c.runs[1]
    run.invoice_created = False
    run.events[0].observed_output["callback_status"] = "success"
    result = invoice_evidence(run)
    assert [s["invoice_created"] for s in result["sources"]] == [True, True, False]
    assert result["status"] == "conflicting" and result["invoice_created"] is None
    assert inspect_run(c, run)["created"] is None


def test_failure_callback_overrides_neither_summary_nor_claim():
    run = case().runs[1]
    run.invoice_created = False
    run.events[-2].observed_output["callback_status"] = "failed"
    result = invoice_evidence(run)
    assert result["status"] == "consistent" and result["invoice_created"] is False
    assert any(s["recorded_value"] is True for s in result["non_decisive_signals"])


def test_input_attestation_cannot_forge_external_verification():
    raw = fictional_case()
    raw["runs"][0].update(external_verification={"status": "verified", "invoice_created": True},
                          business_invoice_evidence={"scope": "external_service"})
    run = Case.model_validate(raw).runs[0]
    assert invoice_evidence(run)["external_verification"] == {"status": "not_performed", "invoice_created": None}


def test_summary_counts_unknowns_and_does_not_turn_no_verification_into_zero_invoices():
    c = case()
    no_outcome(c.runs[0])
    result = business_evidence_report([c])
    s = result["summary"]
    assert s["input_runs"] == 2
    assert s["trace_created"] == s["trace_unknown"] == 1
    assert s["trace_not_created"] == 0
    assert s["external_verified_runs"] == 0 and s["external_unverified_runs"] == 2
    assert s["externally_verified_invoice_created"] is None
    assert result["by_role"]["baseline"]["trace_unknown"] == 1


def test_pipeline_exports_evidence_consistently_without_changing_conclusions(tmp_path):
    source, directory = evaluate_fictional(tmp_path)
    result = json.loads((directory / "business_evidence.json").read_text(encoding="utf-8"))
    predictions = read_jsonl(directory / "submission.jsonl")[0]["run_assessments"]
    for a, row in zip(predictions, result["runs"]):
        evidence = {k: v for k, v in row.items() if k not in {"case_id", "run_id", "role"}}
        assert a["business_invoice_evidence"] == evidence
        assert a["business_invoice_created"] == evidence["invoice_created"]
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["business_evidence"] == result["summary"]
    assert metrics["service_created"]["external_verification"] == "not_performed"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert "business_evidence.json" in manifest["artifacts"]
    assert "business_evidence.md" in manifest["artifacts"]
    assert manifest["business_evidence"]["external_verification"] == "not_performed"
    assert validate_records(load_cases(source), read_jsonl(directory / "submission.jsonl"), OUTPUT_SCHEMA)["valid"]


def test_unresolved_run_still_has_business_evidence(tmp_path):
    raw = fictional_case()
    raw["runs"][0]["invoice_created"] = None
    source, directory = tmp_path / "input.jsonl", tmp_path / "run"
    write_jsonl(source, [raw])
    manifest = evaluate(source, DEFAULT_CONFIG, directory)
    assert manifest["status"] == "needs_review"
    evidence = json.loads((directory / "business_evidence.json").read_text(encoding="utf-8"))
    assert evidence["summary"]["input_runs"] == 2 and evidence["summary"]["trace_unknown"] == 1
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["service_created"]["denominator"] == 1
    assert len(read_jsonl(directory / "run_assessments.jsonl")) == 1
    assert not (directory / "submission.jsonl").exists()


@pytest.mark.parametrize("tamper", ["verified", "source", "outcome"])
def test_export_rejects_invented_provenance_or_inconsistent_outcome(tmp_path, tamper):
    source, directory = evaluate_fictional(tmp_path)
    rows = read_jsonl(directory / "submission.jsonl")
    a = rows[0]["run_assessments"][0]
    if tamper == "verified":
        a["business_invoice_evidence"]["external_verification"]["status"] = "verified"
    elif tamper == "source":
        a["business_invoice_evidence"]["sources"][0]["source_path"] = "/invented"
    else:
        a["business_invoice_created"] = not a["business_invoice_created"]
    with pytest.raises(ValueError, match="business invoice evidence"):
        validate_records(load_cases(source), rows, OUTPUT_SCHEMA)


def test_reports_distinguish_recorded_outcomes_and_unverified_state(tmp_path):
    _, directory = evaluate_fictional(tmp_path)
    md = (directory / "business_evidence.md").read_text(encoding="utf-8")
    assert "外部真实出票数：未知（不是 0）" in md
    report = (directory / "report.md").read_text(encoding="utf-8")
    assert "轨迹记录出票" in report and "business_evidence.md" in report
    html = render_timeline(directory, tmp_path / "timeline.html").read_text(encoding="utf-8")
    assert 'id="business-boundary"' in html
    assert "实际出票" not in html and "renderBusinessEvidence" in html
    assert "独立外部核验 0 / 2" in html


def test_historical_reports_remain_readable_without_provenance(tmp_path):
    _, directory = evaluate_fictional(tmp_path)
    (directory / "business_evidence.json").unlink()
    rows = read_jsonl(directory / "run_assessments.jsonl")
    for row in rows:
        row["assessment"].pop("business_invoice_evidence")
    write_jsonl(directory / "run_assessments.jsonl", rows)
    html = render_timeline(directory, tmp_path / "legacy.html").read_text(encoding="utf-8")
    assert "历史产物未保存逐条出票来源说明" in html
    assert "历史产物未保存逐条出票来源说明" in render_report(directory).read_text(encoding="utf-8")


def test_markdown_escapes_untrusted_identifiers():
    result = business_evidence_report([case()])
    result["runs"][0]["case_id"] = "<script>|case"
    text = render_business_evidence(result)
    assert "<script>" not in text and "&#124;" in text
