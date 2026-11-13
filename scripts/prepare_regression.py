"""Development preregistration examples; expectations are written before execution.

These variants are authored by the implementation team, NOT a human-independent
holdout. The release gate must reject them as independent evidence.
"""
import copy
import json
from invoice_eval.pipeline import ROOT
from invoice_eval.loader import load_cases,write_jsonl,write_json

source=load_cases(next((ROOT/"material").glob("02*.jsonl")))
def renamed(case,new_id):
    return json.loads(json.dumps(case.model_dump(),ensure_ascii=False).replace(case.case_id,new_id))

c=renamed(source[0],"REG-MONEY-071")
c["user_request"]="给客户CUST-021开软件服务费专票，不含税123.45元，开票前确认。"
c["context"]["item_candidates"][0]["configured_rate"]=0.07
good={"exclusive_amount":123.45,"tax_amount":8.64,"inclusive_amount":132.09}
bad={"exclusive_amount":115.37,"tax_amount":8.08,"inclusive_amount":123.45}
c["reference_result"].update(amount_value=123.45,expected_amounts=good)
for index,run in enumerate(c["runs"]):
    amounts=good if index==0 else bad
    lines=[{"item_id":"ITEM-006","exclusive_amount":amounts["exclusive_amount"]}]
    for event in run["events"]:
        incoming,out=event["observed_input"],event["observed_output"]
        if "user_request" in incoming:incoming["user_request"]=c["user_request"]
        if "amount_value" in out:out.update(amount_value=123.45,computed_amounts=amounts,line_items=lines)
        if "preview" in out:out["preview"].update(amounts=amounts,line_items=lines)
        if "amounts" in incoming:incoming.update(amounts=amounts,line_items=lines)
        for tool in event["tool_calls"]:
            if "amounts" in tool["arguments"]:tool["arguments"]["amounts"]=amounts

b=renamed(source[6],"REG-BATCH-222")
b["user_request"]="给客户CUST-061批量开两张票：软件服务费不含税111.11元、实施服务费不含税222.22元，先预览。"
lines=[{"item_id":"ITEM-006","exclusive_amount":111.11},{"item_id":"ITEM-007","exclusive_amount":222.22}]
total={"exclusive_amount":333.33,"tax_amount":20.00,"inclusive_amount":353.33}
b["context"]["line_items"]=lines
b["reference_result"].update(amount_value=333.33,expected_amounts=total,expected_line_items=lines)
for index,run in enumerate(b["runs"]):
    actual=lines if index==0 else lines[:1]
    for event in run["events"]:
        if "user_request" in event["observed_input"]:event["observed_input"]["user_request"]=b["user_request"]
        out=event["observed_output"]
        if "amount_value" in out:out.update(amount_value=333.33,computed_amounts=total,line_items=actual)
        if "preview" in out:out["preview"].update(amounts=total,line_items=actual)

expectations=[
 {"run_id":"REG-MONEY-071-A","agent_task_success":True,"business_invoice_created":False,"root_causes":[]},
 {"run_id":"REG-MONEY-071-B","agent_task_success":False,"business_invoice_created":False,"root_causes":[{"label":"AMOUNT_BASIS","first_fault_event_id":"REG-MONEY-071-B-05"},{"label":"CONFIRMATION_BYPASS","first_fault_event_id":"REG-MONEY-071-B-08"}]},
 {"run_id":"REG-BATCH-222-A","agent_task_success":True,"business_invoice_created":False,"root_causes":[]},
 {"run_id":"REG-BATCH-222-B","agent_task_success":False,"business_invoice_created":False,"root_causes":[{"label":"MULTI_ITEM_OMISSION","first_fault_event_id":"REG-BATCH-222-B-05"}]}
]
write_jsonl(ROOT/"examples/regression-cases.jsonl",[c,b])
write_jsonl(ROOT/"examples/regression-expectations.jsonl",expectations)
write_json(ROOT/"examples/regression-scope.json",{"independence":"development","expected_authorship":"Written as fixed expectations before evaluator execution; not generated from evaluator outputs","independent_human_review":"pending"})
print("Prepared two development cases / four runs and fixed expectations")
