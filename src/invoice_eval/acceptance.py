"""Preregistered acceptance and human evidence review, with explicit attestations."""
import json
from pathlib import Path
from datetime import datetime,timezone
from .loader import digest,file_hash,load_cases,read_jsonl,write_json,source_hash
from .matching import match_causes

def now():return datetime.now(timezone.utc).isoformat()

def register(cases_path,expectations_path,output,author,independent=False):
    if not author.strip():raise ValueError("acceptance author required")
    cases=load_cases(cases_path);expected=read_jsonl(expectations_path)
    ids={r.run_id for c in cases for r in c.runs}
    if {r["run_id"] for r in expected}!=ids or len(expected)!=len(ids):raise ValueError("expectations must cover every run exactly once")
    for e in expected:
        if type(e["agent_task_success"]) is not bool or type(e["business_invoice_created"]) is not bool:raise ValueError("expected booleans required")
        for cause in e["root_causes"]:
            if not cause.get("label"):raise ValueError("expected root label required")
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    artifact={"kind":"acceptance-registration","registered_at":now(),"author":author,"independence":"human_attested" if independent else "development","source_hash_at_registration":source_hash(),"cases_hash":file_hash(cases_path),"expectations_hash":file_hash(expectations_path),"case_ids":[c.case_id for c in cases],"run_ids":sorted(ids),"expectations":expected,"note":"独立性是外部审核人的声明，不是程序能自行证明的事实。"}
    artifact["registration_id"]=digest(artifact);write_json(output,artifact)
    return artifact

def verify_registration(registration):
    if digest({k:v for k,v in registration.items() if k!="registration_id"})!=registration["registration_id"]:raise ValueError("registration changed")

def verify_acceptance(registration_path,run_directory,output):
    reg=json.loads(Path(registration_path).read_text(encoding="utf-8"));verify_registration(reg)
    directory=Path(run_directory);manifest=json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
    if manifest["input_hash"]!=reg["cases_hash"]:raise ValueError("acceptance dataset mismatch")
    if manifest["started_at"]<=reg["registered_at"]:raise ValueError("acceptance was not preregistered before evaluation")
    if manifest["source_hash"]!=source_hash():raise ValueError("acceptance evaluation source is stale")
    for name,sha in manifest["artifacts"].items():
        if file_hash(directory/name)!=sha:raise ValueError("evaluation artifact changed: "+name)
    records=read_jsonl(directory/"run_assessments.jsonl") if (directory/"run_assessments.jsonl").stat().st_size else []
    predictions={r["assessment"]["run_id"]:r["assessment"] for r in records};comparisons=[]
    for e in reg["expectations"]:
        p=predictions.get(e["run_id"]);exact=False
        if p:
            pairs,missing,extra=match_causes(e["root_causes"],p["root_causes"])
            exact=not missing and not extra and all(x[2] for x in pairs) and all(e[k]==p[k] for k in ("agent_task_success","business_invoice_created"))
        comparisons.append({"run_id":e["run_id"],"passed":exact})
    passed=manifest["status"]=="success" and all(c["passed"] for c in comparisons) and set(predictions)==set(reg["run_ids"])
    result={"kind":"acceptance-evidence","passed":passed,"source_hash":source_hash(),"registration":reg,"registration_hash":file_hash(registration_path),"evaluation_manifest_hash":file_hash(directory/"manifest.json"),"evaluation_id":manifest["evaluation_id"],"evaluated_at":manifest["started_at"],"config_hash":manifest["config_hash"],"decision_config_hash":manifest["decision_config_hash"],"rule_hash":manifest["rule_hash"],"schema_hash":manifest["schema_hash"],"comparisons":comparisons}
    result["evidence_id"]=digest(result);write_json(output,result)
    return result

