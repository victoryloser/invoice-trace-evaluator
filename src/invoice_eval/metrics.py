from collections import defaultdict
from .loader import read_jsonl
from .matching import match_causes

def ratio(n, d):
    return {"numerator": n, "denominator": d, "value": n/d if d else None}

def compare_labels(prediction_path, labels_path, cases=None):
    predictions = {r["case_id"]: r for r in read_jsonl(prediction_path)}
    labels = read_jsonl(labels_path)
    correct = created = direction = total = matched = joint = gold_count = pred_count = covered = 0
    errors = []
    high_risk_missed = 0
    risk = {"DUPLICATE_CONTROL", "CONFIRMATION_BYPASS", "TOOL_ERROR_HANDLING"}
    run_context={r.run_id:(c,{e.event_id:e.seq for e in r.events}) for c in cases or [] for r in c.runs}
    for gold in labels:
        prediction = predictions.get(gold["case_id"])
        if prediction and prediction["version_comparison"]["direction"] == gold["version_direction_on_this_case"]:
            direction += 1
        by_run = {x["run_id"]: x for x in prediction["run_assessments"]} if prediction else {}
        for expected in gold["run_assessments"]:
            total += 1
            observed = by_run.get(expected["run_id"])
            covered += observed is not None
            correct += bool(observed and observed["agent_task_success"] == expected["agent_task_success"])
            created += bool(observed and observed["business_invoice_created"] == expected["business_invoice_created"])
            gs = list(expected["root_causes"])
            ps = list(observed["root_causes"]) if observed else []
            gold_count += len(gs)
            pred_count += len(ps)
            ctx=run_context.get(expected["run_id"])
            pairs,missing,extra=match_causes(gs,ps,ctx[1] if ctx else None)
            matched+=len(pairs);joint+=sum(exact for _,_,exact in pairs)
            for gi,pi,exact in pairs:
                if not exact:errors.append({"run_id":expected["run_id"],"expected":gs[gi],"matched_prediction":ps[pi]})
            cancel_goal=expected.get("reference_snapshot_state")=="CANCELED" or bool(ctx and ctx[0].reference_result.get("required_action")=="CANCEL")
            for gi in missing:
                g=gs[gi]
                if g["label"] in risk or (cancel_goal and g["label"] in {"INTENT_CLASSIFICATION","CONTEXT_UPDATE"}):high_risk_missed+=1
                errors.append({"run_id":expected["run_id"],"expected":g,"matched_prediction":None})
            if extra:errors.append({"run_id":expected["run_id"],"extra_predictions":[ps[i] for i in extra]})
    precision = matched/pred_count if pred_count else 0
    recall = matched/gold_count if gold_count else 0
    return {"data_role": "development; not untouched holdout", "agent_task_success_accuracy": ratio(correct, total), "business_invoice_created_accuracy": ratio(created, total), "coverage": ratio(covered, total), "root_precision": ratio(matched, pred_count), "root_recall": ratio(matched, gold_count), "root_f1": 2*precision*recall/(precision+recall) if precision+recall else 0, "first_fault_joint_precision": ratio(joint, pred_count), "first_fault_joint_recall": ratio(joint, gold_count), "matched_localization_accuracy": ratio(joint, matched), "direction_agreement": ratio(direction, len(labels)), "high_risk_missed": high_risk_missed, "disagreements": errors}
