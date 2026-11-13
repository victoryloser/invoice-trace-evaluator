"""Fixed-corpus evaluator regression tests use fictional data only."""
import copy
import json
import shutil

import pytest

from invoice_eval.cli import main
from invoice_eval.loader import file_hash, read_jsonl, write_json, write_jsonl
from invoice_eval.regression import compare_evaluators, write_comparison
from test_onboarding import evaluate_fictional


def seal(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"] = {p.name: file_hash(p) for p in directory.iterdir() if p.is_file() and p.name != "manifest.json"}
    write_json(directory / "manifest.json", manifest)


def change(directory, edit):
    rows = read_jsonl(directory / "run_assessments.jsonl")
    edit(rows)
    write_jsonl(directory / "run_assessments.jsonl", rows)
    # Tests edit individual results; no stale pair conclusions should be read.
    write_jsonl(directory / "assessments.jsonl", [])
    seal(directory)


@pytest.fixture
def runs(tmp_path):
    _, old = evaluate_fictional(tmp_path)
    new = tmp_path / "new"
    shutil.copytree(old, new)
    labels = tmp_path / "labels.jsonl"
    write_jsonl(labels, [{"case_id": "DEMO-001", "run_assessments": [
        {"run_id": "DEMO-A", "agent_task_success": True, "business_invoice_created": False, "root_causes": []},
        {"run_id": "DEMO-B", "agent_task_success": False, "business_invoice_created": True,
         "root_causes": [{"label": "CONFIRMATION_BYPASS", "first_fault_event_id": "DEMO-B-5"}]}]}])
    return old, new, labels


def test_identical_outputs_are_not_invented_improvement(runs, tmp_path):
    old, new, labels = runs
    result = compare_evaluators(old, new, labels)
    assert result["summary"]["changed_runs"] == 0
    assert result["summary"]["total_runs"] == 2
    assert result["reference_comparison"]["regressed_run_ids"] == []
    assert result["reference_comparison"]["improved_run_ids"] == []
    assert result["identity_changes"] == {}
    path = write_comparison(result, tmp_path / "reports" / "comparison.json")
    assert "同身份回放" in path.read_text(encoding="utf-8")
    assert json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["corpus"]["runs"] == 2


def test_fixed_input_rejects_changed_evidence_even_with_same_ids(runs):
    old, new, _ = runs
    rows = read_jsonl(new / "cases.snapshot.jsonl")
    rows[0]["context"]["user_confirmation_recorded"] = True
    write_jsonl(new / "cases.snapshot.jsonl", rows)
    seal(new)
    with pytest.raises(ValueError, match="fixed corpus mismatch"):
        compare_evaluators(old, new)


def test_fixed_input_ignores_json_formatting(runs):
    old, new, _ = runs
    path = new / "cases.snapshot.jsonl"
    path.write_text(json.dumps(read_jsonl(path)[0], ensure_ascii=True) + "\n", encoding="utf-8")
    seal(new)
    assert compare_evaluators(old, new)["summary"]["changed_runs"] == 0


@pytest.mark.parametrize("name", ["run_assessments.jsonl", "cases.snapshot.jsonl", "config.snapshot.json", "assessments.jsonl"])
def test_modified_artifact_is_rejected(runs, name):
    old, new, _ = runs
    with (new / name).open("a", encoding="utf-8") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="artifact hash"):
        compare_evaluators(old, new)


def test_missing_paired_artifact_cannot_hide_stale_results(runs):
    old, new, _ = runs
    (new / "assessments.jsonl").unlink()
    with pytest.raises(ValueError, match="artifact hash"):
        compare_evaluators(old, new)


def test_loss_of_judgment_keeps_denominator_and_correctness_loss(runs):
    old, new, labels = runs
    change(new, lambda rows: rows.pop())
    result = compare_evaluators(old, new, labels)
    assert result["summary"]["availability"]["newly_missing"] == 1
    assert result["summary"]["changed_run_rate"] == .5
    reference = result["reference_comparison"]
    assert reference["transitions"]["agent_task_success"]["lost_correct"] == 1
    assert reference["risk_deltas"]["CONFIRMATION_BYPASS"]["end_to_end_miss_rate"]["new"]["value"] == 1
    assert reference["regressed_run_ids"] == ["DEMO-B"]


def test_both_missing_is_unknown_and_not_a_correct_negative(runs):
    old, new, labels = runs
    for directory in (old, new):
        change(directory, lambda rows: rows.clear())
    result = compare_evaluators(old, new, labels)
    assert result["summary"]["availability"]["both_missing"] == 2
    assert result["summary"]["total_runs"] == 2
    assert result["reference_comparison"]["transitions"]["agent_task_success"] == {"both_unknown": 2}


