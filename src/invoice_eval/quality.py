"""Run-level classifier diagnostics, with explicit abstentions and support."""
from math import sqrt
from statistics import NormalDist

from .models import LABELS


RISK_CATEGORIES = {
    "CONFIRMATION_BYPASS": "未经有效确认提交",
    "DUPLICATE_CONTROL": "重复开票控制",
    "TOOL_ERROR_HANDLING": "受理未知后的错误重试",
    "STOP_REQUEST_ERROR": "取消/暂缓场景中的意图或上下文错误",
}


def proportion(numerator, denominator, confidence=0.95):
    """Two-sided Wilson score interval. No samples means no estimate."""
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if not 0 <= numerator <= denominator:
        raise ValueError("invalid proportion counts")
    value = numerator / denominator if denominator else None
    interval = None
    if denominator:
        z = NormalDist().inv_cdf((1 + confidence) / 2)
        scale = 1 + z*z / denominator
        center = (value + z*z / (2*denominator)) / scale
        margin = z * sqrt(value*(1-value)/denominator + z*z/(4*denominator**2)) / scale
        interval = {"lower": max(0.0, center-margin), "upper": min(1.0, center+margin)}
    return {"numerator": numerator, "denominator": denominator, "value": value,
            "confidence_interval": interval, "confidence_level": confidence, "method": "wilson"}


def membership(assessment, category, stop_context):
    if assessment is None:
        return None
    roots = assessment["root_causes"]
    labels = {r["label"] for r in roots}
    if category == "STOP_REQUEST_ERROR":
        if any(r.get("rule_id") == "R03" for r in roots):
            return True
        if labels & {"INTENT_CLASSIFICATION", "CONTEXT_UPDATE"}:
            if stop_context is None:
                return None
            if stop_context:
                return True
    elif category in labels:
        return True
    # An explicitly unlocated/unknown root cannot establish the absence of
    # other error categories. Known positive labels still remain positive.
    return None if "INSUFFICIENT_EVIDENCE" in labels else False


def classification(samples, category, confidence):
    counts = dict(tp=0, fp=0, fn=0, tn=0)
    support = dict(reference_positive=0, reference_negative=0, reference_unknown=0,
                   prediction_unknown_positive=0, prediction_unknown_negative=0)
    examples = []
    for sample in samples:
        expected = membership(sample["expected"], category, sample["stop_context"])
        observed = membership(sample["observed"], category, sample["stop_context"])
        kind = None
        if expected is None:
            support["reference_unknown"] += 1
            kind = "reference_unknown"
        else:
            support["reference_positive" if expected else "reference_negative"] += 1
            if observed is None:
                support["prediction_unknown_positive" if expected else "prediction_unknown_negative"] += 1
                kind = "prediction_unknown"
            else:
                kind = "tp" if expected and observed else "fn" if expected else "fp" if observed else "tn"
                counts[kind] += 1
        if kind not in {"tp", "tn"}:
            examples.append({"case_id": sample["case_id"], "run_id": sample["run_id"], "kind": kind,
                             "expected": expected, "observed": observed})
    tp, fp, fn, tn = (counts[k] for k in ("tp", "fp", "fn", "tn"))
    known = support["reference_positive"] + support["reference_negative"]
    support.update(reference_runs=len(samples), assessed_runs=tp+fp+fn+tn,
                   assessed_positive=tp+fn, assessed_negative=fp+tn,
                   predicted_positive=tp+fp)
    def rate(n, d):
        return proportion(n, d, confidence)
    return {"support": support, "confusion": counts,
            "assessment_coverage": rate(tp+fp+fn+tn, known),
            "recall": rate(tp, tp+fn), "miss_rate": rate(fn, tp+fn),
            "false_positive_rate": rate(fp, fp+tn),
            "precision": rate(tp, tp+fp), "false_discovery_rate": rate(fp, tp+fp),
            "end_to_end_miss_rate": rate(fn+support["prediction_unknown_positive"], support["reference_positive"]),
            "examples": examples}


def quality_breakdown(samples, root_counts, confidence=0.95):
    def group(selected):
        labels = {label: classification(selected, label, confidence) for label in LABELS}
        risks = {key: {"name": name, **classification(selected, key, confidence)} for key, name in RISK_CATEGORIES.items()}
        return {"reference_runs": len(selected), "per_label": labels, "per_risk": risks}
    result = group(samples)
    for label, counts in root_counts.items():
        result["per_label"][label]["root_instances"] = counts
    result["by_role"] = {role: group([s for s in samples if s["role"] == role]) for role in ("baseline", "candidate", "unspecified")}
    result["uncertainty"] = {
        "method": "wilson", "confidence_level": confidence, "unit": "run-category",
        "assumptions": "区间为逐类别二项比例的 Wilson 近似，假设样本独立且抽样有代表性；不是同时置信区间。",
        "paired_run_caveat": "同一 Case 的基线与候选相关，合并区间仅作描述性参考；优先查看分角色区间。不同 Case 也可能相关。",
        "sampling_caveat": "开发集、定向风险样本及人工标注偏差不能通过置信区间消除；这些比例不代表线上风险发生率。",
        "zero_support": "无正例不能估计漏判率，无负例不能估计误报率；分母为零返回 null。",
        "stop_category": "STOP_REQUEST_ERROR 按明确取消/暂缓上下文筛选意图或上下文错误；上下文缺失且存在相关标签时保留未知，不将所有意图错误当作严重风险。",
    }
    result["run_results"] = [{"case_id": s["case_id"], "run_id": s["run_id"], "role": s["role"],
                              "prediction_available": s["observed"] is not None,
                              "stop_context": s["stop_context"],
                              "expected_roots": s["expected"]["root_causes"],
                              "observed_roots": s["observed"]["root_causes"] if s["observed"] else None} for s in samples]
    return result


