# 评测器质量报告

`compare-labels` 比较评测结论与给定参考标注，用于校准评测器。报告同时输出总体一致性、逐根因标签/风险类别的混淆矩阵、样本数、漏判/误报和置信区间，并保留具体分歧。

这与业务行为覆盖不同：覆盖回答“测到了哪些行为”；本报告回答“在有参考答案的样本上，评测器判断得怎么样”。报告默认标记为开发集对比，不能代替独立验收。

## 使用

已有完整提交文件时：

```powershell
.venv/Scripts/python.exe -m invoice_eval compare-labels --submission runs/local/submission.jsonl --labels data/your-labels.jsonl --cases data/your-cases.jsonl --output runs/quality/label-metrics.json
```

存在待复核运行、没有生成完整 submission 时，推荐直接读运行目录：

```powershell
.venv/Scripts/python.exe -m invoice_eval compare-labels --run runs/local --labels data/your-labels.jsonl --output runs/quality/label-metrics.json --confidence 0.95
```

`--run` 从 `run_assessments.jsonl` 读取已完成的单条结论，包括尚未成对的运行，并默认使用 `cases.snapshot.jsonl` 校验归属及识别取消/暂缓上下文；缺失结果仍保留在参考样本分母中。不能把未完成的任务伪装成空根因的成功结果。

生成同名 `.json` 和 `.md`，JSON 保留全部数字、比例分子/分母、混淆矩阵、分歧和文件哈希，Markdown 可直接阅读。`--confidence` 必须在 0 和 1 之间，默认 95%。

`evaluate` 不读取专家标注；本命令在评测后独立执行，不修改质量分、业务结论、行为覆盖或发布建议。

## 参考标注与输入检查

标签为非空 JSONL，每行至少包括 `case_id`、`run_assessments` 和 `version_direction_on_this_case`。每条运行包含 `run_id`、布尔型 `agent_task_success` / `business_invoice_created` 以及 `root_causes`；根因包含合法 `label` 和首错 `first_fault_event_id`（确实未定位可为 null）。这是逐 Case 格式，不是 `register-acceptance` 的逐 run 期望格式。

参考标注需要完整列出每条运行的已知根因。没有某标签通常按该类负例统计；若存在 `INSUFFICIENT_EVIDENCE`，已明确列出的类别仍为正例，其余类别保留未知，避免将未知根因当成“其他风险都不存在”。预测同理。

重复 Case/run、非法标签、非布尔判断、预测与参考的跨 Case run 归属冲突会报错，防止字典覆盖或类型转换悄悄改变分母。提供源轨迹时还核对参考运行归属和双方首错事件归属。没有参考标注的额外预测单列为未标注，不直接当作误报。

## 按运行计算的逐类别指标

对每个根因标签，将每条参考运行视为一个分类样本：正例表示该运行存在该类错误。一个运行内同类多个根因，在这一层只计一个正例。

| 计数 | 含义 |
|---|---|
| TP | 参考有该错误，评测也检出 |
| FN | 参考有该错误，评测明确给出结果但未检出 |
| FP | 参考没有该错误，评测却报告存在 |
| TN | 参考没有该错误，评测也未报告存在 |
| prediction_unknown_positive/negative | 参考正例/负例中，预测缺失或对应类别无法判定 |
| reference_unknown | 参考本身不能确定该类别，不进入该类混淆矩阵 |

| 指标 | 公式 | 解释 |
|---|---|---|
| recall | TP / (TP + FN) | 已有明确判断中，实际错误检出了多少 |
| miss_rate | FN / (TP + FN) | 已有明确判断中，实际错误漏判了多少 |
| false_positive_rate | FP / (FP + TN) | 实际没有该类错误的样本中，有多少被误报 |
| precision | TP / (TP + FP) | 报出的错误中，有多少与参考一致 |
| false_discovery_rate | FP / (TP + FP) | 报出的错误中，有多少是误报 |
| assessment_coverage | (TP + FP + FN + TN) / 参考已知类别的运行数 | 该类有多少样本获得明确判定 |
| end_to_end_miss_rate | (FN + 正例中的未知/缺失预测) / 参考正例数 | 包含未产出明确结论的整体漏检比例 |

