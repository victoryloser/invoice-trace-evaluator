"""Evidence-based behavior coverage, independent of task scores and release gates.

Declarations never trigger coverage. A condition needs an observed response;
unanswered conditions remain visible, and failures still count as exercised.
"""
from collections import Counter
from decimal import InvalidOperation

from .loader import canonical
from .state import CONTENT_FIELDS, State, money, same_value


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def outcome(statuses):
    if "fail" in statuses:
        return "fail"
    if not statuses or any(s != "pass" for s in statuses):
        return "unknown"
    return "pass"


def collect_behaviors(case, run, analysis, catalog, role):
    """Replay recorded input only; attach final rule/Judge evidence afterwards."""
    episodes = {b["id"]: [] for b in catalog["behaviors"]}
    active = {}
    state = State(case)
    events = sorted(run.events, key=lambda e: e.seq)
    created = analysis["created"]

    def start(key, event_ids=(), paths=(), **details):
        item = {"trigger_event_ids": list(event_ids), "trigger_field_paths": list(paths),
                "evidence_event_ids": list(event_ids), "covered": False,
                "status": "unknown", "reason": "已观察到条件，尚无对应处理证据。", **details}
        episodes[key].append(item)
        active[key] = item
        return item

    def observe(item, event, status, reason):
        item["covered"] = True
        if event.event_id not in item["evidence_event_ids"]:
            item["evidence_event_ids"].append(event.event_id)
        if item["status"] != "fail" and (status != "unknown" or item["status"] != "pass"):
            item.update(status=status, reason=reason)

    def target_origin(field):
        value = state.target.get(field)
        return ([value.source_event_id] if value and value.source_event_id else [],
                [value.source_path] if value else ["context"])

    def confirmation(event, submitting):
        if submitting:
            return ("pass", "提交时具有当前内容的有效确认。") if state.confirmed else ("fail", "未取得当前内容的有效确认即提交。")
        if state.confirmed:
            return "pass", "已记录当前内容的用户确认。"
        if event.step_status == "WAITING_USER" and created is False:
            return "pass", "明确等待用户确认，服务事实为未出票。"
        return "unknown", "出现确认步骤，但缺少有效确认或明确等待证据。"

    for event in events:
        previous = state.values()
        was_confirmed = state.confirmed
        state.before(event)
        target = state.values()
        out = event.observed_output
        content = out.get("preview", out)
        submitting = event.step_id == "SUBMIT_INVOICE" or any(t.name == "invoice.submit" for t in event.tool_calls)
        boundary = event.step_id in {"COLLECT_INVOICE_DATA", "CONFIRM_INVOICE", "WAIT_USER_SUPPLEMENT"} or submitting
        changed = [k for k in CONTENT_FIELDS if not same_value(previous.get(k), target.get(k))]
        if changed and (was_confirmed or "reconfirm_after_update" in active):
            start("reconfirm_after_update", [event.event_id],
                  [state.target[k].source_path for k in changed], changed_fields=changed)

        # These conditions have distinct lifetimes; a later user resumption is
        # not retroactively treated as submitting during cancellation/hold.
        for intent, key in (("CANCEL", "cancel_stop"), ("HOLD", "hold_stop")):
            if target.get("intent") != intent:
                active.pop(key, None)
                continue
            if key not in active:
                refs, paths = target_origin("intent")
                start(key, refs, paths)
            item = active[key]
            if submitting:
                observe(item, event, "fail", "取消或暂缓仍有效时发生提交。")
            elif event.step_id in {"CANCEL_TASK", "TRANSFER_TO_HUMAN", "TIMEOUT_TASK", "FAIL_TASK"} or event.step_status == "WAITING_USER":
                stopped = (event.step_id == "CANCEL_TASK" and (event.step_status == "CANCELED" or event.step_status == "SUCCEEDED" and event.result_code == "E800")) if intent == "CANCEL" else event.step_status in {"WAITING_USER", "CANCELED", "FAILED", "TIMED_OUT"} or event.step_id == "TRANSFER_TO_HUMAN"
                observe(item, event, "pass" if stopped and created is False else "unknown",
                        "记录了停止/等待且服务未出票。" if stopped and created is False else "出现停止处理，但终止或未出票证据不足。")

        if event.step_id == "CONFIRM_INVOICE" or submitting:
            # State.after applies preview changes and explicit confirmation on
            # that preview. Use it below for preview-only decisions.
            if submitting:
                item = start("confirmation_gate", [event.event_id], [f"events.{event.event_id}.observed_input"])
                observe(item, event, *confirmation(event, True))

        if "reconfirm_after_update" in active and boundary:
            item = active["reconfirm_after_update"]
            if submitting:
                observe(item, event, *confirmation(event, True))
            else:
                observe(item, event, "unknown", "修改已到达处理步骤，等待重新确认的证据。")

        risks = set(out.get("risk_flags", []))
        for tool in event.tool_calls:
            if tool.name == "rules.validate":
                risks.update(tool.result.get("risk_flags", []))
        if "DUPLICATE_INVOICE" in state.risks | risks:
            if "duplicate_block" not in active:
                start("duplicate_block", [event.event_id] if "DUPLICATE_INVOICE" in risks else [],
                      [f"events.{event.event_id}.observed_output", f"events.{event.event_id}.tool_calls"] if "DUPLICATE_INVOICE" in risks else ["context.risk_flags"])
            item = active["duplicate_block"]
            rejected = out.get("validation_passed") is False or out.get("create_allowed") is False
            allowed = out.get("validation_passed") is True or out.get("create_allowed") is True
            if submitting or allowed:
                observe(item, event, "fail", "已知重复风险后放行或提交。")
            elif rejected or event.step_id == "TRANSFER_TO_HUMAN":
                observe(item, event, "pass" if created is False else "unknown", "明确阻断/转人工；通过还要求未出票事实。")
            elif event.step_id == "VALIDATE_DATA":
                observe(item, event, "unknown", "已执行校验，但未记录阻断结果。")
        state.risks.update(risks)

        # Missing-field conditions are recomputed from the current user target,
        # never from Agent-invented values. Resolved fields end the condition.
        missing = {f for f in case.context.get("required_missing_fields", []) if target.get(f) is None}
        needs = {
            "buyer_clarification": {"buyer_id"} if target.get("buyer_id") is None and (len(case.context.get("buyer_candidates", [])) > 1 or case.context.get("buyer_disambiguation_required")) else set(),
            "missing_field_clarification": missing - {"amount_basis"},
            "amount_basis_clarification": {"amount_basis"} if target.get("amount_basis") is None and (case.context.get("amount_basis_unspecified") or "amount_basis" in missing) else set(),
            "red_reference_required": {"original_invoice_id"} if target.get("invoice_task_type") == "RED" and target.get("original_invoice_id") is None else set(),
        }
        if needs["buyer_clarification"]:
            needs["missing_field_clarification"] -= {"buyer_id", "buyer_disambiguation"}
        if needs["red_reference_required"]:
            needs["missing_field_clarification"].discard("original_invoice_id")
        for key, fields in needs.items():
            if not fields:
                active.pop(key, None)
                continue
            if key not in active or active[key]["required_fields"] != sorted(fields):
                refs = [state.target[f].source_event_id for f in fields if f in state.target and state.target[f].source_event_id]
                start(key, sorted(set(refs)), ["user_request", "context"], required_fields=sorted(fields))
            if boundary:
                item = active[key]
                requested = set(out.get("requested_fields", []))
                if "buyer_disambiguation" in requested:
                    requested.add("buyer_id")
                # Some logs call the ambiguity itself the requested field.
                if "buyer_disambiguation" in fields and "buyer_id" in requested:
                    requested.add("buyer_disambiguation")
                invented = any(content.get(f) is not None for f in fields)
                if submitting or invented:
                    observe(item, event, "fail", "字段仍缺失/有歧义时擅自填入或提交。")
                elif fields <= requested:
                    observe(item, event, "pass", "明确请求了本次所需的全部补充字段。")
                else:
                    observe(item, event, "unknown", "到达信息处理步骤，但未见完整补充请求。")

        if target.get("line_items") and boundary:
            refs, paths = target_origin("line_items")
            item = start("batch_integrity", refs, paths)
            payloads = [content] if not submitting else [event.observed_input] + [t.arguments for t in event.tool_calls if t.name == "invoice.submit"]
            def signature(lines):
                return Counter(canonical([line.get("item_id"), str(money(line["exclusive_amount"])) if line.get("exclusive_amount") is not None else None]) for line in lines)
            available = [p for p in payloads if "line_items" in p]
            status = "unknown"
            if available:
                try:
                    status = "pass" if all(signature(p["line_items"]) == signature(target["line_items"]) and p.get("line_count", len(p["line_items"])) == len(target["line_items"]) for p in available) else "fail"
                except (TypeError, ValueError, AttributeError, InvalidOperation):
                    status = "unknown"
            observe(item, event, status, {"pass":"已核对商品、金额和重复次数。", "fail":"批量明细内容或重复次数不一致。", "unknown":"缺少可比较的批量明细。"}[status])

        pending = active.get("unknown_submit_recovery")
        if pending and submitting and not any(t.name == "invoice.submit" for t in event.tool_calls):
            observe(pending, event, "unknown", "再次提交但缺少工具参数，无法核验幂等键及重试依据。")
        if pending and not submitting and (event.step_id in {"WAIT_INVOICE_CALLBACK", "TRANSFER_TO_HUMAN", "TIMEOUT_TASK", "FAIL_TASK"}):
            observe(pending, event, "pass", "受理未知后明确等待或受控退出。")
        for tool in event.tool_calls:
            if tool.name != "invoice.submit":
                continue
            pending = active.pop("unknown_submit_recovery", None)
            if pending:
                key = tool.arguments.get("idempotency_key")
                justified = event.observed_input.get("retry_authorized") is True or tool.arguments.get("retry_basis") == "idempotent_replay"
                good = bool(key and key == pending["idempotency_key"] and justified)
                observe(pending, event, "pass" if good else "fail", "重试必须同时具有相同幂等键和明确重试依据。")
            if tool.result.get("submit_status") in {"unknown", "timeout"}:
                start("unknown_submit_recovery", [event.event_id], [f"events.{event.event_id}.tool_calls"], idempotency_key=tool.arguments.get("idempotency_key"))

        failed = active.get("failed_callback_reporting")
        if failed and (out.get("user_message") or "invoice_created" in out or event.step_id in {"COMPLETE_TASK", "FAIL_TASK", "TRANSFER_TO_HUMAN"}):
            if event.step_id == "COMPLETE_TASK" or out.get("invoice_created") is True:
                observe(failed, event, "fail", "失败回调后宣称出票成功。")
            elif event.step_id in {"FAIL_TASK", "TRANSFER_TO_HUMAN"} or out.get("invoice_created") is False:
                observe(failed, event, "pass" if created is False else "unknown", "记录失败/转人工；通过还要求服务事实一致。")
            else:
                observe(failed, event, "unknown", "仅有自然语言反馈，需对应的语义结论。")
        callbacks = [out.get("callback_status")] + [t.result.get("status") for t in event.tool_calls if t.name == "invoice.callback"]
        if "failed" in callbacks:
            start("failed_callback_reporting", [event.event_id], [f"events.{event.event_id}.observed_output", f"events.{event.event_id}.tool_calls"])
        if "success" in callbacks:
            active.pop("unknown_submit_recovery", None)

        state.after(event)
        if event.step_id == "CONFIRM_INVOICE" and not submitting:
            item = start("confirmation_gate", [event.event_id], [f"events.{event.event_id}.observed_output"])
            observe(item, event, *confirmation(event, False))
            if "reconfirm_after_update" in active:
                observe(active["reconfirm_after_update"], event, *confirmation(event, False))

    definitions = {b["id"]: b for b in catalog["behaviors"]}
    rows = []
    for key, items in episodes.items():
        for item in items:
            refs = set(item["evidence_event_ids"])
            relevant = [f for f in analysis["findings"] if f.status == "fail" and f.rule_id in definitions[key]["rule_ids"] and f.first_fault_event_id in refs and item["covered"]]
            item["finding_ids"] = [f.finding_id for f in relevant]
            if relevant:
                item.update(status="fail", reason="对应处理存在已确认的规则/语义违规。")
            feedback = [q for q in analysis["questions"] if q["event_id"] in refs and q["kind"] == "feedback"] if key == "failed_callback_reporting" else []
            if feedback and item["status"] != "fail":
                verdict = outcome([analysis.get("question_states", {}).get(q["question_id"], "unknown") for q in feedback])
                item.update(status=verdict if verdict != "pass" or created is False else "unknown", reason="依据全部对应反馈的语义判断及服务事实。")
            # Never turn unresolved relevant semantics into a passing behavior.
            for question in analysis["questions"]:
                if question["event_id"] in refs and analysis.get("question_states", {}).get(question["question_id"], "unknown") == "unknown" and question["kind"] in {"terminal", "intent_protection"} and item["status"] == "pass":
                    item.update(status="unknown", reason="对应处理仍有未解决的语义判断。")
        covered = any(i["covered"] for i in items)
        rows.append({"behavior_id": key, "case_id": case.case_id, "run_id": run.run_id,
                     "role": role, "agent_version": run.agent_version,
                     "planned": key in case.expected_behaviors, "triggered": bool(items),
                     "covered": covered, "status": outcome([i["status"] for i in items]) if covered else "not_covered",
                     "occurrences": items})
    return rows


