"""Explicit trace transport adapters; missing business evidence is never invented."""
import copy
import json
from pathlib import Path
from .loader import load_cases, read_jsonl, write_jsonl, write_json, file_hash
from .models import Case

def normalize(rows, format_name):
    if format_name == "canonical":
        values=rows
    elif format_name == "envelope":
        values=[]
        for row in rows:
            value=copy.deepcopy(row["task"])
            value["runs"]=row["trajectories"]
            values.append(value)
    elif format_name == "otel":
        values=[]
        for row in rows:
            value=copy.deepcopy(row["case"])
            runs={r["run_id"]:{**copy.deepcopy(r),"events":[]} for r in row["run_metadata"]}
            for resource in row["resourceSpans"]:
                for scope in resource["scopeSpans"]:
                    for span in scope["spans"]:
                        attrs={a["key"]:a["value"] for a in span["attributes"]}
                        run_id=attrs["invoice.run_id"]["stringValue"]
                        event=json.loads(attrs["invoice.event_json"]["stringValue"])
                        runs[run_id]["events"].append(event)
            value["runs"]=list(runs.values())
            values.append(value)
    else:
        raise ValueError(f"unsupported adapter: {format_name}")
    return [Case.model_validate(v).model_dump() for v in values]

def adapt(source, format_name, output):
    source,output=Path(source),Path(output)
    if output.exists():
        raise FileExistsError(output)
    rows=normalize(read_jsonl(source),format_name)
    output.parent.mkdir(parents=True,exist_ok=True)
    pending=output.with_suffix(".pending.jsonl")
    write_jsonl(pending,rows)
    load_cases(pending)
    pending.replace(output)
    write_json(output.with_suffix(".provenance.json"),{"adapter":format_name,"source_hash":file_hash(source),"output_hash":file_hash(output),"case_count":len(rows),"event_ids_preserved":True,"executed_tools":False})
    return output
