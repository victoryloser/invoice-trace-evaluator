from .attribution import group_findings
from .rules import RULE_LABELS

def assessment(run, analysis, config):
    groups = group_findings(analysis["findings"], run)
    penalties, causes = [], []
    for group in groups:
        root = group["root"]
        points, dimension = max((config["penalties"][f.rule_id] for f in group["members"]), key=lambda x: x[0])
        penalties.append({"dimension": dimension, "points_deducted": points, "evidence_event_ids": group["evidence_event_ids"], "explanation": root.explanation, "causal_group_id": group["group_id"]})
        causes.append({"label": root.label, "first_fault_event_id": root.first_fault_event_id, "evidence_event_ids": group["evidence_event_ids"], "explanation": root.explanation, "rule_id": root.rule_id, "causal_group_id": group["group_id"]})
    evidence = list(dict.fromkeys(x for g in groups for x in g["evidence_event_ids"])) or [e.event_id for e in sorted(run.events, key=lambda e: e.seq)[-2:]]
    return {"run_id": run.run_id, "agent_task_success": not groups, "business_invoice_created": analysis["created"], "quality_score": max(0, 100 - sum(p["points_deducted"] for p in penalties)), "business_result_assessment": "处理符合当前任务约束。" if not groups else f"发现 {len(groups)} 个独立业务或风险问题。", "process_assessment": "检查当前目标、逐步状态、确认、风险、工具及回调；工具保护性拒绝不单独算错。", "penalty_items": penalties, "root_causes": causes, "downstream_impact": "；".join(f"{g['root'].first_fault_event_id} → {', '.join(g['evidence_event_ids'][1:]) or '无可观察下游传播'}" for g in groups) or "无异常传播。", "improvement_suggestions": [f"修复 {g['root'].rule_id}：{g['root'].explanation}" for g in groups], "evidence_event_ids": evidence, "explanation": "处理正确性与服务出票事实分别判定；质量分按配置中的规则扣分。"}

def risks(result):
    return {RULE_LABELS[c["rule_id"]] if c["label"]=="INSUFFICIENT_EVIDENCE" else c["label"] for c in result["root_causes"] if any(p["dimension"] == "RISK_CONTROL" and p.get("causal_group_id") == c.get("causal_group_id") for p in result["penalty_items"])}

def compare(a, b):
    ar, br = risks(a), risks(b)
    if ar < br:
        direction = "baseline_better"
    elif br < ar:
        direction = "candidate_better"
    elif ar != br:
        direction = "insufficient_evidence"
    else:
        av, bv = (a["agent_task_success"], a["quality_score"]), (b["agent_task_success"], b["quality_score"])
        direction = "candidate_better" if bv > av else "baseline_better" if av > bv else "equal"
    release = "defer" if br or not b["agent_task_success"] or direction == "baseline_better" else "investigate" if direction == "insufficient_evidence" else "gray"
    return {"direction": direction, "release_recommendation": release, "reason": f"严重风险类型：基线 {sorted(ar)}，候选 {sorted(br)}；质量分 {a['quality_score']} → {b['quality_score']}。单 Case 不构成生产发布证明。"}

def release_gate(records, expected_cases, review_count, gate):
    if any(r["version_comparison"]["release_recommendation"] == "defer" for r in records):
        return "defer"
    if review_count or len(records) != expected_cases or any(r["version_comparison"]["direction"] == "insufficient_evidence" for r in records):
        return "investigate"
    required = set(gate["required_cases"])
    if required and required <= {r["case_id"] for r in records} and gate.get("verified_evidence",{}).get("verified"):
        return "release"
    return "gray"