def render_quality_report(result):
    def cell(value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\n", " ").replace("\r", " ")
    def rate(value):
        count = f"{value['numerator']}/{value['denominator']}"
        if value["value"] is None:
            return count + "（不可估计）"
        interval = value["confidence_interval"]
        return f"{count} = {value['value']:.1%} [{interval['lower']:.1%}, {interval['upper']:.1%}]"
    quality = result["quality"]
    lines = ["# 评测器质量报告", "", f"数据用途：{cell(result['data_role'])}。", "",
             f"参考任务 {result['input_integrity']['reference_cases']} 个，参考运行 {quality['reference_runs']} 条；有预测 {result['coverage']['numerator']} 条。",
             f"区间置信水平 {quality['uncertainty']['confidence_level']:.0%}，方法 Wilson。方括号为区间；零分母不预填 0% 或 100%。", "",
             "漏判率 FN/(TP+FN) 与误报率 FP/(FP+TN) 只用有明确预测的样本；端到端漏检率另将正例中的缺失/未知预测计入。误报占比 FP/(TP+FP) 表示报出的风险中有多少错误。", ""]
    def table(title, categories):
        lines.extend([f"## {title}", "", "| 类别 | 参考正/负/未知 | 有效判定 | 漏判率 | 误报率 | 误报占比 | 端到端漏检率 |",
                      "|---|---|---|---|---|---|---|"])
        for key, item in categories.items():
            s = item["support"]
            lines.append("| " + " | ".join([cell(item.get("name", key)), f"{s['reference_positive']}/{s['reference_negative']}/{s['reference_unknown']}",
                f"{s['assessed_runs']}/{s['reference_runs']}", *[rate(item[k]) for k in ("miss_rate", "false_positive_rate", "false_discovery_rate", "end_to_end_miss_rate")]]) + " |")
        lines.append("")
    table("风险类别（合并，描述性参考）", quality["per_risk"])
    for role, title in (("baseline", "基线轨迹上的裁判质量"), ("candidate", "候选轨迹上的裁判质量"), ("unspecified", "角色未指定")):
        if quality["by_role"][role]["reference_runs"]:
            table(title, quality["by_role"][role]["per_risk"])
    table("全部根因标签（按运行是否出现）", quality["per_label"])
    lines.extend(["## 独立根因与定位", "", "同标签的多个独立根因逐个匹配，不因按运行统计而折叠。缺失根因包含预测缺失的运行；定位错误与标签漏判分别记录。", "",
                  "| 标签 | 参考根因数 | 预测根因数 | 标签匹配 | 首错同时匹配 | 缺失根因 | 额外根因 |", "|---|---|---|---|---|---|---|"])
    for label, item in quality["per_label"].items():
        c = item["root_instances"]
        lines.append(f"| {label} | " + " | ".join(str(c[k]) for k in ("reference", "predicted", "matched", "localized", "missing", "extra")) + " |")
    lines.extend(["", "## 逐类别分歧与未判定", "", "| 类别 | Case | run | 类型 | 参考 / 预测 |", "|---|---|---|---|---|"])
    kinds = {"fp": "误报", "fn": "漏判", "prediction_unknown": "预测缺失/未知", "reference_unknown": "参考根因未知"}
    for key, item in quality["per_label"].items():
        for example in item["examples"]:
            lines.append("| " + " | ".join(cell(v) for v in (key, example["case_id"], example["run_id"], kinds[example["kind"]], f"{example['expected']} / {example['observed']}")) + " |")
    lines.extend(["", "## 解释边界", ""])
    lines.extend("- " + value for key, value in quality["uncertainty"].items() if key.endswith("caveat") or key in {"assumptions", "zero_support", "stop_category"})
    integrity = result["input_integrity"]
    lines.append(f"- 未标注的预测 Case：{len(integrity['unlabeled_prediction_cases'])}；未标注的预测 run：{len(integrity['unlabeled_prediction_runs'])}。单列记录，不擅自当作误报样本。")
    lines.append("- 本报告检查评测器与给定参考标注的一致性；不自动证明参考标注正确或满足独立验收要求。原始证据与定位分歧见 JSON 中的 quality.run_results、disagreements 和 provenance。")
    lines.append("- business_invoice_created 的一致性仅比较轨迹判定与参考标注；不代表本程序独立查询或核验过外部开票服务。")
    return "\n".join(lines) + "\n"
