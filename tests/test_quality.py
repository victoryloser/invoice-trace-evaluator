"""Classifier metrics distinguish missed risks, false alarms and abstentions."""
import copy
import json

import pytest

from invoice_eval.cli import main
from invoice_eval.loader import write_jsonl
from invoice_eval.metrics import compare_labels, write_quality_report
from invoice_eval.models import Case, LABELS
from invoice_eval.pipeline import evaluate
from invoice_eval.quality import proportion
from invoice_eval.resources import DEFAULT_CONFIG
from test_onboarding import fictional_case


RISK = "CONFIRMATION_BYPASS"


def root(label=RISK, first="event-1"):
    return {"label": label, "first_fault_event_id": first}


def assessment(run_id, causes=()):
    return {"run_id": run_id, "agent_task_success": not causes,
            "business_invoice_created": False, "root_causes": list(causes)}


def record(case_id, runs, reference=False):
    return {"case_id": case_id, "run_assessments": runs,
            **({"version_direction_on_this_case": "equal"} if reference else {"version_comparison": {"direction": "equal"}})}


def compare(tmp_path, gold, predictions, **kwargs):
    write_jsonl(tmp_path / "labels.jsonl", gold)
    write_jsonl(tmp_path / "prediction.jsonl", predictions)
    return compare_labels(tmp_path / "prediction.jsonl", tmp_path / "labels.jsonl", **kwargs)


def test_confusion_rates_have_distinct_denominators_and_keep_abstentions(tmp_path):
    pairs = [(True, True), (True, False), (False, True), (False, False), (True, None), (False, None)]
    gold, predictions = [], []
    for i, (expected, observed) in enumerate(pairs):
        gold.append(record(str(i), [assessment(str(i), [root()] if expected else [])], True))
        if observed is not None:
            predictions.append(record(str(i), [assessment(str(i), [root()] if observed else [])]))
    result = compare(tmp_path, gold, predictions)
    c = result["quality"]["per_risk"][RISK]
    assert c["confusion"] == dict(tp=1, fp=1, fn=1, tn=1)
    assert c["support"]["reference_positive"] == c["support"]["reference_negative"] == 3
    assert c["support"]["prediction_unknown_positive"] == c["support"]["prediction_unknown_negative"] == 1
    assert c["miss_rate"]["value"] == c["false_positive_rate"]["value"] == c["false_discovery_rate"]["value"] == .5
    assert c["end_to_end_miss_rate"]["value"] == 2/3
    assert c["assessment_coverage"]["value"] == 4/6
    assert result["coverage"]["denominator"] == 6
    assert result["high_risk_missed"] == 2
    assert {e["kind"] for e in c["examples"]} == {"fn", "fp", "prediction_unknown"}


def test_false_positive_rate_is_not_false_discovery_rate(tmp_path):
    gold = [record(str(i), [assessment(str(i))], True) for i in range(5)]
    predictions = [record(str(i), [assessment(str(i), [root()] if i == 0 else [])]) for i in range(5)]
    c = compare(tmp_path, gold, predictions)["quality"]["per_risk"][RISK]
    assert c["false_positive_rate"]["value"] == .2
    assert c["false_discovery_rate"]["value"] == 1
    assert c["miss_rate"]["value"] is None and c["miss_rate"]["confidence_interval"] is None


def test_empty_predictions_are_not_correct_negatives(tmp_path):
    gold = [record("case", [assessment("positive", [root()]), assessment("negative")], True)]
    result = compare(tmp_path, gold, [])
    c = result["quality"]["per_risk"][RISK]
    assert c["confusion"] == dict(tp=0, fp=0, fn=0, tn=0)
    assert c["end_to_end_miss_rate"]["value"] == 1
    assert c["false_positive_rate"]["value"] is None
    assert result["coverage"]["value"] == 0
    assert result["input_integrity"]["missing_prediction_runs"] == ["negative", "positive"]


def test_wilson_interval_retains_uncertainty_for_zero_errors():
    zero = proportion(0, 1)
    assert zero["confidence_interval"]["lower"] == pytest.approx(0)
    assert zero["confidence_interval"]["upper"] == pytest.approx(.7934507)
    half = proportion(5, 10)
    assert half["confidence_interval"]["lower"] == pytest.approx(.2365931)
    assert half["confidence_interval"]["upper"] == pytest.approx(.7634069)
    assert proportion(0, 100)["confidence_interval"]["upper"] < zero["confidence_interval"]["upper"]
    assert proportion(0, 0)["confidence_interval"] is None
    assert proportion(5, 10, .99)["confidence_interval"]["lower"] < half["confidence_interval"]["lower"]


