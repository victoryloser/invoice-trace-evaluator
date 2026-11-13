"""Live coverage of semantic branches, uncached stability, and full dual audit."""
import argparse
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from invoice_eval.loader import load_cases,read_jsonl,write_json,write_jsonl,source_hash
from invoice_eval.models import Case
from invoice_eval.pipeline import ROOT,evaluate
from invoice_eval.stability import stability
from invoice_eval.audit import audit

parser=argparse.ArgumentParser()
parser.add_argument("--env-file",type=Path,required=True)
parser.add_argument("--output",type=Path,required=True)
parser.add_argument("--workers",type=int,default=3)
parser.add_argument("--resume-audit",action="store_true",help="Keep completed semantic/stability runs and resume with current dual config")
args=parser.parse_args()
load_dotenv(args.env_file, override=True)
args.output.mkdir(parents=True,exist_ok=args.resume_audit)
config=json.loads((ROOT/"configs/dual.json").read_text(encoding="utf-8"))
config_path=args.output/("audit.config.json" if args.resume_audit else "live.config.json");write_json(config_path,config)
train=load_cases(next((ROOT/"material").glob("02*.jsonl")))
def renamed(case,name):
    return Case.model_validate(json.loads(json.dumps(case.model_dump(),ensure_ascii=False).replace(case.case_id,name)))
product=renamed(train[11],"SEM-SERVICE")
product.user_request="给客户CUST-111开一张与软件有关的服务费用专票，不含税7200元，开票前确认。"
cancel=renamed(train[8],"SEM-CANCEL")
cancel.user_request="这单就算了吧，别往下弄了。"
feedback=renamed(train[9],"SEM-FEEDBACK")
feedback.runs[0].events[-1].observed_output["user_message"]="电子票都出好了，你去下载吧。"
for c in (product,cancel,feedback):
    for r in c.runs:r.events[0].observed_input["user_request"]=c.user_request
semantic_path=args.output/"semantic-cases.jsonl";write_jsonl(semantic_path,[c.model_dump() for c in (product,cancel,feedback)])
print("Required semantic branches: product, cancellation and callback feedback",flush=True)
semantic=json.loads((args.output/"semantic/manifest.json").read_text(encoding="utf-8")) if args.resume_audit else evaluate(semantic_path,config_path,args.output/"semantic",cache_policy="bypass")
expected={"SEM-SERVICE-A":False,"SEM-SERVICE-B":True,"SEM-CANCEL-A":False,"SEM-CANCEL-B":True,"SEM-FEEDBACK-A":False,"SEM-FEEDBACK-B":False}
observed={x["assessment"]["run_id"]:x["assessment"]["agent_task_success"] for x in read_jsonl(args.output/"semantic/run_assessments.jsonl")} if (args.output/"semantic/run_assessments.jsonl").stat().st_size else {}
semantic_expected=observed==expected
print("Semantic:",semantic["status"],"expected outcomes:",semantic_expected,flush=True)
product_path=args.output/"stability-cases.jsonl";write_jsonl(product_path,[product.model_dump()])
print("Fresh live stability: three repeats, cache disabled",flush=True)
repeat=json.loads((args.output/"fresh-stability/stability.json").read_text(encoding="utf-8")) if args.resume_audit else stability(product_path,config_path,args.output/"fresh-stability",3,fresh=True)
print("Fresh stability:",repeat["network_attempts"],"network attempts;",repeat["changed_conclusions"],"changed outcomes",flush=True)
all_cases=[*train,*load_cases(next((ROOT/"material").glob("04*.jsonl")))]
combined=args.output/"all-cases.jsonl";write_jsonl(combined,[c.model_dump() for c in all_cases])
if not args.resume_audit:evaluate(combined,ROOT/"configs/default.json",args.output/"audit-input")
print("Full dual audit: 22 cases / 44 runs / 88 judgments, all independent causes",flush=True)
dual_path=args.output/("dual-budget-adjusted" if args.resume_audit else "dual-all")
dual=audit(args.output/"audit-input",config_path,dual_path,workers=args.workers)
if dual["valid_pairs"]!=44:
    print("Rerunning full audit with valid-response cache; only invalid responses need new calls",flush=True)
    dual_path=args.output/("dual-budget-adjusted-rerun" if args.resume_audit else "dual-all-rerun")
    dual=audit(args.output/"audit-input",config_path,dual_path,workers=args.workers)
result={"source_hash":source_hash(),"semantic_status":semantic["status"],"semantic_expected_outcomes":semantic_expected,"semantic_runs":len(observed),"fresh_stability_valid":repeat["all_evaluations_valid"],"fresh_network_attempts":repeat["network_attempts"],"fresh_cache_hits":repeat["cache_hits"],"fresh_changed_conclusions":repeat["changed_conclusions"],"dual_total_pairs":len(dual["comparisons"]),"dual_valid_pairs":dual["valid_pairs"],"dual_agreement_pairs":dual["agreement_pairs"],"dual_disagreement_pairs":dual["disagreement_pairs"],"dual_report":str(dual_path.relative_to(args.output)/"dual-judge.json")}
write_json(args.output/"live-validation.json",result)
print(json.dumps(result,ensure_ascii=False),flush=True)