def test_restored_predictions_are_separate_from_incorrect_to_correct(runs):
    old, new, labels = runs
    change(old, lambda rows: rows.pop())
    result = compare_evaluators(old, new, labels)
    assert result["summary"]["availability"]["newly_assessed"] == 1
    assert result["reference_comparison"]["transitions"]["agent_task_success"]["resolved_correct"] == 1


def test_a_run_can_improve_one_dimension_and_regress_another(runs):
    old, new, labels = runs
    change(old, lambda rows: rows[1]["assessment"].update(agent_task_success=True))
    change(new, lambda rows: rows[1]["assessment"].update(business_invoice_created=False, quality_score=99))
    result = compare_evaluators(old, new, labels)
    reference = result["reference_comparison"]
    assert reference["improved_run_ids"] == reference["regressed_run_ids"] == ["DEMO-B"]
    assert reference["transitions"]["agent_task_success"]["improvement"] == 1
    assert reference["transitions"]["business_invoice_created"]["regression"] == 1
    assert result["run_diffs"][1]["field_changes"]["quality_score"]["new"] == 99


def test_root_matching_reserves_exact_instances_before_localization(runs):
    old, new, _ = runs
    def two_roots(rows):
        causes = rows[1]["assessment"]["root_causes"]
        second = copy.deepcopy(causes[0])
        second["first_fault_event_id"] = "DEMO-B-6"
        causes.append(second)
    change(old, two_roots)
    change(new, two_roots)
    def move(rows):
        causes = rows[1]["assessment"]["root_causes"]
        causes[0]["first_fault_event_id"] = "DEMO-B-7"
        causes.reverse()
    change(new, move)
    roots = compare_evaluators(old, new)["run_diffs"][1]["root_changes"]
    assert roots["removed"] == roots["added"] == []
    assert len(roots["relocated"]) == 1
    assert roots["relocated"][0]["old"]["first_fault_event_id"] == "DEMO-B-5"


def test_evidence_order_and_prose_do_not_count_as_business_changes(runs):
    old, new, _ = runs
    def prose(rows):
        cause = rows[1]["assessment"]["root_causes"][0]
        cause["evidence_event_ids"].reverse()
        cause["explanation"] = "Different wording only"
    change(new, prose)
    result = compare_evaluators(old, new)
    assert result["summary"]["changed_runs"] == 0
    # Pair artifacts were removed, so their availability change is visible separately.
    assert result["summary"]["changed_agent_pair_decisions"] == 1


def test_changed_evidence_is_reported_without_quality_verdict(runs):
    old, new, _ = runs
    def evidence(rows):
        rows[1]["assessment"]["root_causes"][0]["evidence_event_ids"] = ["DEMO-B-1"]
    change(new, evidence)
    result = compare_evaluators(old, new)
    assert result["run_diffs"][1]["root_changes"]["evidence_changed"]
    assert result["reference_comparison"] is None


def test_unknown_roots_and_null_localization_stay_unknown(runs):
    old, new, labels = runs
    def unknown(rows):
        rows[1]["assessment"]["root_causes"] = [{"label": "INSUFFICIENT_EVIDENCE", "first_fault_event_id": None}]
    change(new, unknown)
    reference = compare_evaluators(old, new, labels)["reference_comparison"]
    assert reference["transitions"]["root_labels"]["lost_correct"] == 1
    assert reference["transitions"]["risk:CONFIRMATION_BYPASS"]["lost_correct"] == 1
    def unlocated(rows):
        rows[1]["assessment"]["root_causes"] = [{"label": "CONFIRMATION_BYPASS", "first_fault_event_id": None}]
    change(new, unlocated)
    reference = compare_evaluators(old, new, labels)["reference_comparison"]
    assert reference["transitions"]["root_labels"] == {"unchanged_correct": 2}
    assert reference["transitions"]["root_localization"]["lost_correct"] == 1


