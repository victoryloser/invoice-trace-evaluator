from collections import Counter
from .loader import canonical, digest
from .models import Finding
from .state import State, amounts, money, same_amounts, same_value

RULE_LABELS = dict(I01="INTENT_CLASSIFICATION", E01="ENTITY_RESOLUTION", F01="FIELD_FABRICATION", A01="AMOUNT_BASIS", A02="AMOUNT_CALCULATION", P01="PRODUCT_MATCH", B01="MULTI_ITEM_OMISSION", U01="CONTEXT_UPDATE", C01="CONFIRMATION_BYPASS", R01="DUPLICATE_CONTROL", R02="TOOL_ERROR_HANDLING", R03="INTENT_CLASSIFICATION", T01="TOOL_ARGUMENT_ERROR", O01="CALLBACK_MISINTERPRETATION")
RISK_RULES = {"C01", "R01", "R02", "R03"}

def inspect_run(case, run):
    state = State(case)
    findings, snapshots, questions, review = [], [], [], []
    sources = {}
    checked = set()
    events = sorted(run.events, key=lambda e: e.seq)
    rates = {x["item_id"]: x["configured_rate"] for x in case.context.get("item_candidates", [])}

    def add(rule, event, field, expected, observed, explanation, evidence=(), group=None):
        checked.add(rule)
        key = group or f"{rule}:{field}:{state.target.get(field).known_at_seq if field in state.target else 0}:{digest(observed) if rule not in RISK_RULES else ''}"
        old = sources.get(key)
        location = f"events.{event.event_id}.observed_output"
        if field == "tool_calls":
            location = f"events.{event.event_id}.tool_calls"
        elif event.step_id == "SUBMIT_INVOICE":
            location = f"events.{event.event_id}.observed_input"
        elif "preview" in event.observed_output:
            location += ".preview"
        f = Finding(finding_id=f"{run.run_id}:f{len(findings)+1}", rule_id=rule, label=RULE_LABELS[rule], severity="risk" if rule in RISK_RULES else "business", first_fault_event_id=event.event_id, evidence_event_ids=list(dict.fromkeys([*evidence, event.event_id])), field_paths=[location, "user_request", "context"], expected=expected, observed=observed, explanation=explanation, origin_finding_id=old.finding_id if old else None, causal_group_id=old.causal_group_id if old else f"{run.run_id}:g{len(sources)+1}")
        if old is None:
            sources[key] = f
        f.field_name = field
        findings.append(f)
        return f

    def propagate(event, payload):
        """Link only preserved bad values, not event adjacency or same label."""
        for origin in list(sources.values()):
            name = origin.field_name
            field = name if name in payload and same_value(payload[name], origin.observed) else None
            inherited_amounts = payload.get("amounts")
            if field is None and inherited_amounts and origin.rule_id in {"A01", "U01", "A02"}:
                previous_amounts = state.actual.get("computed_amounts")
                if previous_amounts and same_amounts(inherited_amounts, previous_amounts.value) and not same_amounts(inherited_amounts, state.expected_amounts()):
                    field = "amounts"
            if field and origin.first_fault_event_id != event.event_id:
                f = origin.model_copy(deep=True)
                f.finding_id = f"{run.run_id}:f{len(findings)+1}"
                f.origin_finding_id = origin.finding_id
                f.first_fault_event_id = event.event_id
                f.evidence_event_ids = [origin.first_fault_event_id, event.event_id]
                f.field_paths = [f"events.{event.event_id}.observed_input" if event.step_id == "SUBMIT_INVOICE" else f"events.{event.event_id}.observed_output"]
                f.explanation = "保留上游错误字段或金额，属于同一原因的传播。"
                findings.append(f)

    for event in events:
        state.before(event)
        target = state.values()
        out = event.observed_output
        content = out.get("preview", out)
        expected = state.expected_amounts()
        if event.step_id == "PARSE_INTENT":
            checked.add("I01")
            for field in ("intent", "invoice_task_type"):
                if field == "intent" and target[field] == "UNKNOWN":
                    questions.append({"question_id": f"intent-{event.seq}", "kind": "intent", "event_id": event.event_id, "prompt": "结合入口用户口语及已知上下文，判断当前意图识别是否正确。区分取消与暂缓，不把不明确要求直接当开票授权。"})
                    continue
                if field in out and out[field] != target[field]:
                    add("I01", event, field, target[field], out[field], f"任务{field}与当时有效用户要求不符。", group="cancel" if target["intent"] == "CANCEL" else None)
        if event.step_id in {"COLLECT_INVOICE_DATA", "CONFIRM_INVOICE"}:
            if "preview" in out:
                propagate(event, content)
            checked.update({"E01", "F01", "A01", "A02", "P01", "B01", "U01"})
            for field in ("buyer_id", "amount_value", "amount_basis", "original_invoice_id"):
                value = content.get(field)
                wanted = target.get(field)
                if field == "amount_basis" and wanted is None and target.get("amount_value") is None and target["invoice_task_type"] == "RED" and not case.context.get("amount_basis_unspecified"):
                    # Red-invoice amounts await the original invoice. A basis
                    # placeholder alone is not evidence of an invented amount.
                    continue
                if value is None:
                    continue  # A missing field is not fabricated data.
                if wanted is None:
                    rule = "E01" if field == "buyer_id" and len(case.context.get("buyer_candidates", [])) > 1 else "F01"
                    add(rule, event, field, None, value, f"{field}缺失或有歧义，却填入了未经用户提供的值。")
                elif not same_value(wanted, value):
                    rule = "U01" if field in state.updated or (field == "amount_value" and case.context.get("conversation_rounds")) else {"buyer_id": "E01", "amount_basis": "A01", "amount_value": "A02", "original_invoice_id": "F01"}[field]
                    add(rule, event, field, wanted, value, f"{field}未遵守当前有效目标。")
            if content.get("item_id") and target.get("item_id") and content["item_id"] != target["item_id"]:
                add("P01", event, "item_id", target["item_id"], content["item_id"], "所选商品与用户描述匹配的商品不同。")
            if content.get("item_id") and target.get("item_id") is None and not any(q.get("selected_item") == content["item_id"] and q.get("target_epoch") == state.target["item_id"].known_at_seq for q in questions):
                questions.append({"question_id": f"product-{event.seq}", "kind": "product", "event_id": event.event_id, "prompt": "商品近义描述与所选商品是否匹配？仅判断当前事件可知证据。"})
                questions[-1].update(selected_item=content["item_id"], target_epoch=state.target["item_id"].known_at_seq)
            wanted_lines = target.get("line_items")
            if wanted_lines and "line_items" in content:
                def signature(lines):
                    return Counter((i.get("item_id"), str(money(i["exclusive_amount"])) if i.get("exclusive_amount") is not None else None) for i in lines)
                if signature(wanted_lines) != signature(content["line_items"]) or content.get("line_count", len(content["line_items"])) != len(wanted_lines):
                    add("B01", event, "line_items", wanted_lines, content["line_items"], "批量明细发生遗漏、合并、重复或内容变化，合计正确不能消除此问题。")
            actual_amounts = content.get("computed_amounts", content.get("amounts"))
            if actual_amounts is not None:
                # Recalculate from the agent's own inputs to distinguish arithmetic
                # errors from correctly propagated bad upstream data.
                prior = {k: v.value for k, v in state.actual.items()}
                value = content.get("amount_value", prior.get("amount_value"))
                basis = content.get("amount_basis", prior.get("amount_basis"))
                item = content.get("item_id", prior.get("item_id"))
                calculated = None
                if wanted_lines and same_value(value, target.get("amount_value")):
                    calculated = expected
                elif value is not None and basis in {"tax_inclusive", "tax_exclusive"} and item in rates:
                    calculated = amounts(value, basis, rates[item])
                if calculated and not same_amounts(calculated, actual_amounts):
                    add("A02", event, "computed_amounts", calculated, actual_amounts, "按当前金额口径及配置税率复算不一致。")
        if event.step_id == "VALIDATE_DATA":
            checked.add("R01")
            returned_risks = set(out.get("risk_flags", []))
            for tool in event.tool_calls:
                if tool.name == "rules.validate":
                    returned_risks.update(tool.result.get("risk_flags", []))
            if "DUPLICATE_INVOICE" in state.risks | returned_risks and (out.get("validation_passed") is True or out.get("create_allowed") is True):
                add("R01", event, "risk_flags", "block duplicate", out, "已知重复风险未在校验环节阻断。")
            state.risks.update(returned_risks)
        if event.step_id == "SUBMIT_INVOICE" or any(t.name == "invoice.submit" for t in event.tool_calls):
            checked.update({"C01", "R01", "R02", "R03", "T01"})
            if target["intent"] == "UNKNOWN":
                questions.append({"question_id": f"intent-protection-{event.seq}", "kind": "intent_protection", "event_id": event.event_id, "prompt": "口语取消/暂缓或后续用户修订是否要求停止本次提交？若仍提交构成违反，定位其独立原因的最早可观察事件。"})
            if target["intent"] in {"CANCEL", "HOLD"}:
                rule = "R03"
                f = add(rule, event, "intent", "stop submitting", target["intent"], "取消或暂缓要求生效后仍继续提交。", group="cancel" if target["intent"] == "CANCEL" else "hold")
                if "intent" in state.updated:
                    f.label = "CONTEXT_UPDATE"
            if not state.confirmed:
                add("C01", event, "confirmation", True, False, "正式提交时没有本次有效内容的用户确认。", [state.preview_event] if state.preview_event else [])
            if "DUPLICATE_INVOICE" in state.risks:
                add("R01", event, "risk_flags", "blocked", "submit", "重复风险已可见，仍发起提交。")
            payloads = [event.observed_input] + [t.arguments for t in event.tool_calls if t.name == "invoice.submit"]
            for payload in payloads:
                propagate(event, payload)
                for field in state.updated - {"intent"}:
                    if field in payload and not same_value(payload[field], target[field]):
                        add("U01", event, field, target[field], payload[field], "用户修改生效后仍提交旧字段。")
                    elif field in {"amount_value", "amount_basis", "line_items"} and payload.get("amounts") and expected and not same_amounts(payload["amounts"], expected):
                        add("U01", event, field, expected, payload["amounts"], "用户修改生效后仍提交旧金额。")
            if state.preview and state.confirmed:
                for payload in payloads:
                    for field in ("buyer_id", "item_id", "invoice_task_type", "amounts", "line_items", "original_invoice_id"):
                        if field in payload and field in state.preview and not same_value(payload[field], state.preview[field]):
                            preview_ok = same_amounts(state.preview[field], expected) if field == "amounts" else same_value(state.preview[field], target.get(field))
                            if preview_ok:
                                add("T01", event, field, state.preview[field], payload[field], "已确认的正确预览与提交参数发生漂移。", [state.preview_event])
            for tool in event.tool_calls:
                if tool.name != "invoice.submit":
                    continue
                if state.submissions and state.submissions[-1]["status"] in {"unknown", "timeout"}:
                    prior = state.submissions[-1]
                    key = tool.arguments.get("idempotency_key")
                    stable = key and key == prior["key"]
                    justified = event.observed_input.get("retry_authorized") is True or tool.arguments.get("retry_basis") == "idempotent_replay"
                    if not (stable and justified):
                        add("R02", event, "tool_calls", "stable idempotency key AND explicit retry basis", tool.model_dump(), "受理状态未知后无稳定幂等键及明确依据而再次提交。", [prior["event_id"]])
                state.submissions.append({"status": tool.result.get("submit_status"), "key": tool.arguments.get("idempotency_key"), "event_id": event.event_id})
        if state.callback == "failed" and event.seq > next(e.seq for e in events if e.event_id == state.callback_event):
            checked.add("O01")
            if event.step_id == "COMPLETE_TASK" or out.get("invoice_created") is True:
                add("O01", event, "callback_status", "report failure", out, "收到明确失败回调后，处理事件仍将任务标为成功。", [state.callback_event])
            elif out.get("user_message") and not any(word in out["user_message"] for word in ("失败", "未生成", "未开具", "未开票", "转人工", "人工核查")):
                questions.append({"question_id": f"feedback-{event.seq}", "kind": "feedback", "event_id": event.event_id, "prompt": "在明确失败回调之后，这段反馈是否误报开票成功？"})
        state.after(event)
        snapshots.append(state.snapshot(event))

    service_values = []
    for event in events:
        if event.observed_output.get("callback_status") in {"success", "failed"}:
            service_values.append(event.observed_output["callback_status"] == "success")
        for tool in event.tool_calls:
            if tool.name == "invoice.callback" and tool.result.get("status") in {"success", "failed"}:
                service_values.append(tool.result["status"] == "success")
    if run.invoice_created is not None:
        service_values.append(run.invoice_created)
    created = service_values[0] if service_values and len(set(service_values)) == 1 else None
    if created is None:
        review.append("服务出票事实缺失或互相矛盾。")
    # A truncated trace can prove a violated precondition while not revealing
    # who/which action first violated it. Preserve known outcome, unknown cause.
    if created is True and not findings and not case.context.get("user_confirmation_recorded",False) and not any(e.observed_input.get("user_confirmation") is True for e in events) and case.reference_result.get("invoice_should_exist") is False:
        refs=[e.event_id for e in events if e.observed_output.get("callback_status")=="success" or any(t.name=="invoice.callback" and t.result.get("status")=="success" for t in e.tool_calls)]
        if refs:
            findings.append(Finding(finding_id=f"{run.run_id}:unlocated-confirmation",rule_id="C01",label="INSUFFICIENT_EVIDENCE",severity="risk",first_fault_event_id=None,evidence_event_ids=refs,field_paths=["context.user_confirmation_recorded","reference_result.invoice_should_exist"]+[f"events.{r}.observed_output" for r in refs],expected="未确认任务不能出票",observed="服务已明确出票；缺少违规动作的可观察事件",explanation="确认前置和未出票目标已被违反，但轨迹未提供足够信息定位违规提交首错。",causal_group_id=f"{run.run_id}:unlocated-confirmation"))
    action = case.reference_result.get("required_action", "")
    requested_fields = {f for e in events for f in e.observed_output.get("requested_fields", [])}
    required_fields = set(case.context.get("required_missing_fields", []))
    if case.context.get("amount_basis_unspecified"):
        required_fields.add("amount_basis")
    if case.context.get("buyer_disambiguation_required"):
        required_fields.add("buyer_disambiguation")
    missing_action = action.startswith("ASK_") and not required_fields <= requested_fields
    if not findings and (run.final_state != case.reference_result.get("acceptable_snapshot_state") or missing_action):
        questions.append({"question_id": "terminal", "kind": "terminal", "event_id": events[-1].event_id, "prompt": "最终状态与业务目标不同：是否存在可证明的独立违规？正确等待、外部失败不自动算错。"})
    # Verify independently derived terminal targets against authoritative goals.
    for key in ("buyer_id", "item_id", "amount_value", "amount_basis", "invoice_task_type"):
        goal = case.reference_result.get(key)
        actual_target = state.values().get(key)
        if goal is not None and actual_target is not None and not same_value(goal, actual_target):
            review.append(f"终态目标与用户/context来源冲突: {key}")
    goal_amounts = case.reference_result.get("expected_amounts")
    derived_amounts = state.expected_amounts()
    if goal_amounts and derived_amounts and not same_amounts(goal_amounts, derived_amounts):
        review.append("reference_result 终态金额与用户/context及配置税率复算冲突。")
    checks = [{"rule_id": rule, "status": "fail" if any(f.rule_id == rule for f in findings) else "pass" if rule in checked else "not_applicable"} for rule in RULE_LABELS]
    return {"findings": findings, "states": snapshots, "questions": questions, "review": review, "created": created, "checks": checks}
