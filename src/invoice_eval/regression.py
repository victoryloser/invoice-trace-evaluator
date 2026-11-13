"""Compare evaluator artifacts on a fixed corpus, without rerunning either Judge."""
from collections import Counter
import json
from math import isfinite
from pathlib import Path

from .loader import digest, file_hash, load_cases, read_jsonl, source_hash, write_json
from .matching import match_causes
from .metrics import _index_records, _load_predictions, compare_labels
from .models import LABELS
from .quality import RISK_CATEGORIES, membership, proportion


IDENTITY_KEYS = ("version", "source_hash", "config_hash", "decision_config_hash", "rule_hash",
                 "workflow_hash", "schema_hash", "lock_hash", "scoring_version",
                 "behavior_catalog_hash", "judge", "cache_policy")
FIELDS = ("agent_task_success", "business_invoice_created", "quality_score")


def _load_run(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") not in {"success", "needs_review", "failed"}:
        raise ValueError(f"{directory}: evaluation must have a terminal status")
    for key in ("evaluation_id", "source_hash", "config_hash"):
        if not manifest.get(key):
            raise ValueError(f"{directory}: manifest missing {key}")
    names = ["cases.snapshot.jsonl", "config.snapshot.json", "run_assessments.jsonl"]
    if (directory / "assessments.jsonl").exists() or "assessments.jsonl" in manifest.get("artifacts", {}):
        names.append("assessments.jsonl")
    hashes = {}
    for name in names:
        expected = manifest.get("artifacts", {}).get(name)
        if not expected or not (directory / name).is_file() or file_hash(directory / name) != expected:
            raise ValueError(f"{directory}: artifact hash mismatch or missing artifact: {name}")
        hashes[name] = expected
    cases = load_cases(directory / "cases.snapshot.jsonl")
    context = {r.run_id: (c.case_id, r, "baseline" if i == 0 else "candidate")
               for c in cases for i, r in enumerate(c.runs)}
    rows, _ = _load_predictions(directory, "run")
    records, owners = _index_records(rows, str(directory))
    assessments = {a["run_id"]: a for row in rows for a in row["run_assessments"]}
    for run_id, assessment in assessments.items():
        if run_id not in context or owners[run_id] != context[run_id][0]:
            raise ValueError(f"{directory}: prediction {run_id} outside fixed corpus or wrong case")
        score = assessment.get("quality_score")
        if type(score) not in (int, float) or not isfinite(score) or not 0 <= score <= 100:
            raise ValueError(f"{directory}: {run_id} requires a finite quality_score in [0, 100]")
        events = {e.event_id for e in context[run_id][1].events}
        for cause in assessment["root_causes"]:
            first = cause.get("first_fault_event_id")
            if first is not None and first not in events:
                raise ValueError(f"{directory}: first fault {first} outside run {run_id}")
            evidence = cause.get("evidence_event_ids", [])
            if not isinstance(evidence, list) or any(not isinstance(e, str) or e not in events for e in evidence):
                raise ValueError(f"{directory}: root evidence outside run {run_id}")
    return {"directory": directory, "manifest": manifest, "cases": cases, "context": context,
            "records": records, "assessments": assessments,
            "corpus_hash": digest([c.model_dump() for c in sorted(cases, key=lambda c: c.case_id)]),
            "provenance": {"directory": str(directory.resolve()), "manifest_hash": file_hash(directory / "manifest.json"),
                           "evaluation_id": manifest["evaluation_id"], "status": manifest["status"],
                           "identity": {k: manifest.get(k) for k in IDENTITY_KEYS}, "verified_artifacts": hashes}}


def _roots(before, after, order):
    old, new = before["root_causes"], after["root_causes"]
    pairs, removed, added = match_causes(old, new, order)
    return {"removed": [old[i] for i in removed], "added": [new[i] for i in added],
            "relocated": [{"old": old[i], "new": new[j]} for i, j, exact in pairs if not exact],
            "evidence_changed": [{"old": old[i], "new": new[j]} for i, j, exact in pairs
                                 if exact and set(old[i].get("evidence_event_ids", [])) != set(new[j].get("evidence_event_ids", []))]}


def _signature(roots, location=False):
    return Counter((r["label"], r.get("first_fault_event_id")) if location else r["label"] for r in roots)


def _correct(expected, observed, dimension, stop):
    if dimension in FIELDS[:2]:
        return None if observed is None else observed[dimension] == expected[dimension]
    if dimension in {"root_labels", "root_localization"}:
        if observed is None or any(r["label"] == "INSUFFICIENT_EVIDENCE" for r in expected["root_causes"] + observed["root_causes"]):
            return None
        location = dimension == "root_localization"
        if location and any(r.get("first_fault_event_id") is None for r in expected["root_causes"] + observed["root_causes"]):
            return None
        return _signature(expected["root_causes"], location) == _signature(observed["root_causes"], location)
    category = dimension.split(":", 1)[1]
    gold, prediction = membership(expected, category, stop), membership(observed, category, stop)
    return None if gold is None or prediction is None else gold == prediction


def _transition(old, new):
    if old is None:
        return "both_unknown" if new is None else "resolved_correct" if new else "resolved_incorrect"
    if new is None:
        return "lost_correct" if old else "lost_incorrect"
    return "unchanged_correct" if old and new else "regression" if old else "improvement" if new else "unchanged_incorrect"


def _delta(old, new):
    a = old.get("value") if isinstance(old, dict) else old
    b = new.get("value") if isinstance(new, dict) else new
    return {"old": old, "new": new, "delta": None if a is None or b is None else b - a}


def _reference_comparison(old, new, labels, confidence):
    reports = [compare_labels(run["directory"], labels, old["cases"], prediction_format="run", confidence=confidence)
               for run in (old, new)]
    references, _ = _index_records(read_jsonl(labels), "references")
    contexts = {r["run_id"]: r for r in reports[0]["quality"]["run_results"]}
    dimensions = [*FIELDS[:2], "root_labels", "root_localization",
                  *("risk:" + k for k in RISK_CATEGORIES), *("label:" + k for k in LABELS)]
    rows = []
    for record in references.values():
        for expected in record["run_assessments"]:
            run_id = expected["run_id"]
            for dimension in dimensions:
                a, b = [_correct(expected, run["assessments"].get(run_id), dimension, contexts[run_id]["stop_context"])
                        for run in (old, new)]
                rows.append({"case_id": record["case_id"], "run_id": run_id, "role": old["context"][run_id][2],
                             "dimension": dimension, "old_correct": a, "new_correct": b, "transition": _transition(a, b)})
    transitions = {d: dict(Counter(r["transition"] for r in rows if r["dimension"] == d)) for d in dimensions}
    metrics = ("agent_task_success_accuracy", "business_invoice_created_accuracy", "coverage", "root_precision",
               "root_recall", "root_f1", "matched_localization_accuracy", "first_fault_joint_recall", "direction_agreement", "high_risk_missed")
    rates = ("miss_rate", "false_positive_rate", "false_discovery_rate", "end_to_end_miss_rate", "assessment_coverage")
    risks = {k: {rate: _delta(reports[0]["quality"]["per_risk"][k][rate], reports[1]["quality"]["per_risk"][k][rate])
                 for rate in rates} for k in RISK_CATEGORIES}
    return {"labels_path": str(Path(labels).resolve()), "labels_hash": file_hash(labels), "reference_runs": len(contexts),
            "unlabeled_runs": sorted(old["context"].keys() - contexts.keys()),
            "metric_deltas": {k: _delta(reports[0][k], reports[1][k]) for k in metrics},
            "risk_deltas": risks, "transitions": transitions, "run_transitions": rows,
            "regressed_run_ids": sorted({r["run_id"] for r in rows if r["transition"] in {"regression", "lost_correct"}}),
            "improved_run_ids": sorted({r["run_id"] for r in rows if r["transition"] in {"improvement", "resolved_correct"}}),
            "quality_reports": {"old": reports[0], "new": reports[1]}}


def compare_evaluators(old_run, new_run, labels=None, *, confidence=0.95):
    proportion(0, 0, confidence)
    old, new = _load_run(old_run), _load_run(new_run)
    if old["corpus_hash"] != new["corpus_hash"]:
        raise ValueError("fixed corpus mismatch: case content, runs and event evidence must be identical")
    rows = []
    for run_id, (case_id, run, role) in sorted(old["context"].items()):
        before, after = old["assessments"].get(run_id), new["assessments"].get(run_id)
        availability = "both_missing" if before is None and after is None else "newly_assessed" if before is None else "newly_missing" if after is None else "both_assessed"
        changes, roots = {}, None
        if before is not None and after is not None:
            changes = {k: {"old": before[k], "new": after[k]} for k in FIELDS if before[k] != after[k]}
            roots = _roots(before, after, {e.event_id: e.seq for e in run.events})
        changed = availability in {"newly_assessed", "newly_missing"} or bool(changes) or bool(roots and any(roots.values()))
        rows.append({"case_id": case_id, "run_id": run_id, "role": role, "availability": availability,
                     "changed": changed, "field_changes": changes, "root_changes": roots,
                     "old": before, "new": after})
    pairs = []
    for case in sorted(old["cases"], key=lambda c: c.case_id):
        values = [{k: run["records"].get(case.case_id, {}).get("version_comparison", {}).get(k)
                   for k in ("direction", "release_recommendation")} for run in (old, new)]
        pairs.append({"case_id": case.case_id, "old": values[0], "new": values[1], "changed": values[0] != values[1]})
    identity_changes = {k: {"old": old["manifest"].get(k), "new": new["manifest"].get(k)} for k in IDENTITY_KEYS
                        if old["manifest"].get(k) != new["manifest"].get(k)}
    counts = Counter(r["availability"] for r in rows)
    result = {"comparison_version": "1.0", "comparison_source_hash": source_hash(),
              "corpus": {"content_hash": old["corpus_hash"], "cases": len(old["cases"]), "runs": len(rows)},
              "provenance": {"old": old["provenance"], "new": new["provenance"]}, "identity_changes": identity_changes,
              "summary": {"changed_runs": sum(r["changed"] for r in rows), "total_runs": len(rows),
                          "changed_run_rate": sum(r["changed"] for r in rows) / len(rows) if rows else None,
                          "availability": {k: counts[k] for k in ("both_assessed", "newly_assessed", "newly_missing", "both_missing")},
                          "changed_agent_pair_decisions": sum(p["changed"] for p in pairs)},
              "run_diffs": rows, "agent_pair_diffs": pairs, "reference_comparison": None,
              "limits": ["old/new 指评测器版本；baseline/candidate 指同一 Case 内的 Agent 轨迹角色。",
                         "缺失运行仍计入固定样本分母；两次均缺失不表示结论正确或评测完整。",
                         "无参考标注时，判定、根因、分数和证据变化只表示漂移，不能据此判断质量改善。",
                         "参考标注只用于事后比较；局部修正与回归可出现在同一运行，不合成为自动发布结论。",
                         "指标差值为 new-old；两端 Wilson 区间不是差值置信区间，不作显著性结论。",
                         "代码、配置、规则或模型可同时变化；单次比较不能分离其因果贡献，也不能排除模型随机性。",
                         "产物哈希校验用于发现意外修改，不是签名认证；开发集回归不能替代独立验收。"]}
    if labels is not None:
        result["reference_comparison"] = _reference_comparison(old, new, labels, confidence)
    return result


def render_comparison(result):
    def cell(value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\n", " ").replace("\r", " ")
    def value(v):
        if isinstance(v, dict):
            return f"{v['numerator']}/{v['denominator']} ({v['value']:.3f})" if v["value"] is not None else f"{v['numerator']}/{v['denominator']} (未知)"
        return "未知" if v is None else f"{v:.4g}" if isinstance(v, float) else str(v)
    summary = result["summary"]
    lines = ["# 固定样本评测器版本对比", "", f"固定 {result['corpus']['cases']} 个 Case / {summary['total_runs']} 条运行；{summary['changed_runs']} 条运行发生变化。",
             f"输入内容哈希：`{result['corpus']['content_hash']}`", "",
             f"判定可用性：{cell(summary['availability'])}；Agent A/B 比较结论变化 {summary['changed_agent_pair_decisions']} 个 Case。", "",
             "## 评测器版本", "", "| 版本 | evaluation_id | 状态 | source_hash |", "|---|---|---|---|"]
    for side in ("old", "new"):
        p = result["provenance"][side]
        lines.append("| " + " | ".join(cell(v) for v in (side, p["evaluation_id"], p["status"], p["identity"]["source_hash"])) + " |")
    lines.extend(["", "身份变更字段：" + (", ".join(result["identity_changes"]) or "无（同身份回放）") + "。", "",
                  "## 逐运行变化", "", "| Case / run | Agent 角色 | 可用性 | 字段变化 | 根因变化 |", "|---|---|---|---|---|"])
    for row in result["run_diffs"]:
        if row["changed"] or row["availability"] == "both_missing":
            roots = row["root_changes"]
            detail = "; ".join(f"{k}: {cell(v['old'])} → {cell(v['new'])}" for k, v in row["field_changes"].items()) or "—"
            root_detail = "; ".join(f"{k}: {cell(v)}" for k, v in (roots or {}).items() if v) or "—"
            lines.append(f"| {cell(row['case_id'])} / {cell(row['run_id'])} | {row['role']} | {row['availability']} | {detail} | {root_detail} |")
    lines.extend(["", "## Agent A/B 结论变化", "", "| Case | old | new |", "|---|---|---|"])
    for pair in result["agent_pair_diffs"]:
        if pair["changed"]:
            lines.append(f"| {cell(pair['case_id'])} | {cell(pair['old'])} | {cell(pair['new'])} |")
    reference = result["reference_comparison"]
    if reference:
        lines.extend(["", "## 参考标注下的变化", "", f"有标注 {reference['reference_runs']} 条；未标注 {len(reference['unlabeled_runs'])} 条。",
                      f"存在回归或丢失正确判定的运行 {len(reference['regressed_run_ids'])} 条；存在修正或恢复正确判定的运行 {len(reference['improved_run_ids'])} 条（可重叠）。", "",
                      "| 指标 | old | new | new-old |", "|---|---|---|---|"])
        for key, item in reference["metric_deltas"].items():
            lines.append(f"| {key} | {value(item['old'])} | {value(item['new'])} | {value(item['delta'])} |")
        lines.extend(["", "### 风险类别", "", "| 风险 / 指标 | old | new | new-old |", "|---|---|---|---|"])
        for key, metrics in reference["risk_deltas"].items():
            for metric, item in metrics.items():
                lines.append(f"| {key} / {metric} | {value(item['old'])} | {value(item['new'])} | {value(item['delta'])} |")
        lines.extend(["", "### 判定正确性转移", "", "improvement/regression：错→对 / 对→错；resolved_*：未知→已判；lost_*：已判→未知。", "",
                      "| 维度 | 转移计数 |", "|---|---|"])
        for key, counts in reference["transitions"].items():
            lines.append(f"| {key} | {cell(counts)} |")
        lines.extend(["", "### 需复核的逐项转移", "", "| Case / run | 维度 | 转移 |", "|---|---|---|"])
        for row in reference["run_transitions"]:
            if row["transition"] not in {"unchanged_correct", "unchanged_incorrect", "both_unknown"}:
                lines.append(f"| {cell(row['case_id'])} / {cell(row['run_id'])} | {row['dimension']} | {row['transition']} |")
    else:
        lines.extend(["", "未提供参考标注：只报告变化，不判断改善/回归。"])
    lines.extend(["", "## 解释边界", "", *("- " + note for note in result["limits"]),
                  "", "完整旧/新判定、事件证据、Agent A/B 结论、指标支持数与区间见同名 JSON。"])
    return "\n".join(lines) + "\n"


def write_comparison(result, output):
    output = Path(output)
    markdown = output.with_suffix(".md")
    if output == markdown:
        raise ValueError("JSON output must not use the .md extension")
    reference = result["reference_comparison"]
    if reference and Path(reference["labels_path"]) in {output.resolve(), markdown.resolve()}:
        raise ValueError("comparison output must not overwrite reference labels")
    # A comparison must never rewrite the evaluation artifacts it describes.
    for side in ("old", "new"):
        source = Path(result["provenance"][side]["directory"]).resolve()
        if output.resolve().is_relative_to(source) or markdown.resolve().is_relative_to(source):
            raise ValueError("comparison output must be outside old/new evaluation directories")
    output.parent.mkdir(parents=True, exist_ok=True)
    text = render_comparison(result)
    write_json(output, result)
    markdown.write_text(text, encoding="utf-8")
    return markdown
