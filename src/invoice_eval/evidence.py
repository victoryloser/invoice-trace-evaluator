from .models import LABELS, AuditResult, JudgeResult

def resolve_path(document, path):
    current = document
    for part in path.split("."):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current

def validate_judge(result, packet, question):
    if isinstance(result, AuditResult):
        if result.verdict == "pass" and result.causes:
            raise ValueError("passing audit contains causes")
        if result.verdict == "fail" and not result.causes:
            raise ValueError("failing audit must inventory all causes")
        signatures = [(c.label,c.first_fault_event_id,tuple(sorted(c.field_paths))) for c in result.causes]
        if len(signatures) != len(set(signatures)):
            raise ValueError("duplicate audit causes")
        top = JudgeResult(status=result.status, question_id=result.question_id, verdict="pass" if result.verdict=="fail" else result.verdict, label=None, first_fault_event_id=None, evidence_event_ids=result.evidence_event_ids, field_paths=result.field_paths, explanation=result.explanation, missing_evidence=result.missing_evidence)
        validate_judge(top,packet,question)
        for cause in result.causes:
            validate_judge(JudgeResult(status=result.status,question_id=result.question_id,verdict="fail",missing_evidence=result.missing_evidence,**cause.model_dump()),packet,question)
        return result
    if result.question_id != question["question_id"]:
        raise ValueError("Judge question_id mismatch")
    if result.label is not None and result.label not in LABELS:
        raise ValueError("Judge illegal label")
    allowed = {"product": {"PRODUCT_MATCH", "INSUFFICIENT_EVIDENCE"}, "feedback": {"CALLBACK_MISINTERPRETATION", "INSUFFICIENT_EVIDENCE"}, "intent": {"INTENT_CLASSIFICATION", "CONTEXT_UPDATE", "INSUFFICIENT_EVIDENCE"}, "intent_protection": {"INTENT_CLASSIFICATION", "CONTEXT_UPDATE", "INSUFFICIENT_EVIDENCE"}}
    if result.verdict == "fail" and question.get("kind") in allowed and result.label not in allowed[question["kind"]]:
        raise ValueError("Judge label unrelated to assigned question")
    if result.verdict == "pass" and (result.label is not None or result.first_fault_event_id is not None):
        raise ValueError("passing verdict cannot contain a fault")
    events = packet["events"]
    if any(x not in events for x in result.evidence_event_ids):
        raise ValueError("Judge cross-run/nonexistent evidence")
    if len(result.evidence_event_ids) != len(set(result.evidence_event_ids)):
        raise ValueError("Judge duplicate evidence")
    first = result.first_fault_event_id
    if first is not None:
        if first not in result.evidence_event_ids:
            raise ValueError("first fault must be cited")
        if events[first]["seq"] > question["known_at_seq"]:
            raise ValueError("future first fault")
    if result.status == "success" and result.verdict != "unknown":
        if not result.evidence_event_ids or not result.field_paths:
            raise ValueError("Judge missing supporting evidence")
        if result.verdict == "fail" and (not result.label or (first is None and result.label != "INSUFFICIENT_EVIDENCE")):
            raise ValueError("Judge missing cause")
    for path in result.field_paths:
        try:
            resolve_path(packet, path)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ValueError(f"Judge nonexistent field path: {path}") from exc
    related = [p.split(".")[1] for p in result.field_paths if p.startswith("events.")]
    if result.status == "success" and result.verdict != "unknown" and not set(related) & set(result.evidence_event_ids):
        raise ValueError("Judge field paths do not support cited events")
    if result.verdict == "fail" and first is not None and first not in related:
        raise ValueError("first fault must have a supporting field path")
    label_fields={"AMOUNT_BASIS":{"amount_basis"},"AMOUNT_CALCULATION":{"computed_amounts","amounts","amount_value","tax_amount"},"PRODUCT_MATCH":{"item_id","line_items","item_query"},"ENTITY_RESOLUTION":{"buyer_id","buyer_query","candidates"},"FIELD_FABRICATION":{"buyer_id","amount_value","amount_basis","original_invoice_id"},"MULTI_ITEM_OMISSION":{"line_items","line_count"},"CONFIRMATION_BYPASS":{"user_confirmed","invoice_confirmed","tool_calls"},"TOOL_ARGUMENT_ERROR":{"arguments","amounts","buyer_id","item_id","line_items"},"DUPLICATE_CONTROL":{"risk_flags","validation_passed","create_allowed","tool_calls"},"TOOL_ERROR_HANDLING":{"tool_calls","retry_count","submit_status"},"CALLBACK_MISINTERPRETATION":{"callback_status","user_message","invoice_created","step_id"},"INTENT_CLASSIFICATION":{"intent","invoice_task_type","user_request","step_id"},"CONTEXT_UPDATE":{"user_update","user_request","amount_value","amounts","buyer_id","item_id","intent"}}
    def keys(value):
        if isinstance(value,dict):return set(value).union(*(keys(v) for v in value.values()))
        if isinstance(value,list):return set().union(*(keys(v) for v in value))
        return set()
    if result.verdict=="fail" and first and result.label in label_fields:
        anchored=set()
        for path in result.field_paths:
            if path.startswith(f"events.{first}."):
                anchored.update(path.split("."));anchored.update(keys(resolve_path(packet,path)))
        if not anchored & label_fields[result.label]:raise ValueError("first fault lacks label-relevant business evidence")
    # An event ID alone is not a factual anchor: require relevant business data.
    if result.status == "success" and result.verdict != "unknown":
        category = question.get("kind")
        fields = {"product": ("item_id", "item_candidates", "item_query", "line_items"), "feedback": ("user_message", "callback_status", "status"), "intent": ("intent", "user_request", "invoice_task_type"), "intent_protection": ("intent", "user_request", "user_update", "user_confirmed")}.get(category)
        if fields and not any(any(part in path.split(".") for part in fields) for path in result.field_paths if path.startswith("events.")):
            raise ValueError("evidence fields unrelated to assigned business question")
        for path in result.field_paths:
            value = resolve_path(packet,path)
            if value is None and len(result.field_paths)==1:
                raise ValueError("a null field alone cannot support the verdict")
    return result
