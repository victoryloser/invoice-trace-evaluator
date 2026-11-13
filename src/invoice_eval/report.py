from collections import Counter
from pathlib import Path
from .loader import read_jsonl, write_json
from .scoring import release_gate, risks

def metrics(cases, records, reviews, judge_records, gate, completed=None):
    assessments = [x["assessment"] for x in completed] if completed is not None else [a for r in records for a in r["run_assessments"]]
    total = sum(len(c.runs) for c in cases)
    result = {"input_cases": len(cases), "input_runs": total, "valid_cases": len(records), "valid_runs": len(assessments), "failed_or_review_runs": total-len(assessments), "coverage": len(assessments)/total if total else 0, "agent_correct": {"numerator": sum(a["agent_task_success"] for a in assessments), "denominator": len(assessments), "input_denominator": total}, "service_created": {"numerator": sum(a["business_invoice_created"] for a in assessments), "denominator": len(assessments)}, "high_risk_runs": sum(bool(risks(a)) for a in assessments), "mean_quality_score": sum(a["quality_score"] for a in assessments)/len(assessments) if assessments else None, "directions": dict(Counter(r["version_comparison"]["direction"] for r in records)), "scenarios": dict(Counter(c.scenario for c in cases)), "release_recommendation": release_gate(records, len(cases), len(reviews), gate), "judge_calls": len(judge_records), "valid_live_judgments": sum(r["valid_live"] for r in judge_records), "live_cache_hits": sum(r["cache_hit"] for r in judge_records), "judge_usage": {}, "simulated_agent_usage": [], "expert_accuracy": None, "evidence_support_manual_audit": "未进行独立人工抽查"}
    for record in judge_records:
        if not record["cache_hit"]:
            for attempt in record["attempts"]:
                for k, v in attempt.get("usage", {}).items():
                    if isinstance(v, (int, float)):
                        result["judge_usage"][k] = result["judge_usage"].get(k, 0) + v
    for c in cases:
        for r in c.runs:
            result["simulated_agent_usage"].append({"run_id": r.run_id, **r.resource_usage})
    result["paired_runs"]=len(records)*2
    result["service_created"].update(evidence_scope="offline_trace", external_verification="not_performed")
    result["unpaired_valid_runs"]=len(assessments)-len(records)*2
    evidence=gate.get("verified_evidence",{})
    result["release_evidence"]={"verified":evidence.get("verified",False),"errors":evidence.get("errors",[]),"evidence_ids":[x["evidence_id"] for x in evidence.get("evidence",[])]}
    if completed is not None and any(not x["assessment"]["agent_task_success"] or risks(x["assessment"]) for x in completed if any(c.case_id==x["case_id"] and c.runs[1].run_id==x["assessment"]["run_id"] for c in cases)):
        result["release_recommendation"]="defer"
    return result

