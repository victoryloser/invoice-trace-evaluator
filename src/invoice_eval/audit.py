import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from .judge import Judge
from .loader import digest, load_cases, read_jsonl, write_json, write_jsonl, file_hash, source_hash
from .matching import match_causes
from .models import AuditResult

def align(left,right,order):
    pairs,missing,extra=match_causes(left,right,order)
    return {"matched":[{"left":left[i],"right":right[j],"same_first_fault":exact,"evidence_only_left":sorted(set(left[i]["evidence_event_ids"])-set(right[j]["evidence_event_ids"])),"evidence_only_right":sorted(set(right[j]["evidence_event_ids"])-set(left[i]["evidence_event_ids"]))} for i,j,exact in pairs],"only_left":[left[i] for i in missing],"only_right":[right[j] for j in extra],"same_causes":not missing and not extra and all(exact for _,_,exact in pairs)}

def audit(run_directory, config_path, output, case_ids=None, fresh=False, workers=1):
    run_directory,output=Path(run_directory),Path(output)
    output.mkdir(parents=True,exist_ok=False)
    config=json.loads(Path(config_path).read_text(encoding="utf-8"))
    if not config.get("secondary_judge"):raise ValueError("secondary_judge configuration required")
    if not 1<=workers<=8:raise ValueError("audit workers must be between 1 and 8")
    cache=output.parent/"audit-cache"
    judges=[Judge(config[key],cache,digest(config),cache_policy="bypass" if fresh else "use") for key in ("judge","secondary_judge")]
    if judges[0].public_config()==judges[1].public_config():raise ValueError("dual Judge requires distinct provider/model configurations")
    cases=load_cases(run_directory/"cases.snapshot.jsonl")
    if case_ids and not set(case_ids)<={c.case_id for c in cases}:raise ValueError("unknown audit case_id")
    path=run_directory/"run_assessments.jsonl"
    assessments={x["assessment"]["run_id"]:x["assessment"] for x in read_jsonl(path)} if path.exists() and path.stat().st_size else {a["run_id"]:a for r in read_jsonl(run_directory/"assessments.jsonl") for a in r["run_assessments"]}
    comparisons=[];records=[]
    work=[(case,run) for case in cases if not case_ids or case.case_id in case_ids for run in case.runs]
    def inspect_pair(pair):
            case,run=pair
            pair_judges=[Judge(config[key],cache,digest(config),cache_policy="bypass" if fresh else "use") for key in ("judge","secondary_judge")]
            q={"question_id":"all-independent-faults","event_id":max(run.events,key=lambda e:e.seq).event_id,"kind":"audit_all","prompt":"独立检查全部业务和风险约束，列出所有独立根因及各自首错。必须同时检查金额/字段、批量明细、确认、重复、取消、未知受理重试、回调。不要只列最早错误；同源传播合并；相同标签的不同独立错误保留。"}
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda j:j.ask(case,run,q),pair_judges))
            valid=all(r is not None for r in results)
            order={e.event_id:e.seq for e in run.events}
            cause_lists=[[c.model_dump() for c in r.causes] if r else [] for r in results]
            alignment=align(*cause_lists,order) if valid else None
            a=assessments.get(run.run_id)
            facts=a["root_causes"] if a else []
            fact_comparisons=[align(facts,causes,order) if r and a else None for r,causes in zip(results,cause_lists)]
            same=valid and results[0].verdict==results[1].verdict and alignment["same_causes"]
            comparison={"case_id":case.case_id,"run_id":run.run_id,"status":"agreement" if same else "disagreement" if valid else "invalid_judge","cause_alignment":alignment,"deterministic_causes":facts,"judge_results":[r.model_dump() if r else None for r in results],"deterministic_alignment":fact_comparisons,"conflicts_with_deterministic":[bool(r and a and ((r.verdict=="pass")!=a["agent_task_success"] or not comparison["same_causes"])) for r,comparison in zip(results,fact_comparisons)]}
            return comparison,[r for j in pair_judges for r in j.records]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for comparison,pair_records in pool.map(inspect_pair,work):
            comparisons.append(comparison);records.extend(pair_records)
            write_jsonl(output/"judge_results.jsonl",records)
            write_json(output/"comparisons.json",comparisons)
    report={"source_hash":source_hash(),"assessment_manifest_hash":file_hash(run_directory/"manifest.json"),"scope":"selected" if case_ids else "full","cause_scope":"all_independent_causes","cache_policy":"bypass" if fresh else "use","models":[j.public_config(AuditResult) for j in judges],"comparisons":comparisons,"valid_pairs":sum(c["status"]!="invalid_judge" for c in comparisons),"agreement_pairs":sum(c["status"]=="agreement" for c in comparisons),"disagreement_pairs":sum(c["status"]=="disagreement" for c in comparisons),"note":"逐项对齐全部独立根因，保留漏项、额外根因、定位及证据差异；模型意见不改写确定性结论。"}
    write_json(output/"dual-judge.json",report)
    return report