@pytest.mark.parametrize("confidence", [0, 1, -1, float("nan"), float("inf")])
def test_invalid_confidence_is_rejected(confidence):
    with pytest.raises(ValueError, match="confidence"):
        proportion(0, 0, confidence)


def test_unknown_root_does_not_establish_absence_of_other_risks(tmp_path):
    gold = [record("case", [assessment("known", [root()]), assessment("unknown", [root("INSUFFICIENT_EVIDENCE", None)])], True)]
    predictions = [record("case", [assessment("known", [root("INSUFFICIENT_EVIDENCE", None)]), assessment("unknown")])]
    c = compare(tmp_path, gold, predictions)["quality"]["per_risk"][RISK]
    assert c["support"]["reference_unknown"] == 1
    assert c["support"]["prediction_unknown_positive"] == 1
    assert c["confusion"] == dict(tp=0, fp=0, fn=0, tn=0)
    assert c["end_to_end_miss_rate"]["value"] == 1
    predictions[0]["run_assessments"][0]["root_causes"].append(root())
    c = compare(tmp_path, gold, predictions)["quality"]["per_risk"][RISK]
    assert c["confusion"]["tp"] == 1


def test_same_label_independent_roots_are_counted_beyond_run_presence(tmp_path):
    gold = [record("case", [assessment("run", [root(first="one"), root(first="two")])], True)]
    predictions = [record("case", [assessment("run", [root(first="two")])])]
    result = compare(tmp_path, gold, predictions)
    c = result["quality"]["per_label"][RISK]
    assert c["confusion"]["tp"] == 1
    assert c["root_instances"] == dict(reference=2, predicted=1, matched=1, localized=1, missing=1, extra=0)
    assert result["root_recall"]["value"] == .5


def test_label_match_and_localization_error_are_separate(tmp_path):
    gold = [record("case", [assessment("run", [root(first="one")])], True)]
    predictions = [record("case", [assessment("run", [root(first="two")])])]
    result = compare(tmp_path, gold, predictions)
    c = result["quality"]["per_label"][RISK]
    assert c["miss_rate"]["value"] == 0
    assert c["root_instances"]["localized"] == 0
    assert result["disagreements"][0]["matched_prediction"]["first_fault_event_id"] == "two"


def test_two_null_first_faults_do_not_prove_successful_localization(tmp_path):
    roots = [root("INSUFFICIENT_EVIDENCE", None)]
    result = compare(tmp_path, [record("c", [assessment("r", roots)], True)], [record("c", [assessment("r", roots)])])
    assert result["root_recall"]["value"] == 1
    assert result["matched_localization_accuracy"]["numerator"] == 0
    assert result["quality"]["per_label"]["INSUFFICIENT_EVIDENCE"]["root_instances"]["localized"] == 0


def test_risk_without_stop_context_is_unknown_not_a_generic_intent_risk(tmp_path):
    gold = [record("case", [assessment("run", [root("INTENT_CLASSIFICATION")])], True)]
    predictions = [record("case", [assessment("run")])]
    result = compare(tmp_path, gold, predictions)
    assert result["quality"]["per_risk"]["STOP_REQUEST_ERROR"]["support"]["reference_unknown"] == 1
    gold[0]["run_assessments"][0]["reference_snapshot_state"] = "CANCELED"
    result = compare(tmp_path, gold, predictions)
    assert result["quality"]["per_risk"]["STOP_REQUEST_ERROR"]["confusion"]["fn"] == 1
    assert result["high_risk_missed"] == 1


def test_hold_context_and_roles_follow_source_case_not_label_order(tmp_path):
    raw = fictional_case()
    raw["user_request"] = "暂缓开具发票"
    case = Case.model_validate(raw)
    a, b = case.runs
    gold = [record(case.case_id, [assessment(b.run_id), assessment(a.run_id, [root("CONTEXT_UPDATE", a.events[0].event_id)])], True)]
    predictions = [record(case.case_id, [assessment(a.run_id), assessment(b.run_id)])]
    result = compare(tmp_path, gold, predictions, cases=[case])
    assert result["quality"]["by_role"]["baseline"]["per_risk"]["STOP_REQUEST_ERROR"]["confusion"]["fn"] == 1
    assert result["quality"]["by_role"]["candidate"]["per_risk"]["STOP_REQUEST_ERROR"]["confusion"]["tn"] == 1


def test_extra_unlabeled_predictions_are_visible_but_not_false_positives(tmp_path):
    gold = [record("case", [assessment("run")], True)]
    predictions = [record("case", [assessment("run")]), record("extra", [assessment("extra-run", [root()])])]
    result = compare(tmp_path, gold, predictions)
    assert result["input_integrity"]["unlabeled_prediction_cases"] == ["extra"]
    assert result["input_integrity"]["unlabeled_prediction_runs"] == ["extra-run"]
    assert result["quality"]["per_risk"][RISK]["confusion"]["fp"] == 0


