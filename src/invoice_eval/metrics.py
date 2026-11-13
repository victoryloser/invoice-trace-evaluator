"""Reference-label comparison. Evaluation never imports reference labels."""
from pathlib import Path

from .loader import digest, file_hash, read_jsonl, source_hash, write_json
from .matching import match_causes
from .models import LABELS
from .quality import proportion, quality_breakdown, render_quality_report


def ratio(n, d):
    return {"numerator": n, "denominator": d, "value": n/d if d else None}


def _read_optional_rows(path):
    # Empty predictions are meaningful abstentions. References stay nonempty.
    path = Path(path)
    return read_jsonl(path) if path.stat().st_size else []


def _load_predictions(path, source_format):
    path = Path(path)
    if source_format == "submission":
        return _read_optional_rows(path), [path]
    if source_format != "run":
        raise ValueError("prediction format must be submission or run")
    individual_path = path / "run_assessments.jsonl"
    rows = _read_optional_rows(individual_path)
    grouped = {}
    for row in rows:
        grouped.setdefault(row["case_id"], {"case_id": row["case_id"], "run_assessments": []})["run_assessments"].append(row["assessment"])
    pair_path = path / "assessments.jsonl"
    files = [individual_path]
    if pair_path.exists():
        paired = _read_optional_rows(pair_path)
        _index_records(paired, "paired predictions")
        for row in paired:
            current = grouped.get(row["case_id"])
            by_run = {a["run_id"]: a for a in current["run_assessments"]} if current else {}
            if any(by_run.get(a["run_id"]) != a for a in row["run_assessments"]):
                raise ValueError("paired and individual assessments disagree")
            if current is None:
                raise ValueError("paired case missing individual assessments")
            current["version_comparison"] = row.get("version_comparison", {})
        files.append(pair_path)
    return list(grouped.values()), files


def _index_records(rows, name):
    records, owners = {}, {}
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str) or not row["case_id"]:
            raise ValueError(f"{name}:{index}: nonempty case_id required")
        case_id = row["case_id"]
        if case_id in records:
            raise ValueError(f"{name}:{index}: duplicate case_id {case_id}")
        assessments = row.get("run_assessments")
        if not isinstance(assessments, list):
            raise ValueError(f"{name}:{index}: run_assessments must be an array")
        for assessment in assessments:
            if not isinstance(assessment, dict) or not isinstance(assessment.get("run_id"), str) or not assessment["run_id"]:
                raise ValueError(f"{name}:{index}: nonempty run_id required")
            run_id = assessment["run_id"]
            if run_id in owners:
                raise ValueError(f"{name}:{index}: duplicate run_id {run_id}")
            owners[run_id] = case_id
            for key in ("agent_task_success", "business_invoice_created"):
                if type(assessment.get(key)) is not bool:
                    raise ValueError(f"{name}:{index}: {run_id}.{key} must be a boolean; omit unresolved predictions")
            roots = assessment.get("root_causes")
            if not isinstance(roots, list):
                raise ValueError(f"{name}:{index}: root_causes must be an array")
            for cause in roots:
                if not isinstance(cause, dict) or cause.get("label") not in LABELS:
                    raise ValueError(f"{name}:{index}: invalid root label")
                first = cause.get("first_fault_event_id")
                if first is not None and (not isinstance(first, str) or not first):
                    raise ValueError(f"{name}:{index}: invalid first_fault_event_id")
        records[case_id] = row
    return records, owners


def _stop_context(expected, ctx):
    if expected.get("reference_snapshot_state") == "CANCELED":
        return True
    if ctx is None:
        return None
    from .state import State
    case, run, _, _ = ctx
    if case.reference_result.get("required_action") == "CANCEL":
        return True
    state = State(case)
    intents = {state.values().get("intent")}
    for event in sorted(run.events, key=lambda e: e.seq):
        state.before(event)
        intents.add(state.values().get("intent"))
        state.after(event)
    return True if intents & {"CANCEL", "HOLD"} else None if "UNKNOWN" in intents else False