例如，3 个参考正例中检出 1 个、明确漏判 1 个、待复核 1 个：条件漏判率为 1/2，端到端漏检率为 2/3。待复核不是已确认错判，因此不塞进 FN；也不能通过删除它来美化整体结果。

无正例时漏判率为 null，无负例时误报率为 null，没有预测正例时 precision 和 false_discovery_rate 为 null。报告显示“不可估计”，不会预填完美结果。

## 风险类别与角色拆分

`quality.per_label` 包含当前全部 14 种根因标签。`quality.per_risk` 聚焦：

- `CONFIRMATION_BYPASS`：未经有效确认提交。
- `DUPLICATE_CONTROL`：重复开票控制。
- `TOOL_ERROR_HANDLING`：受理未知后的错误重试。
- `STOP_REQUEST_ERROR`：明确取消/暂缓上下文中的意图或上下文错误。

最后一项是上下文筛选类别，不将所有 `INTENT_CLASSIFICATION` / `CONTEXT_UPDATE` 都视为严重风险。优先使用源轨迹中的用户目标，也支持参考标注的 CANCELED 终态或根因明确标注 `rule_id=R03`。缺少上下文且只有相关通用标签时保留未知。这类统计不证明每个上下文错误的底层机制相同。

`quality.by_role` 分别统计裁判在 baseline、candidate 轨迹上的表现，不是重新比较被测 Agent 的业务质量。提供 `--cases`/`--run` 时以源轨迹的 runs 顺序识别角色；否则恰有两条参考运行时使用参考顺序，其余标为 unspecified。生产分析应提供源轨迹。

## 同标签多根因与首错定位

`quality.per_label.<label>.root_instances` 保留参考/预测根因数、标签匹配数、首错同时匹配数、缺失和额外根因数。先保留全部精确匹配，再匹配剩余同标签根因。

例如一条运行有两个独立字段伪造，评测只找到一个：运行级分类可以是 TP，但根因实例召回只有 1/2。标签正确而首错事件错误时，属于定位分歧，不自动变成标签漏判。

双方首错均为 null 只能说明都未定位，可以匹配根因标签，但不计入定位正确的分子。

根因实例可能来自同一运行，报告不为这些相关计数额外套用独立二项区间。原有总体 `root_precision`、`root_recall`、`first_fault_joint_*` 等字段继续保留；新逐类表用于补充诊断。

## 置信区间的解释

采用双侧 Wilson score 区间，每个比例都保留样本数和置信水平。零漏判不代表真实漏判概率已经被证明为零：只有 1 个正例且没有漏判时，95% 区间上界约为 79.3%。

区间假设样本独立且抽样具有代表性。同一 Case 的基线和候选相关，合并区间仅作描述性参考；优先查看分角色区间，并审查跨 Case 的用户/模板相关性。它们是逐项区间，未作多重比较校正，也不是版本差异显著性检验。

开发数据、定向风险样本、重复模板或有偏标注，都不会因增加区间而自动变成独立线上估计。风险样本召回率不能解释为线上风险发生率；模型与人工一致也不能证明双方没有共同误判。

## 追溯

`input_integrity` 列出参考/预测规模、缺失预测和未标注预测。`quality.run_results` 保留 Case/run、版本角色及双方根因，`examples` 指向各类别的漏判、误报和未知样本，原有 `disagreements` 保留首错等分歧。

`provenance` 保存指标版本、比较程序源码哈希、预测文件哈希、参考标签哈希以及可选源 Case 内容哈希。输入或标准变更后应生成新报告；旧评测运行仍可分析，但该分析不能替代针对新评测器重新执行独立验收。
