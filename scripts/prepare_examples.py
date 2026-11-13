import json
from invoice_eval.loader import load_cases,write_jsonl
from invoice_eval.pipeline import ROOT

directory=ROOT/"examples"
directory.mkdir(exist_ok=True)
cases=load_cases(next((ROOT/"material").glob("02*.jsonl")))
c=cases[0].model_dump()
write_jsonl(directory/"envelope.jsonl",[{"task":{k:v for k,v in c.items() if k!="runs"},"trajectories":c["runs"]}])
write_jsonl(directory/"otel.jsonl",[{"case":{k:v for k,v in c.items() if k!="runs"},"run_metadata":[{k:v for k,v in r.items() if k!="events"} for r in c["runs"]],"resourceSpans":[{"scopeSpans":[{"spans":[{"attributes":[{"key":"invoice.run_id","value":{"stringValue":r["run_id"]}},{"key":"invoice.event_json","value":{"stringValue":json.dumps(e,ensure_ascii=False)}}]} for r in c["runs"] for e in r["events"]]}]}]}])
semantic=cases[11].model_copy(deep=True)
semantic.case_id="SYN-SEMANTIC-SERVICE"
semantic.user_request="给客户CUST-111开一张与软件有关的服务费用专票，不含税7200元，开票前确认。"
for run in semantic.runs:run.events[0].observed_input["user_request"]=semantic.user_request
write_jsonl(directory/"semantic.jsonl",[semantic.model_dump()])
print(directory)
