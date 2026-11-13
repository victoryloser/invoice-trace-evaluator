import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from . import __version__
from .export import publish
from .judge import Judge
from .loader import digest, file_hash, load_cases, source_hash, write_json, write_jsonl
from .models import Finding
from .report import metrics, render_report
from .rules import RULE_LABELS, inspect_run
from .scoring import assessment, compare
from .decisions import reconcile, synchronize_checks
from .acceptance import load_gate_evidence
from .resources import OUTPUT_SCHEMA, WORKFLOW, RULES, SPLIT

ROOT = Path(__file__).resolve().parents[2]

def evaluate(cases_path, config_path, output, material=None, cache_policy="use"):
    started = datetime.now(timezone.utc).isoformat()
    if not Path(cases_path).is_file():
        raise FileNotFoundError(f"Trace file not found: {cases_path}. Supply your own JSONL; see docs/data-format.md.")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    material = Path(material) if material else None
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    schema_path = next(material.glob("06*.json")) if material else OUTPUT_SCHEMA
    rule_path = next(material.glob("07*.md")) if material else RULES
    workflow_path = next(material.glob("01*.json")) if material else WORKFLOW
    evaluation_id = uuid.uuid4().hex
    manifest = {"evaluation_id": evaluation_id, "started_at": started, "command": sys.argv, "version": __version__, "source_hash": source_hash(), "input_hash": file_hash(cases_path), "input_name": Path(cases_path).name, "config_hash": file_hash(config_path), "rule_hash": file_hash(rule_path), "workflow_hash": file_hash(workflow_path), "schema_hash": file_hash(schema_path), "lock_hash": file_hash(ROOT / "requirements.lock.txt") if (ROOT / "requirements.lock.txt").exists() else None, "split": json.loads(SPLIT.read_text(encoding="utf-8")), "scoring_version": config["scoring_version"], "status": "running", "artifacts": {}}
    write_json(output / "manifest.json", manifest)
    write_json(output / "config.snapshot.json", config)
    records, findings, states, reviews, traces, checks, completed, conflicts = [], [], [], [], [], [], [], []
    judge = Judge(config["judge"], output.parent / "judge-cache", digest(config), cache_policy=cache_policy)
    manifest["cache_policy"] = cache_policy
    manifest["judge"] = judge.public_config()
    manifest["decision_config_hash"]=digest({"config":{k:v for k,v in config.items() if k!="release_gate"},"judge":manifest["judge"]})
    gate=dict(config["release_gate"])
    gate["evidence_files"]=[str((Path(config_path).parent/p).resolve()) if not Path(p).is_absolute() else p for p in gate.get("evidence_files",[])]
    gate["verified_evidence"]=load_gate_evidence(gate,manifest)
    write_json(output/"release_evidence.json",gate["verified_evidence"])
    last_stage_time = time.perf_counter()

    def trace(stage, status, reason, case_id=None, run_id=None, required=True, refs=(), duration=None):
        nonlocal last_stage_time
        now = time.perf_counter()
        if duration is None:
            duration = round((now-last_stage_time)*1000, 3)
        last_stage_time = now
        traces.append({"evaluation_id": evaluation_id, "eval_event_seq": len(traces)+1, "stage": stage, "status": status, "payload_valid": status == "success", "required_for_decision": required, "reason": reason, "duration_ms": duration, "source_case_id": case_id, "source_run_id": run_id, "source_event_ids": list(refs)})
        write_jsonl(output / "evaluator_trace.jsonl", traces)

    try:
        cases = load_cases(cases_path)
        manifest.update(expected_cases=len(cases), expected_runs=sum(len(c.runs) for c in cases))
        write_jsonl(output / "cases.snapshot.jsonl", [c.model_dump() for c in cases])
        trace("load", "success", "strict JSONL and identity validation")
        for case in cases:
            pair = []
            for run in case.runs:
                begin = time.perf_counter()
                analysis = inspect_run(case, run)
                synchronize_checks(analysis)
                trace("state_rules", "success", "business checks recorded separately", case.case_id, run.run_id, duration=round((time.perf_counter()-begin)*1000, 3))
                for q in analysis["questions"]:
                    result = judge.ask(case, run, q)
                    decision = reconcile(result,q,analysis,run)
                    judge.records[-1]["decision"] = decision
                    if result is None:
                        status=judge.records[-1]["status"]
                        trace("judge", "warning" if status=="success" else status, judge.records[-1].get("reason", "no valid semantic verdict"), case.case_id, run.run_id)
                    else:
                        trace("judge", "success" if decision["accepted"] else "warning", decision.get("reason",result.explanation), case.case_id, run.run_id, refs=result.evidence_event_ids)
                synchronize_checks(analysis)
                conflicts.extend({"case_id":case.case_id,"run_id":run.run_id,**c} for c in analysis.get("conflicts",[]))
                if not analysis["questions"]:
                    trace("judge", "skipped", "structured facts sufficient", case.case_id, run.run_id, required=False)
                findings.extend({"case_id": case.case_id, "run_id": run.run_id, **f.model_dump()} for f in analysis["findings"])
                states.append({"case_id": case.case_id, "run_id": run.run_id, "snapshots": analysis["states"]})
                checks.append({"case_id": case.case_id, "run_id": run.run_id, "checks": analysis["checks"]})
                if analysis["review"]:
                    reviews.append({"case_id": case.case_id, "run_id": run.run_id, "reasons": analysis["review"]})
                    trace("decision", "warning", "; ".join(analysis["review"]), case.case_id, run.run_id)
                else:
                    pair.append(assessment(run, analysis, config))
                    completed.append({"case_id":case.case_id,"assessment":pair[-1]})
                    trace("attribution_scoring", "success", "independent causal groups scored", case.case_id, run.run_id)
            if len(pair) == 2:
                records.append({"case_id": case.case_id, "run_assessments": pair, "version_comparison": compare(*pair)})
        write_jsonl(output / "assessments.jsonl", records)
        write_jsonl(output / "run_assessments.jsonl", completed)
        write_json(output / "judge_conflicts.json", conflicts)
        write_jsonl(output / "findings.jsonl", findings)
        write_jsonl(output / "states.jsonl", states)
        write_jsonl(output / "checks.jsonl", checks)
        write_jsonl(output / "judge_results.jsonl", judge.records)
        write_json(output / "review_queue.json", reviews)
        summary = metrics(cases, records, reviews, judge.records, gate, completed=completed)
        write_json(output / "metrics.json", summary)
        if reviews:
            manifest.update(status="needs_review", validation={"valid": False, "reason": "unresolved decisions"})
            trace("export", "failed", "critical unknown; complete submission withheld")
        else:
            manifest.update(status="success", validation=publish(cases, records, schema_path, output))
            trace("export", "success", "full coverage, schema, references and score validated")
        manifest.update(successful_cases=len(records), successful_runs=len(completed), published_pair_runs=len(records)*2, failed_cases=len(cases)-len(records), failed_runs=len(cases)*2-len(completed))
        write_json(output / "manifest.json", manifest)
        render_report(output)
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        trace("evaluation", "failed", manifest["error"])
        write_json(output / "diagnostic.json", {"error": manifest["error"]})
        raise
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["artifacts"] = {p.name: file_hash(p) for p in sorted(output.iterdir()) if p.is_file() and p.name != "manifest.json"}
        write_json(output / "manifest.json", manifest)
    return manifest