@pytest.mark.parametrize("side", ["reference", "prediction"])
@pytest.mark.parametrize("problem", ["duplicate_case", "duplicate_run", "invalid_label", "invalid_boolean"])
def test_bad_identity_or_judgment_cannot_silently_change_denominators(tmp_path, side, problem):
    gold = [record("case", [assessment("run")], True)]
    predictions = [record("case", [assessment("run")])]
    rows = gold if side == "reference" else predictions
    if problem == "duplicate_case":
        rows.append(copy.deepcopy(rows[0]))
    elif problem == "duplicate_run":
        rows[0]["run_assessments"].append(copy.deepcopy(rows[0]["run_assessments"][0]))
    elif problem == "invalid_label":
        rows[0]["run_assessments"][0]["root_causes"] = [root("TYPO")]
    else:
        rows[0]["run_assessments"][0]["agent_task_success"] = "false"
    with pytest.raises(ValueError):
        compare(tmp_path, gold, predictions)


def test_cross_case_predictions_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="different case"):
        compare(tmp_path, [record("case", [assessment("run")], True)], [record("wrong", [assessment("run")])])


@pytest.mark.parametrize("side", ["reference", "prediction"])
def test_first_fault_must_belong_to_source_run(tmp_path, side):
    case = Case.model_validate(fictional_case())
    gold = [record(case.case_id, [assessment(case.runs[0].run_id)], True)]
    predictions = [record(case.case_id, [assessment(case.runs[0].run_id)])]
    rows = gold if side == "reference" else predictions
    rows[0]["run_assessments"][0]["root_causes"] = [root(first=case.runs[1].events[0].event_id)]
    with pytest.raises(ValueError, match="does not belong"):
        compare(tmp_path, gold, predictions, cases=[case])


def test_partial_run_cli_includes_valid_unpaired_run_and_writes_readable_report(tmp_path):
    raw = fictional_case()
    raw["runs"][0].pop("invoice_created")
    source = tmp_path / "cases.jsonl"
    write_jsonl(source, [raw])
    run = tmp_path / "evaluation"
    assert evaluate(source, DEFAULT_CONFIG, run)["status"] == "needs_review"
    assert not (run / "submission.jsonl").exists()
    labels = tmp_path / "labels.jsonl"
    write_jsonl(labels, [record(raw["case_id"], [assessment("DEMO-A"), assessment("DEMO-B", [root(first="DEMO-B-5")])], True)])
    output = tmp_path / "reports/quality.json"
    assert main(["compare-labels", "--run", str(run), "--labels", str(labels), "--output", str(output), "--confidence", "0.9"]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["coverage"]["numerator"] == 1 and result["coverage"]["denominator"] == 2
    assert result["quality"]["by_role"]["candidate"]["per_risk"][RISK]["confusion"]["tp"] == 1
    assert result["quality"]["by_role"]["baseline"]["per_risk"][RISK]["support"]["prediction_unknown_negative"] == 1
    assert result["quality"]["uncertainty"]["confidence_level"] == .9
    assert result["provenance"]["cases_content_hash"]
    assert set(result["quality"]["per_label"]) == set(LABELS)
    text = output.with_suffix(".md").read_text(encoding="utf-8")
    assert "端到端漏检率" in text and "不可估计" in text and "DEMO-A" in text


def test_paired_and_individual_mismatch_is_rejected(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    write_jsonl(run / "run_assessments.jsonl", [{"case_id": "c", "assessment": assessment("r")}])
    write_jsonl(run / "assessments.jsonl", [record("c", [assessment("r", [root()])])])
    labels = tmp_path / "labels.jsonl"
    write_jsonl(labels, [record("c", [assessment("r")], True)])
    with pytest.raises(ValueError, match="disagree"):
        compare_labels(run, labels, prediction_format="run")


def test_report_escapes_trace_identifiers_and_preserves_provenance(tmp_path):
    gold = [record("<script>|case", [assessment("r", [root()])], True)]
    result = compare(tmp_path, gold, [])
    output = tmp_path / "quality.json"
    markdown = write_quality_report(result, output)
    text = markdown.read_text(encoding="utf-8")
    assert "<script>" not in text and "&lt;script&gt;&#124;case" in text
    assert result["provenance"]["labels_hash"] and result["provenance"]["prediction_hashes"]
    with pytest.raises(ValueError, match=".md"):
        write_quality_report(result, tmp_path / "bad.md")