@pytest.mark.parametrize("kind", ["case", "run", "event", "evidence", "duplicate", "score", "stale_pairs"])
def test_bad_predictions_cannot_enter_comparison(runs, kind):
    old, new, _ = runs
    def corrupt(rows):
        if kind == "case": rows[0]["case_id"] = "other-case"
        elif kind == "run": rows[0]["assessment"]["run_id"] = "other-run"
        elif kind == "event": rows[1]["assessment"]["root_causes"][0]["first_fault_event_id"] = "DEMO-A-1"
        elif kind == "evidence": rows[1]["assessment"]["root_causes"][0]["evidence_event_ids"] = ["DEMO-A-1"]
        elif kind == "duplicate": rows.append(copy.deepcopy(rows[0]))
        elif kind == "score": rows[0]["assessment"]["quality_score"] = True
        else: rows[0]["assessment"]["agent_task_success"] = False
    if kind == "stale_pairs":
        rows = read_jsonl(new / "run_assessments.jsonl")
        corrupt(rows)
        write_jsonl(new / "run_assessments.jsonl", rows)
        seal(new)
    else:
        change(new, corrupt)
    with pytest.raises(ValueError):
        compare_evaluators(old, new)


def test_partial_reference_is_explicit_not_a_false_positive(runs):
    old, new, labels = runs
    rows = read_jsonl(labels)
    rows[0]["run_assessments"].pop()
    write_jsonl(labels, rows)
    reference = compare_evaluators(old, new, labels)["reference_comparison"]
    assert reference["reference_runs"] == 1
    assert reference["unlabeled_runs"] == ["DEMO-B"]


def test_removed_root_is_a_missed_risk_and_reverse_is_a_fix(runs):
    old, new, labels = runs
    change(new, lambda rows: rows[1]["assessment"].update(root_causes=[]))
    result = compare_evaluators(old, new, labels)
    assert len(result["run_diffs"][1]["root_changes"]["removed"]) == 1
    reference = result["reference_comparison"]
    assert reference["risk_deltas"]["CONFIRMATION_BYPASS"]["miss_rate"]["delta"] == 1
    assert reference["transitions"]["risk:CONFIRMATION_BYPASS"]["regression"] == 1
    reverse = compare_evaluators(new, old, labels)
    assert len(reverse["run_diffs"][1]["root_changes"]["added"]) == 1
    assert reverse["reference_comparison"]["transitions"]["risk:CONFIRMATION_BYPASS"]["improvement"] == 1


def test_case_line_order_does_not_change_corpus_but_agent_role_order_does(runs):
    old, new, _ = runs
    for directory in (old, new):
        rows = read_jsonl(directory / "cases.snapshot.jsonl")
        second = json.loads(json.dumps(rows[0]).replace("DEMO", "SECOND"))
        rows.append(second)
        write_jsonl(directory / "cases.snapshot.jsonl", rows if directory == old else list(reversed(rows)))
        seal(directory)
    result = compare_evaluators(old, new)
    assert result["corpus"]["runs"] == 4
    assert result["summary"]["availability"]["both_missing"] == 2
    rows = read_jsonl(new / "cases.snapshot.jsonl")
    rows[0]["runs"].reverse()
    write_jsonl(new / "cases.snapshot.jsonl", rows)
    seal(new)
    with pytest.raises(ValueError, match="fixed corpus mismatch"):
        compare_evaluators(old, new)


def test_in_progress_evaluation_is_rejected(runs):
    old, new, _ = runs
    path = new / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["status"] = "running"
    write_json(path, manifest)
    with pytest.raises(ValueError, match="terminal status"):
        compare_evaluators(old, new)


def test_invalid_confidence_without_labels_is_rejected(runs):
    old, new, _ = runs
    with pytest.raises(ValueError, match="confidence"):
        compare_evaluators(old, new, confidence=1)


def test_different_evaluator_identity_is_preserved(runs):
    old, new, _ = runs
    path = new / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["source_hash"] = "another-implementation"
    manifest["status"] = "needs_review"
    write_json(path, manifest)
    result = compare_evaluators(old, new)
    assert "source_hash" in result["identity_changes"]
    assert result["provenance"]["new"]["status"] == "needs_review"


def test_cli_writes_reports_and_protects_original_artifacts(runs, tmp_path):
    old, new, labels = runs
    output = tmp_path / "diff.json"
    args = ["compare-evaluators", "--old-run", str(old), "--new-run", str(new), "--labels", str(labels), "--output"]
    assert main([*args, str(output)]) == 0
    assert output.is_file() and output.with_suffix(".md").is_file()
    before = file_hash(new / "manifest.json")
    assert main([*args, str(new / "manifest.json")]) == 2
    assert file_hash(new / "manifest.json") == before
    assert main([*args, str(tmp_path / "bad.md")]) == 2
    before = file_hash(labels)
    assert main([*args, str(labels)]) == 2
    assert file_hash(labels) == before
