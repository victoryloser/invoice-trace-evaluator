import hashlib
import json
from pathlib import Path
from .models import Case

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def source_hash():
    root = Path(__file__).parent
    return digest({p.relative_to(root).as_posix(): file_hash(p) for p in sorted(root.rglob("*.py"))})

def read_jsonl(path):
    rows = []
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                rows.append(json.loads(line, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x))))
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    if not rows:
        raise ValueError(f"{path}: empty JSONL")
    return rows

def load_cases(path):
    cases, ids, run_ids, event_ids = [], set(), set(), set()
    for line_number, value in enumerate(read_jsonl(path), 1):
        try:
            case = Case.model_validate(value)
            if case.case_id in ids:
                raise ValueError(f"duplicate case_id {case.case_id}")
            ids.add(case.case_id)
            for run in case.runs:
                if run.run_id in run_ids:
                    raise ValueError(f"duplicate run_id {run.run_id}")
                run_ids.add(run.run_id)
                for event in run.events:
                    if event.event_id in event_ids:
                        raise ValueError(f"duplicate event_id {event.event_id}")
                    event_ids.add(event.event_id)
            cases.append(case)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return cases

def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

def write_jsonl(path, rows):
    Path(path).write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")
