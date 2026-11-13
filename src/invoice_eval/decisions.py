"""Join business checks, semantic decisions and independently observed facts."""
from .models import Finding
from .rules import RULE_LABELS

QUESTION_RULES = {"product": ["P01"], "feedback": ["O01"], "intent": ["I01","U01"], "intent_protection": ["R03"], "terminal": []}

def synchronize_checks(analysis):
    checks = {c["rule_id"]:dict(c) for c in analysis["checks"]}
    for f in analysis["findings"]:
        check=checks.setdefault(f.rule_id,{"rule_id":f.rule_id})
        check.update(status="fail", evidence_event_ids=list(dict.fromkeys(check.get("evidence_event_ids",[])+f.evidence_event_ids)))
    semantic_states={}
    for q in analysis["questions"]:
        rules=QUESTION_RULES.get(q["kind"],[]) or ["SEMANTIC:"+q["question_id"]]
        state=analysis.get("question_states",{}).get(q["question_id"],"unknown")
        for rule in rules:
            c=checks.setdefault(rule,{"rule_id":rule,"status":"not_applicable"})
            semantic_states.setdefault(rule,[]).append(state)
            c.setdefault("semantic_questions",[]).append(q["question_id"])
    for rule,states in semantic_states.items():
        if checks[rule]["status"]!="fail":
            checks[rule]["status"]="fail" if "fail" in states else "unknown" if "unknown" in states else "pass"
    analysis["checks"]=list(checks.values())

def reconcile(result, question, analysis, run):
    """Reject model contradictions; never erase a supported deterministic finding."""
    seq={e.event_id:e.seq for e in run.events}
    limit=seq[question["event_id"]]
    scoped=QUESTION_RULES.get(question["kind"],[])
    facts=[f for f in analysis["findings"] if f.status=="fail" and not f.finding_id.startswith(run.run_id+":judge:") and (not scoped or f.rule_id in scoped) and (f.first_fault_event_id is None or seq[f.first_fault_event_id]<=limit)]
    states=analysis.setdefault("question_states",{})
    if result is None:
        states[question["question_id"]]="unknown"
        analysis["review"].append(f"必需语义判断未完成: {question['question_id']}")
        return {"status":"unresolved","accepted":False}
    conflict=None
    if result.verdict=="pass" and facts:
        conflict="模型判通过，但已有同范围确定性违规证据。"
    if result.verdict=="fail" and result.label!="INSUFFICIENT_EVIDENCE":
        rule=next((k for k,v in RULE_LABELS.items() if v==result.label),None)
        if question["kind"]=="intent_protection" and result.label in {"INTENT_CLASSIFICATION","CONTEXT_UPDATE"}:
            rule="R03"
        if rule in {"A01","A02","C01","T01","R01","R02","B01"}:
            proven=[f for f in analysis["findings"] if f.rule_id==rule and f.first_fault_event_id==result.first_fault_event_id]
            checked=next((c["status"] for c in analysis["checks"] if c["rule_id"]==rule),"not_applicable")
            if not proven and checked=="pass":
                conflict="模型所称强约束违规与程序复算/前置状态检查相反。"
        if result.label=="PRODUCT_MATCH" and result.first_fault_event_id:
            snapshot=next(s for s in analysis["states"] if s["event_id"]==result.first_fault_event_id)
            target=snapshot["target"].get("item_id",{}).get("value")
            actual=snapshot["actual"].get("item_id",{}).get("value")
            if target is not None and target==actual:
                conflict="模型商品错误结论与当前已知商品目标及实际选择相反。"
    if conflict:
        analysis.setdefault("conflicts",[]).append({"question_id":question["question_id"],"model_result":result.model_dump(),"reason":conflict,"resolution":"deterministic_facts_preserved","fact_ids":[f.finding_id for f in facts]})
        states[question["question_id"]]="fail" if facts else "unknown"
        if not facts:
            analysis["review"].append("语义结论与确定性事实冲突，需要重新判断: "+question["question_id"])
        return {"status":"conflict","accepted":False,"reason":conflict}
    if result.label=="INSUFFICIENT_EVIDENCE":
        if facts:
            states[question["question_id"]]="fail"
            analysis.setdefault("attribution_notes",[]).append(result.model_dump())
            return {"status":"known_failure","accepted":True,"reason":"业务失败已有独立事实，不因模型未能归因撤销。"}
        states[question["question_id"]]="unknown"
        analysis["review"].append("根因未知且尚无独立可核验的业务成败事实: "+question["question_id"])
        return {"status":"unresolved","accepted":False}
    states[question["question_id"]]=result.verdict
    if result.verdict=="fail":
        rule=next((k for k,v in RULE_LABELS.items() if v==result.label),None)
        if question["kind"]=="intent_protection":rule="R03"
        if rule is None:
            raise ValueError("No supported rule for semantic failure")
        fid=f"{run.run_id}:judge:{question['question_id']}"
        origin=next((f for f in analysis["findings"] if f.label==result.label and f.first_fault_event_id==result.first_fault_event_id),None)
        analysis["findings"].append(Finding(finding_id=fid,rule_id=rule,label=result.label,first_fault_event_id=result.first_fault_event_id,evidence_event_ids=result.evidence_event_ids,field_paths=result.field_paths,expected=question["prompt"],observed=result.explanation,explanation=result.explanation,origin_finding_id=origin.finding_id if origin else None,causal_group_id=origin.causal_group_id if origin else fid))
    return {"status":"accepted","accepted":True}
