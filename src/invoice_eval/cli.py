import argparse
import json
from pathlib import Path
from dotenv import load_dotenv
from .export import validate_records
from .loader import load_cases, read_jsonl, write_json
from .metrics import compare_labels, write_quality_report
from .pipeline import ROOT, evaluate
from .report import render_report
from .resources import DEFAULT_CONFIG, OUTPUT_SCHEMA, BEHAVIORS

def main(argv=None):
    parser = argparse.ArgumentParser(description="开票 Agent 离线轨迹评测器")
    parser.add_argument("--env-file", type=Path)
    subs = parser.add_subparsers(dest="command", required=True)
    schema = subs.add_parser("schema", help="Show the supported canonical input JSON Schema; no data required")
    schema.add_argument("--output", type=Path)
    behaviors = subs.add_parser("behavior-catalog", help="Export the versioned business behavior inventory; no data required")
    behaviors.add_argument("--output", type=Path)
    ev = subs.add_parser("evaluate")
    ev.add_argument("--cases", type=Path, required=True)
    ev.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ev.add_argument("--output", type=Path, required=True)
    ev.add_argument("--fresh",action="store_true",help="Bypass model cache reads and writes")
    va = subs.add_parser("validate")
    va.add_argument("--cases", type=Path, required=True)
    va.add_argument("--submission", type=Path, required=True)
    co = subs.add_parser("compare-labels")
    prediction = co.add_mutually_exclusive_group(required=True)
    prediction.add_argument("--submission", type=Path)
    prediction.add_argument("--run", type=Path, help="Use individual results, including valid unpaired runs; missing runs remain in the denominator")
    co.add_argument("--labels", type=Path, required=True)
    co.add_argument("--output", type=Path, required=True)
    co.add_argument("--cases",type=Path,help="Supply source cases for seq-based root matching and cancellation risk")
    co.add_argument("--confidence", type=float, default=0.95, help="Wilson interval confidence level between 0 and 1 (default: 0.95)")
    regression = subs.add_parser("compare-evaluators", help="Compare old/new evaluator artifacts on identical input snapshots")
    regression.add_argument("--old-run", type=Path, required=True)
    regression.add_argument("--new-run", type=Path, required=True)
    regression.add_argument("--labels", type=Path, help="Optional fixed reference labels; never read by evaluate")
    regression.add_argument("--confidence", type=float, default=0.95)
    regression.add_argument("--output", type=Path, required=True)
    re = subs.add_parser("report")
    re.add_argument("--run", type=Path, required=True)
    ti = subs.add_parser("timeline")
    ti.add_argument("--run", type=Path, required=True)
    ti.add_argument("--output", type=Path, required=True)
    ti.add_argument("--audit", type=Path)
    au = subs.add_parser("judge-audit")
    au.add_argument("--run", type=Path, required=True)
    au.add_argument("--config", type=Path, required=True)
    au.add_argument("--output", type=Path, required=True)
    au.add_argument("--case-ids", nargs="+")
    au.add_argument("--fresh",action="store_true")
    au.add_argument("--workers",type=int,default=1)
    ad = subs.add_parser("adapt")
    ad.add_argument("--input", type=Path, required=True)
    ad.add_argument("--format", choices=["canonical", "envelope", "otel"], required=True)
    ad.add_argument("--output", type=Path, required=True)
    st = subs.add_parser("stability")
    st.add_argument("--cases", type=Path, required=True)
    st.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    st.add_argument("--output", type=Path, required=True)
    st.add_argument("--repeats", type=int, default=3)
    st.add_argument("--fresh",action="store_true")
    reg=subs.add_parser("register-acceptance")
    reg.add_argument("--cases",type=Path,required=True)
    reg.add_argument("--expectations",type=Path,required=True)
    reg.add_argument("--author",required=True)
    reg.add_argument("--independent-human",action="store_true",help="Only the independent human reviewer may attest this")
    reg.add_argument("--output",type=Path,required=True)
    acc=subs.add_parser("verify-acceptance")
    acc.add_argument("--registration",type=Path,required=True)
    acc.add_argument("--run",type=Path,required=True)
    acc.add_argument("--output",type=Path,required=True)
    rev=subs.add_parser("review-template")
    rev.add_argument("--run",type=Path,required=True)
    rev.add_argument("--output",type=Path,required=True)
    support=subs.add_parser("review-summary")
    support.add_argument("--run",type=Path,required=True)
    support.add_argument("--review",type=Path,required=True)
    support.add_argument("--output",type=Path,required=True)
    args = parser.parse_args(argv)
    load_dotenv(args.env_file or Path.cwd() / ".env", override=False)
    try:
        if args.command in {"schema", "behavior-catalog"}:
            from .models import Case
            value = Case.model_json_schema() if args.command == "schema" else json.loads(BEHAVIORS.read_text(encoding="utf-8"))
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                write_json(args.output, value)
                print(args.output)
            else:
                print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0
        if args.command == "evaluate":
            result = evaluate(args.cases, args.config, args.output,cache_policy="bypass" if args.fresh else "use")
            print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
            return 0 if result["status"] == "success" else 2
        if args.command == "validate":
            print(json.dumps(validate_records(load_cases(args.cases), read_jsonl(args.submission), OUTPUT_SCHEMA), ensure_ascii=False))
        elif args.command == "compare-labels":
            source = args.cases or (args.run / "cases.snapshot.jsonl" if args.run else None)
            result = compare_labels(args.run or args.submission, args.labels, load_cases(source) if source else None,
                                    confidence=args.confidence, prediction_format="run" if args.run else "submission")
            markdown = write_quality_report(result, args.output)
            print(str(args.output))
            print(str(markdown))
        elif args.command == "compare-evaluators":
            from .regression import compare_evaluators, write_comparison
            result = compare_evaluators(args.old_run, args.new_run, args.labels, confidence=args.confidence)
            markdown = write_comparison(result, args.output)
            print(args.output)
            print(markdown)
        elif args.command == "report":
            print(render_report(args.run))
        elif args.command == "timeline":
            from .timeline import render_timeline
            print(render_timeline(args.run,args.output,args.audit))
        elif args.command == "judge-audit":
            from .audit import audit
            result=audit(args.run,args.config,args.output,args.case_ids,args.fresh,args.workers)
            print(json.dumps({k:result[k] for k in ("valid_pairs","agreement_pairs","disagreement_pairs")},ensure_ascii=False))
            return 0 if result["valid_pairs"]==len(result["comparisons"]) else 2
        elif args.command == "adapt":
            from .adapters import adapt
            print(adapt(args.input,args.format,args.output))
        elif args.command == "stability":
            from .stability import stability
            result=stability(args.cases,args.config,args.output,args.repeats,args.fresh)
            print(json.dumps(result,ensure_ascii=False))
            return 0 if result["all_evaluations_valid"] else 2
        elif args.command=="register-acceptance":
            from .acceptance import register
            register(args.cases,args.expectations,args.output,args.author,args.independent_human)
            print(args.output)
        elif args.command=="verify-acceptance":
            from .acceptance import verify_acceptance
            result=verify_acceptance(args.registration,args.run,args.output)
            print(json.dumps({"passed":result["passed"],"evidence_id":result["evidence_id"]}))
            return 0 if result["passed"] else 2
        elif args.command=="review-template":
            from .acceptance import review_template
            review_template(args.run,args.output);print(args.output)
        elif args.command=="review-summary":
            from .acceptance import summarize_review
            print(json.dumps(summarize_review(args.run,args.review,args.output),ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}")
        return 2
