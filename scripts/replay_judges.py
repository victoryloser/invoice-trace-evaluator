"""Validate and replay saved model responses without any network call."""
import argparse
from pathlib import Path
from invoice_eval.evidence import validate_judge
from invoice_eval.judge import blind_packet
from invoice_eval.loader import load_cases,read_jsonl,write_json,source_hash
from invoice_eval.models import JudgeResult,AuditResult
from invoice_eval.rules import inspect_run

parser=argparse.ArgumentParser()
parser.add_argument("--run",type=Path,required=True)
parser.add_argument("--records",type=Path,required=True)
parser.add_argument("--output",type=Path,required=True)
args=parser.parse_args()
cases=load_cases(args.run/"cases.snapshot.jsonl")
by_run={r.run_id:(c,r) for c in cases for r in c.runs}
results=[]
for record in read_jsonl(args.records):
    if not record["valid_live"]:
        results.append({"run_id":record["run_id"],"question_id":record["question_id"],"status":"original_invalid","original_status":record["status"]})
        continue
    case,run=by_run[record["run_id"]]
    if "question" in record:
        question=record["question"]
    elif record["question_id"]=="first-independent-fault":
        question={"question_id":record["question_id"],"kind":"audit","event_id":max(run.events,key=lambda e:e.seq).event_id}
    else:
        question=next(q for q in inspect_run(case,run)["questions"] if q["question_id"]==record["question_id"])
    packet,q,mapping=blind_packet(case,run,question)
    response_model=AuditResult if question.get("kind")=="audit_all" else JudgeResult
    result=validate_judge(response_model.model_validate_json(record["raw_response"]),packet,q)
    for item in [result,*getattr(result,"causes",[])]:
        item.evidence_event_ids=[mapping[x] for x in item.evidence_event_ids]
        if hasattr(item,"first_fault_event_id") and item.first_fault_event_id:item.first_fault_event_id=mapping[item.first_fault_event_id]
        for alias,original in mapping.items():
            item.field_paths=[p.replace(f"events.{alias}.",f"events.{original}.") for p in item.field_paths]
    assert result.model_dump()==record["mapped_result"]
    results.append({"run_id":run.run_id,"question_id":q["question_id"],"status":"replayed","result":result.model_dump()})
write_json(args.output,{"source_hash":source_hash(),"network_calls":0,"replayed":sum(r["status"]=="replayed" for r in results),"original_invalid":sum(r["status"]=="original_invalid" for r in results),"records":results})
print(args.output)