def compare_labels(prediction_path, labels_path, cases=None, *, confidence=0.95, prediction_format="submission"):
    proportion(0, 0, confidence)
    prediction_rows, prediction_files = _load_predictions(prediction_path, prediction_format)
    labels = read_jsonl(labels_path)
    predictions, predicted_owners = _index_records(prediction_rows, "predictions")
    references, reference_owners = _index_records(labels, "references")
    if not reference_owners:
        raise ValueError("references must contain at least one assessed run")
    for run_id in predicted_owners.keys() & reference_owners.keys():
        if predicted_owners[run_id] != reference_owners[run_id]:
            raise ValueError(f"prediction run {run_id} belongs to a different case")
    run_context = {r.run_id: (c, r, {e.event_id: e.seq for e in r.events}, "baseline" if i == 0 else "candidate")
                   for c in cases or [] for i, r in enumerate(c.runs)}
    if cases is not None:
        for run_id, case_id in reference_owners.items():
            if run_id not in run_context or run_context[run_id][0].case_id != case_id:
                raise ValueError(f"reference {case_id}/{run_id} not found in source cases")
    correct = created = direction = total = matched = joint = gold_count = pred_count = covered = 0
    errors, samples = [], []
    high_risk_missed = 0
    risk = {"DUPLICATE_CONTROL", "CONFIRMATION_BYPASS", "TOOL_ERROR_HANDLING"}
    root_counts = {label: dict(reference=0, predicted=0, matched=0, localized=0, missing=0, extra=0) for label in LABELS}
    for gold in labels:
        prediction = predictions.get(gold["case_id"])
        if prediction and prediction.get("version_comparison", {}).get("direction") == gold.get("version_direction_on_this_case") and gold.get("version_direction_on_this_case") is not None:
            direction += 1
        by_run = {x["run_id"]: x for x in prediction["run_assessments"]} if prediction else {}
        for index, expected in enumerate(gold["run_assessments"]):
            total += 1
            observed = by_run.get(expected["run_id"])
            covered += observed is not None
            correct += bool(observed and observed["agent_task_success"] == expected["agent_task_success"])
            created += bool(observed and observed["business_invoice_created"] == expected["business_invoice_created"])
            gs = list(expected["root_causes"])
            ps = list(observed["root_causes"]) if observed else []
            gold_count += len(gs)
            pred_count += len(ps)
            ctx = run_context.get(expected["run_id"])
            if ctx:
                for kind, causes in (("reference", gs), ("prediction", ps)):
                    for cause in causes:
                        first = cause.get("first_fault_event_id")
                        if first is not None and first not in ctx[2]:
                            raise ValueError(f"{kind} first fault {first} does not belong to run {expected['run_id']}")
            pairs, missing, extra = match_causes(gs, ps, ctx[2] if ctx else None)
            matched += len(pairs)
            joint += sum(exact and gs[gi].get("first_fault_event_id") is not None for gi, _, exact in pairs)
            for g in gs:
                root_counts[g["label"]]["reference"] += 1
            for p in ps:
                root_counts[p["label"]]["predicted"] += 1
            for gi, pi, exact in pairs:
                count = root_counts[gs[gi]["label"]]
                count["matched"] += 1
                count["localized"] += exact and gs[gi].get("first_fault_event_id") is not None
                if not exact:
                    errors.append({"run_id": expected["run_id"], "expected": gs[gi], "matched_prediction": ps[pi]})
            stop = _stop_context(expected, ctx)
            for gi in missing:
                g = gs[gi]
                root_counts[g["label"]]["missing"] += 1
                if g["label"] in risk or (stop and g["label"] in {"INTENT_CLASSIFICATION", "CONTEXT_UPDATE"}):
                    high_risk_missed += 1
                errors.append({"run_id": expected["run_id"], "expected": g, "matched_prediction": None})
            for pi in extra:
                root_counts[ps[pi]["label"]]["extra"] += 1
            if extra:
                errors.append({"run_id": expected["run_id"], "extra_predictions": [ps[i] for i in extra]})
            role = ctx[3] if ctx else ("baseline" if index == 0 else "candidate") if len(gold["run_assessments"]) == 2 else "unspecified"
            samples.append({"case_id": gold["case_id"], "run_id": expected["run_id"], "expected": expected,
                            "observed": observed, "role": role, "stop_context": stop})
    precision = matched/pred_count if pred_count else 0
    recall = matched/gold_count if gold_count else 0
    result = {"data_role": "development; not untouched holdout", "agent_task_success_accuracy": ratio(correct, total),
              "business_invoice_created_accuracy": ratio(created, total), "coverage": ratio(covered, total),
              "root_precision": ratio(matched, pred_count), "root_recall": ratio(matched, gold_count),
              "root_f1": 2*precision*recall/(precision+recall) if precision+recall else 0,
              "first_fault_joint_precision": ratio(joint, pred_count), "first_fault_joint_recall": ratio(joint, gold_count),
              "matched_localization_accuracy": ratio(joint, matched), "direction_agreement": ratio(direction, len(labels)),
              "high_risk_missed": high_risk_missed, "disagreements": errors}
    result["quality"] = quality_breakdown(samples, root_counts, confidence)
    result["input_integrity"] = {
        "reference_cases": len(references), "reference_runs": total,
        "prediction_cases": len(predictions), "prediction_runs": len(predicted_owners),
        "unlabeled_prediction_cases": sorted(predictions.keys() - references.keys()),
        "unlabeled_prediction_runs": sorted(predicted_owners.keys() - reference_owners.keys()),
        "missing_prediction_runs": sorted(reference_owners.keys() - predicted_owners.keys()),
        "role_source": "source case run order" if cases is not None else "reference order when exactly two runs; otherwise unspecified",
    }
    result["provenance"] = {"metrics_version": "2.0", "comparison_source_hash": source_hash(),
                            "prediction_format": prediction_format,
                            "prediction_hashes": {p.name: file_hash(p) for p in prediction_files},
                            "labels_hash": file_hash(labels_path),
                            "cases_content_hash": digest([c.model_dump() for c in cases]) if cases is not None else None}
    return result


def write_quality_report(result, output):
    output = Path(output)
    markdown = output.with_suffix(".md")
    if output == markdown:
        raise ValueError("JSON output must not use the .md extension")
    output.parent.mkdir(parents=True, exist_ok=True)
    text = render_quality_report(result)
    write_json(output, result)
    markdown.write_text(text, encoding="utf-8")
    return markdown
