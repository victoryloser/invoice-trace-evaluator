import json
from pathlib import Path
from jsonschema import Draft202012Validator
from .loader import read_jsonl, write_jsonl

def validate_records(cases, records, schema_path):
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    expected = {c.case_id: c for c in cases}
    if len(records) != len(expected) or {r["case_id"] for r in records} != set(expected):
        raise ValueError("case coverage/uniqueness mismatch")
    references = 0
    for record in records:
        validator.validate(record)
        case = expected[record["case_id"]]
        runs = {r.run_id: r for r in case.runs}
        if {a["run_id"] for a in record["run_assessments"]} != set(runs):
            raise ValueError("run coverage mismatch")
        for a in record["run_assessments"]:
            if "business_invoice_evidence" in a:
                from .business_evidence import invoice_evidence
                expected_evidence = invoice_evidence(runs[a["run_id"]])
                if a["business_invoice_evidence"] != expected_evidence or a["business_invoice_created"] != expected_evidence["invoice_created"]:
                    raise ValueError("business invoice evidence does not match recorded trace or verification boundary")
            events = {e.event_id for e in runs[a["run_id"]].events}
            for obj in [a, *a["root_causes"], *a["penalty_items"]]:
                refs = obj["evidence_event_ids"]
                if not refs or not set(refs) <= events:
                    raise ValueError("missing or cross-run evidence")
                references += len(refs)
            for cause in a["root_causes"]:
                first = cause["first_fault_event_id"]
                if first is not None and first not in cause["evidence_event_ids"]:
                    raise ValueError("first fault absent from evidence")
                if first is None and cause["label"] != "INSUFFICIENT_EVIDENCE":
                    raise ValueError("missing first fault")
            groups = [p.get("causal_group_id") for p in a["penalty_items"]]
            nonnull = [g for g in groups if g is not None]
            if len(nonnull) != len(set(nonnull)):
                raise ValueError("duplicate causal penalty")
            if a["quality_score"] != max(0, 100 - sum(p["points_deducted"] for p in a["penalty_items"])):
                raise ValueError("score arithmetic mismatch")
            if not a["agent_task_success"] and not a["improvement_suggestions"]:
                raise ValueError("failed run missing improvement suggestions")
    return {"valid": True, "case_count": len(records), "run_count": len(records) * 2, "valid_references": references, "total_references": references}

def publish(cases, records, schema_path, directory):
    directory = Path(directory)
    path = directory / "submission.pending.jsonl"
    write_jsonl(path, records)
    result = validate_records(cases, read_jsonl(path), schema_path)
    path.replace(directory / "submission.jsonl")
    return result
