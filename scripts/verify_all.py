"""Offline verification with optional user-supplied cases and expert labels."""
import argparse
from pathlib import Path
import subprocess
import sys

from invoice_eval.export import validate_records
from invoice_eval.loader import load_cases, read_jsonl, write_json, source_hash, write_jsonl
from invoice_eval.metrics import compare_labels, write_quality_report
from invoice_eval.pipeline import evaluate
from invoice_eval.resources import DEFAULT_CONFIG, OUTPUT_SCHEMA
from invoice_eval.stability import stability
from invoice_eval.timeline import render_timeline

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--labels", type=Path)
    args = parser.parse_args()
    if args.labels and not args.cases:
        parser.error("--labels requires --cases")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    subprocess.run([sys.executable, "-m", "pytest", str(ROOT/"tests"), "-q", f"--junitxml={output/'pytest.xml'}"], cwd=ROOT, check=True)
    source = args.cases
    if source is None:
        sys.path.insert(0, str(ROOT/"tests"))
        from test_onboarding import fictional_case
        source = output/"fictional-input.jsonl"
        write_jsonl(source, [fictional_case()])
    run = output/"evaluation"
    result = evaluate(source, DEFAULT_CONFIG, run)
    if result["status"] != "success":
        raise ValueError("Evaluation needs review; see the generated run directory")
    validation = validate_records(load_cases(source), read_jsonl(run/"submission.jsonl"), OUTPUT_SCHEMA)
    write_json(output/"format-validation.json", validation)
    stability(source, DEFAULT_CONFIG, output/"stability", 2)
    render_timeline(run, output/"timeline.html")
    if args.labels:
        write_quality_report(compare_labels(run/"submission.jsonl", args.labels, load_cases(source)), output/"label-metrics.json")
    write_json(output/"verification.json", {"source_hash":source_hash(), "format_validation_passed":True,
               "input":"user-supplied" if args.cases else "temporary fictional records", "model_calls":False})
    print("Offline checks passed:", output)


if __name__ == "__main__":
    main()