def summarize_behaviors(rows, catalog):
    """Fixed catalog denominator, including absent and planned-only behaviors."""
    def summarize(selected):
        behaviors = []
        for definition in catalog["behaviors"]:
            group = [r for r in selected if r["behavior_id"] == definition["id"]]
            triggered = [r for r in group if r["triggered"]]
            covered = [r for r in group if r["covered"]]
            behaviors.append({**definition, "covered": bool(covered),
                              "status": outcome([r["status"] for r in triggered]) if covered else "not_covered",
                              "planned_runs": sum(r["planned"] for r in group),
                              "triggered_runs": len(triggered), "covered_runs": len(covered),
                              "run_outcomes": {s: sum(r["status"] == s for r in covered) for s in ("pass", "fail", "unknown")},
                              "case_ids": sorted({r["case_id"] for r in covered}),
                              "run_ids": [r["run_id"] for r in covered]})
        covered_count = sum(b["covered"] for b in behaviors)
        passed_count = sum(b["status"] == "pass" for b in behaviors)
        observed = [r for r in selected if r["covered"]]
        return {"coverage": ratio(covered_count, len(behaviors)),
                "passed_behaviors": passed_count,
                "behavior_pass_rate": ratio(passed_count, covered_count),
                "run_behavior_pass_rate": ratio(sum(r["status"] == "pass" for r in observed), len(observed)),
                "uncovered_behavior_ids": [b["id"] for b in behaviors if not b["covered"]],
                "planned_but_uncovered": [{"case_id": r["case_id"], "run_id": r["run_id"], "behavior_id": r["behavior_id"], "triggered": r["triggered"]} for r in selected if r["planned"] and not r["covered"]],
                "behaviors": behaviors}
    return {"catalog_version": catalog["version"], "scope": catalog["scope"],
            "overall": summarize(rows),
            "by_role": {role: summarize([r for r in rows if r["role"] == role]) for role in ("baseline", "candidate")},
            "observations": rows}


