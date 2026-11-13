from pathlib import Path
from .pipeline import evaluate
from .loader import read_jsonl, write_json, source_hash

def stability(cases, config, output, repeats=3, fresh=False):
    if repeats < 2:
        raise ValueError("stability requires at least two runs")
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    signatures=[]
    manifests=[]
    judge_runs=[]
    for i in range(repeats):
        directory=output/f"repeat-{i+1}"
        manifest=evaluate(cases,config,directory,cache_policy="bypass" if fresh else "use")
        manifests.append({"evaluation_id":manifest["evaluation_id"],"status":manifest["status"]})
        judge_runs.append(read_jsonl(directory/"judge_results.jsonl") if (directory/"judge_results.jsonl").stat().st_size else [])
        records=read_jsonl(directory/"assessments.jsonl") if (directory/"assessments.jsonl").stat().st_size else []
        signatures.append({a["run_id"]:{"success":a["agent_task_success"],"created":a["business_invoice_created"],"score":a["quality_score"],"causes":[(c["label"],c["first_fault_event_id"]) for c in a["root_causes"]]} for r in records for a in r["run_assessments"]})
    ids=set().union(*(set(s) for s in signatures))
    comparisons=(repeats-1)*len(ids)
    changes=sum(s.get(r)!=signatures[0].get(r) for s in signatures[1:] for r in ids)
    report={"source_hash":source_hash(),"repeats":repeats,"runs":manifests,"comparisons":comparisons,"changed_conclusions":changes,"change_rate":changes/comparisons if comparisons else None,"all_evaluations_valid":all(m["status"]=="success" for m in manifests),"note":"同配置可能命中有效live缓存；缓存回放稳定性不等于独立联网模型确定性。"}
    def judged(rows):
        return {(r["run_id"],r["question_id"]):r.get("mapped_result") if r["valid_live"] else {"invalid":r["status"]} for r in rows}
    judgments=[judged(rows) for rows in judge_runs]
    keys=set().union(*(set(j) for j in judgments))
    changed=sum(j.get(k)!=judgments[0].get(k) for j in judgments[1:] for k in keys)
    calls=sum(len(r["attempts"]) for rows in judge_runs for r in rows)
    valid=sum(r["valid_live"] for rows in judge_runs for r in rows)
    total=sum(len(rows) for rows in judge_runs)
    report.update(cache_policy="bypass" if fresh else "use",network_attempts=calls,cache_hits=sum(r["cache_hit"] for rows in judge_runs for r in rows),valid_judgments=valid,total_judgments=total,valid_judgment_rate=valid/total if total else None,judge_response_comparisons=len(keys)*(repeats-1),changed_judge_responses=changed,independent_live_measurement=fresh and calls>0 and total>0)
    if fresh:report["note"]="禁用缓存读写；分别统计完整模型响应变化和业务结论变化。失败也保留在判断有效率分母。"
    write_json(output/"stability.json",report)
    return report