def render_report(directory):
    import json
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    records = read_jsonl(directory / "assessments.jsonl") if (directory / "assessments.jsonl").stat().st_size else []
    lines = ["# 开票 Agent 离线版本评测报告", "", f"运行：`{manifest['evaluation_id']}`；源码：`{manifest['source_hash']}`。", "", f"输入 {summary['input_cases']} Case / {summary['input_runs']} run；完整有效 {summary['valid_cases']} Case / {summary['valid_runs']} run；覆盖 {summary['coverage']:.1%}。", f"待复核或失败 run：{summary['failed_or_review_runs']}。批量建议：**{summary['release_recommendation']}**。", "", f"处理正确 {summary['agent_correct']['numerator']}/{summary['agent_correct']['denominator']}（总输入分母 {summary['input_runs']}）；轨迹记录出票 {summary['service_created']['numerator']}/{summary['service_created']['denominator']}。", f"严重风险 run：{summary['high_risk_runs']}；平均质量分：{summary['mean_quality_score']}。", "", "以上比例描述离线轨迹支持的业务处理与出票判断；未独立核验开票服务或账本。质量分按配置中的规则和扣分权重计算。", "", f"Judge 请求 {summary['judge_calls']}；有效 live 判断 {summary['valid_live_judgments']}；缓存 {summary['live_cache_hits']}。", f"评测器本次模型用量：{summary['judge_usage']}。Agent 资源统计来自输入轨迹，见 metrics.json；评测器不核实这些计量值。", "", "| Case | A 分数/处理/记录出票 | B 分数/处理/记录出票 | 方向 | 建议 |", "|---|---|---|---|---|"]
    for r in records:
        a, b = r["run_assessments"]
        fmt = lambda x: f"{x['quality_score']} / {x['agent_task_success']} / {x['business_invoice_created']}"
        lines.append(f"| {r['case_id']} | {fmt(a)} | {fmt(b)} | {r['version_comparison']['direction']} | {r['version_comparison']['release_recommendation']} |")
    lines.extend(["", "## 逐 Case 证据链", ""])
    lines.extend([f"未成对但有效的运行：{summary.get('unpaired_valid_runs',0)}；它们计入有效运行分母，整批提交仍要求 Case 两版完整。",f"独立验收证据：{summary.get('release_evidence',{})}",""])
    for r in records:
        lines.extend([f"### {r['case_id']}", "", r["version_comparison"]["reason"], ""])
        for a in r["run_assessments"]:
            lines.append(f"- **{a['run_id']}**：{a['business_result_assessment']} 轨迹记录出票：{a['business_invoice_created']}。")
            for root in a["root_causes"]:
                lines.append(f"  - {root['label']}，首错 `{root['first_fault_event_id']}`；{root['explanation']} 证据：{', '.join(root['evidence_event_ids'])}。")
            lines.append(f"  - 传播：{a['downstream_impact']}")
    paired={a["run_id"] for r in records for a in r["run_assessments"]}
    all_runs=read_jsonl(directory/"run_assessments.jsonl") if (directory/"run_assessments.jsonl").exists() and (directory/"run_assessments.jsonl").stat().st_size else []
    for row in all_runs:
        a=row["assessment"]
        if a["run_id"] not in paired:
            lines.extend(["",f"### 未成对有效运行 {a['run_id']}","",f"{a['business_result_assessment']}；处理正确 {a['agent_task_success']}，轨迹记录出票 {a['business_invoice_created']}，质量分 {a['quality_score']}。",f"证据：{', '.join(a['evidence_event_ids'])}"])
    lines.extend(["", "## 边界", "", "原始轨迹未改写。评测阶段状态与被评 Agent 业务成败分别保存。存在关键 unknown 或必需 Judge 失败时不发布完整 submission.jsonl。", "标签一致性与运行质量分别统计。数据划分见 configs/split.json；输入轨迹与评测器自身 trace 分开保存。", ""])
    coverage_path = directory / "behavior_coverage.json"
    business_path = directory / "business_evidence.json"
    if business_path.exists():
        from .business_evidence import render_business_evidence
        business = json.loads(business_path.read_text(encoding="utf-8"))
        (directory / "business_evidence.md").write_text(render_business_evidence(business), encoding="utf-8")
        evidence_summary = business["summary"]
        lines.extend(["", "## 出票证据与外部核验", "", business["boundary"],
                      f"全部输入：记录出票 {evidence_summary['trace_created']}、记录未出票 {evidence_summary['trace_not_created']}、缺失或冲突 {evidence_summary['trace_unknown']}；独立外部核验 0/{evidence_summary['input_runs']}。外部真实出票数未知。",
                      "逐条来源、字段路径和事件编号见 [出票证据报告](business_evidence.md)。", ""])
    else:
        lines.extend(["", "## 出票证据与外部核验", "", "此历史产物未保存逐条出票来源说明。出票字段来自离线记录，不应解释为已独立核验外部业务状态。", ""])
    if coverage_path.exists():
        from .behavior import render_behavior_report
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        behavior_lines = render_behavior_report(coverage)
        (directory / "behavior_coverage.md").write_text("\n".join(behavior_lines), encoding="utf-8")
        lines.extend(["", "## 业务行为覆盖", "", *behavior_lines[2:behavior_lines.index("## 用例与事件映射")],
                      "完整用例、触发条件及事件证据见 [业务行为覆盖报告](behavior_coverage.md)。", ""])
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return directory / "report.md"