def load_gate_evidence(gate,current_manifest):
    reports=[];errors=[];covered=set()
    paths=gate.get("evidence_files",[])
    if not paths:errors.append("缺少已绑定源码与配置的独立验收证据文件。")
    for path in paths:
        try:
            report=json.loads(Path(path).read_text(encoding="utf-8"))
            if digest({k:v for k,v in report.items() if k!="evidence_id"})!=report["evidence_id"]:raise ValueError("evidence content changed")
            verify_registration(report["registration"])
            for key in ("source_hash","decision_config_hash","rule_hash","schema_hash"):
                if report[key]!=current_manifest[key]:raise ValueError("stale "+key)
            if not report["passed"] or not all(c["passed"] for c in report["comparisons"]):raise ValueError("independent regression did not pass")
            if {c["run_id"] for c in report["comparisons"]}!=set(report["registration"]["run_ids"]):raise ValueError("acceptance run coverage incomplete")
            if report["registration"]["independence"]!="human_attested":raise ValueError("development evidence is not independent human acceptance")
            if report["evaluated_at"]<=report["registration"]["registered_at"]:raise ValueError("not preregistered")
            covered.update(report["registration"]["case_ids"]);reports.append({"evidence_id":report["evidence_id"],"file_hash":file_hash(path),"report":report})
        except (ValueError,KeyError,OSError) as exc:errors.append(f"{path}: {exc}")
    if not set(gate.get("independent_required_cases",[]))<=covered:errors.append("独立验收证据未覆盖全部预登记 Case。")
    return {"verified":bool(reports) and not errors,"evidence":reports,"covered_cases":sorted(covered),"errors":errors}

def review_items(directory):
    items=[]
    for row in read_jsonl(Path(directory)/"run_assessments.jsonl"):
        a=row["assessment"]
        for index,root in enumerate(a["root_causes"]):
            items.append({"item_id":f"{a['run_id']}:root:{index}","case_id":row["case_id"],"run_id":a["run_id"],"conclusion":root,"supported":None,"comment":""})
        if not a["root_causes"]:
            items.append({"item_id":f"{a['run_id']}:success","case_id":row["case_id"],"run_id":a["run_id"],"conclusion":{"agent_task_success":a["agent_task_success"],"evidence_event_ids":a["evidence_event_ids"]},"supported":None,"comment":""})
    return items

def review_template(run_directory,output):
    directory=Path(run_directory)
    template={"kind":"human-evidence-review","evaluation_manifest_hash":file_hash(directory/"manifest.json"),"reviewer":"","independent":False,"completed_at":None,"items":review_items(directory)}
    write_json(output,template);return template

def summarize_review(run_directory,review_path,output):
    directory=Path(run_directory);review=json.loads(Path(review_path).read_text(encoding="utf-8"))
    if review["evaluation_manifest_hash"]!=file_hash(directory/"manifest.json"):raise ValueError("review refers to another evaluation")
    if not review["reviewer"] or not review["completed_at"]:raise ValueError("human reviewer and completion time required")
    if type(review["independent"]) is not bool:raise ValueError("independent must be a boolean attestation")
    if datetime.fromisoformat(review["completed_at"]).tzinfo is None:raise ValueError("review completion time must include timezone")
    mapped={x["item_id"]:x for x in review_items(directory)};ids=[x["item_id"] for x in review["items"]]
    if len(ids)!=len(set(ids)) or set(ids)!=set(mapped):raise ValueError("review inventory changed")
    for item in review["items"]:
        if any(item[k]!=mapped[item["item_id"]][k] for k in ("conclusion","case_id","run_id")):raise ValueError("review conclusion changed")
        if item["supported"] is not None and type(item["supported"]) is not bool:raise ValueError("supported must be bool or null")
        if item["supported"] is not None and not item["comment"].strip():raise ValueError("reviewed conclusions require supporting rationale")
    reviewed=[i for i in review["items"] if i["supported"] is not None]
    result={"kind":"human-evidence-support","reviewer":review["reviewer"],"independence":"human_attested" if review["independent"] else "development","review_hash":file_hash(review_path),"evaluation_manifest_hash":review["evaluation_manifest_hash"],"reviewed":len(reviewed),"total":len(ids),"supported":sum(i["supported"] for i in reviewed),"support_rate":sum(i["supported"] for i in reviewed)/len(reviewed) if reviewed else None,"pending":len(ids)-len(reviewed)}
    write_json(output,result);return result