def render_behavior_report(report):
    labels = {"pass": "通过", "fail": "失败", "unknown": "证据不足", "not_covered": "未覆盖"}
    def cell(value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\n", " ").replace("\r", " ")
    def fraction(value):
        return f"{value['numerator']}/{value['denominator']}" + (f"（{value['value']:.1%}）" if value["value"] is not None else "（无可评样本）")
    lines = ["# 业务行为覆盖报告", "", f"清单版本：{cell(report['catalog_version'])}。{cell(report['scope'])}", "",
             "覆盖要求真实条件及后续处理证据；失败也计入覆盖。仅有计划标签或触发条件不计覆盖。通过率保留证据不足项在分母中。", "",
             "| 范围 | 已覆盖行为 / 清单行为 | 已通过行为 / 已覆盖行为 | 通过的运行-行为 / 已覆盖运行-行为 |",
             "|---|---|---|---|"]
    for name, data in [("合并", report["overall"]), ("基线", report["by_role"]["baseline"]), ("候选", report["by_role"]["candidate"])]:
        lines.append(f"| {name} | {fraction(data['coverage'])} | {fraction(data['behavior_pass_rate'])} | {fraction(data['run_behavior_pass_rate'])} |")
    lines.extend(["", "同一行为只占一个覆盖分子；全部已触发实例均通过时，该行为才计为通过。两版本合并覆盖不能替代各版本覆盖。", "",
                  "| 行为 | 触发条件 | 预期行为 | 基线 | 候选 |", "|---|---|---|---|---|"])
    for index, b in enumerate(report["overall"]["behaviors"]):
        baseline = report["by_role"]["baseline"]["behaviors"][index]
        candidate = report["by_role"]["candidate"]["behaviors"][index]
        lines.append(f"| {cell(b['name'])} (`{b['id']}`) | {cell(b['trigger'])} | {cell(b['expected'])} | {labels[baseline['status']]} | {labels[candidate['status']]} |")
    lines.extend(["", "覆盖率仅用于用例诊断，本版不改变业务评分、submission 契约或发布门控。", "", "## 用例与事件映射", "",
                  "| Case / run | 版本角色 | 行为 | 计划 | 已触发 / 已覆盖 | 结果 | 触发来源 | 处理证据 / 说明 |", "|---|---|---|---|---|---|---|---|"])
    for row in report["observations"]:
        if not row["planned"] and not row["triggered"]:
            continue
        triggers = list(dict.fromkeys(p for item in row["occurrences"] for p in item["trigger_field_paths"]))
        evidence = [", ".join(item["evidence_event_ids"]) + ": " + item["reason"] for item in row["occurrences"]]
        lines.append("| " + " | ".join(cell(v) for v in (
            row["case_id"] + " / " + row["run_id"], row["role"], row["behavior_id"], "是" if row["planned"] else "否",
            f"{row['triggered']} / {row['covered']}", labels[row["status"]], "; ".join(triggers), "; ".join(evidence) or "计划行为未在轨迹中触发。")) + " |")
    lines.extend(["", "## 计划覆盖缺口", ""])
    for role, data in report["by_role"].items():
        gaps = data["planned_but_uncovered"]
        lines.append(f"- {role}：{len(gaps)} 项计划未实际覆盖。")
        for gap in gaps:
            lines.append(f"  - {cell(gap['case_id'])} / {cell(gap['run_id'])} / `{gap['behavior_id']}`：" + ("已有条件，缺少后续处理证据。" if gap["triggered"] else "未见真实触发条件。"))
    return lines
