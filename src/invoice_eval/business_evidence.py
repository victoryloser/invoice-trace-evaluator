"""Recorded invoice outcomes are not independently verified service state."""
from collections import Counter


EVIDENCE_VERSION = "1.0"
BOUNDARY = "出票结论来自离线输入与回调记录；本评测器未查询开票服务或账本，未独立核验外部业务状态。"
SOURCE_LABELS = {
    "run_summary": "输入运行摘要",
    "callback_output": "轨迹回调字段",
    "callback_tool_result": "轨迹回调工具结果",
}
STATUS_LABELS = {"consistent": "记录一致", "missing": "记录缺失", "conflicting": "记录冲突"}


def invoice_evidence(run):
    """Preserve the existing outcome rule and expose every source it uses.

    Paths are JSON Pointers relative to the input run, including original array
    indices. Event IDs remain the navigation anchors, even if events are unsorted.
    No extra input field can attest external verification on our behalf.
    """
    sources, claims = [], []

    def source(kind, path, raw, value, event=None):
        sources.append({"kind": kind, "source_path": path, "event_id": event.event_id if event else None,
                        "recorded_at": event.finished_at if event else None,
                        "recorded_value": raw, "invoice_created": value})

    for index, event in sorted(enumerate(run.events), key=lambda pair: pair[1].seq):
        path = f"/events/{index}"
        status = event.observed_output.get("callback_status")
        if status in ("success", "failed"):
            source("callback_output", path + "/observed_output/callback_status", status, status == "success", event)
        if "invoice_created" in event.observed_output:
            claims.append({"event_id": event.event_id, "source_path": path + "/observed_output/invoice_created",
                           "recorded_value": event.observed_output["invoice_created"], "used_for_conclusion": False,
                           "reason": "事件输出中的出票自述不单独证明服务出票。"})
        for tool_index, tool in enumerate(event.tool_calls):
            tool_path = f"{path}/tool_calls/{tool_index}/result"
            status = tool.result.get("status")
            if tool.name == "invoice.callback" and status in ("success", "failed"):
                source("callback_tool_result", tool_path + "/status", status, status == "success", event)
            if tool.name == "invoice.submit" and "submit_status" in tool.result:
                claims.append({"event_id": event.event_id, "source_path": tool_path + "/submit_status",
                               "recorded_value": tool.result["submit_status"], "used_for_conclusion": False,
                               "reason": "提交受理状态不等于发票生成状态。"})
    if run.invoice_created is not None:
        source("run_summary", "/invoice_created", run.invoice_created, run.invoice_created)
    values = {s["invoice_created"] for s in sources}
    status = "missing" if not values else "consistent" if len(values) == 1 else "conflicting"
    created = sources[0]["invoice_created"] if status == "consistent" else None
    return {"version": EVIDENCE_VERSION, "scope": "offline_trace", "status": status,
            "invoice_created": created, "sources": sources, "non_decisive_signals": claims,
            "external_verification": {"status": "not_performed", "invoice_created": None},
            "boundary": BOUNDARY}


def business_evidence_report(cases):
    rows = [{"case_id": case.case_id, "run_id": run.run_id, "role": "baseline" if i == 0 else "candidate",
             **invoice_evidence(run)} for case in cases for i, run in enumerate(case.runs)]

    def summary(selected):
        states = Counter(r["status"] for r in selected)
        total = len(selected)
        return {"input_runs": total, "trace_created": sum(r["invoice_created"] is True for r in selected),
                "trace_not_created": sum(r["invoice_created"] is False for r in selected),
                "trace_unknown": sum(r["invoice_created"] is None for r in selected),
                "status_counts": {k: states[k] for k in STATUS_LABELS},
                "runs_by_source": {kind: sum(any(s["kind"] == kind for s in r["sources"]) for r in selected) for kind in SOURCE_LABELS},
                "external_verified_runs": 0, "external_unverified_runs": total,
                "externally_verified_invoice_created": None}

    return {"version": EVIDENCE_VERSION, "scope": "offline_trace", "boundary": BOUNDARY,
            "summary": summary(rows), "by_role": {role: summary([r for r in rows if r["role"] == role])
                                                    for role in ("baseline", "candidate")}, "runs": rows}


def render_business_evidence(result):
    def cell(value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\n", " ").replace("\r", " ")
    def outcome(value):
        return "未知" if value is None else "是" if value else "否"
    s = result["summary"]
    lines = ["# 出票结论的证据来源与核验边界", "", result["boundary"], "",
             f"全部输入 {s['input_runs']} 条运行：记录显示出票 {s['trace_created']} 条、未出票 {s['trace_not_created']} 条、未知 {s['trace_unknown']} 条。",
             f"独立外部核验 {s['external_verified_runs']}/{s['input_runs']}；外部真实出票数：未知（不是 0）。", "",
             "记录一致仅表示已提供字段没有冲突，不证明记录真实、完整或来自相互独立的来源。摘要和回调可能来自同一链路，不做多数表决或置信度加分。",
             "本表覆盖所有输入，包括未产出有效判定的运行；与主报告中按有效判定统计的出票分母不同。", "",
             "| Case / run | Agent 角色 | 轨迹记录出票 | 记录状态 | 使用来源 | 外部核验 |", "|---|---|---|---|---|---|"]
    for row in result["runs"]:
        kinds = list(dict.fromkeys(SOURCE_LABELS[s["kind"]] for s in row["sources"]))
        lines.append(f"| {cell(row['case_id'])} / {cell(row['run_id'])} | {row['role']} | {outcome(row['invoice_created'])} | {STATUS_LABELS[row['status']]} | {'、'.join(kinds) or '无'} | 未执行 |")
    lines.extend(["", "## 逐条来源", "", "路径为相对 run 对象的 JSON Pointer；事件编号可在 HTML 时间线定位。", ""])
    for row in result["runs"]:
        lines.extend([f"### {cell(row['case_id'])} / {cell(row['run_id'])}", ""])
        for source in row["sources"]:
            lines.append(f"- {SOURCE_LABELS[source['kind']]}：{cell(source['source_path'])}；事件 {cell(source['event_id'] or '无（运行摘要）')}；记录值 {cell(source['recorded_value'])} → 出票 {outcome(source['invoice_created'])}。")
        if not row["sources"]:
            lines.append("- 没有可用于出票判定的摘要或明确回调记录。")
        for signal in row["non_decisive_signals"]:
            lines.append(f"- 未用于出票判定：{cell(signal['source_path'])} = {cell(signal['recorded_value'])}；{signal['reason']}")
        lines.append("")
    lines.extend(["## 解释边界", "", "- 缺失或冲突保留未知，不用 Agent 自述、成功步骤、受理成功、参考期望或 Judge 输出补全。",
                  "- 已记录回调仅是输入证据；本程序没有重新执行工具，也没有验证回调签名或服务数据。",
                  "- 独立人工验收检验裁判结论，双 Judge 复核检验判定一致性；两者均不等于查询外部业务状态。",
                  "- 外部核验需另行关联业务任务/发票标识、查询时间、权威服务响应及可追溯来源；当前程序未实现该连接。",
                  "- business_invoice_created 为兼容字段名，含义是轨迹记录支持的出票判断，不代表当前外部账本状态。", ""])
    return "\n".join(lines)
