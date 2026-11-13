# 固定样本上的评测器版本回归

`compare-evaluators` 比较旧、新两次评测器产物。它回答的是“规则、提示词、模型或评分配置调整后，裁判对相同轨迹的判断发生了什么变化”。Case 内原有的 `baseline/candidate` 仍指两个 Agent 的轨迹；命令中的 `old/new` 指两次评测器执行。

## 使用方法

在旧版本代码与环境中先执行并保存完整目录：

```powershell
python -m invoice_eval evaluate --cases data/fixed-cases.jsonl --config configs/default.json --output runs/evaluator-old
```

切换到新版本代码与环境，使用同一份输入再次执行。若有意修改配置，使用新配置并保留两边快照：

```powershell
python -m invoice_eval evaluate --cases data/fixed-cases.jsonl --config configs/default.json --output runs/evaluator-new
python -m invoice_eval compare-evaluators --old-run runs/evaluator-old --new-run runs/evaluator-new --output runs/regression/comparison.json
```

有固定参考标注时，增加 `--labels`，生成质量变化、风险指标差值和逐运行正确性转移：

```powershell
python -m invoice_eval compare-evaluators --old-run runs/evaluator-old --new-run runs/evaluator-new --labels data/fixed-labels.jsonl --output runs/regression/labeled.json
```

输出 JSON 与同名 Markdown。参考标注格式与 [compare-labels](evaluator-quality.md) 相同。参考标注只在事后对比中读取，`evaluate` 不接触该文件。可使用部分标注；未标注运行明确列出，不被当作负例。

旧版本应在旧 checkout/虚拟环境中真正执行；仅复制目录再重命名不能产生另一个版本的评测证据。对比命令本身不启动旧代码、不调用模型、不修改源评测目录。输出必须在两个评测目录外，且不得覆盖参考标注。

## 固定样本与可追溯性

- 读取两边的 `manifest.json`、`cases.snapshot.jsonl`、`config.snapshot.json`、`run_assessments.jsonl`，以及存在或在清单中登记的 `assessments.jsonl`。核对被读取产物与 manifest 中的哈希；缺失或被修改时拒绝比较。
- 用当前输入模型规范化快照，按 Case ID 排序后计算内容哈希。对象键顺序、JSON 空白与 Case 行顺序不影响对比；Case 内容、上下文、轨迹、事件证据必须相同。run 顺序用于定义 Agent 角色，保留其顺序；事件数组顺序也保留。不会通过取两批数据的交集掩盖样本变化。
- 校验 Case/run 唯一性和归属、首错及根因证据事件归属、有限的 0–100 质量分，检查成对与逐运行判定一致。当前输入契约无法解析的旧产物需要显式迁移，不能静默跳过。
- 记录两边 evaluation ID、执行状态、源码、配置、决策配置、规则、流程、schema、依赖锁、评分、模型公开配置、缓存策略与行为目录身份，以及对比器源码哈希。源码哈希变化可以来自报告代码，不能单凭它声称业务规则改变。
- 终态 `success`、`needs_review` 和保留完整所需产物的 `failed` 均可比较；仍在执行的产物或缺少必要文件的失败运行不可比较。

哈希用于发现意外修改，不提供签名认证；不会证明输入、参考标注或 manifest 本身来自独立第三方。

## 报告内容

| 层次 | 记录内容 |
|---|---|
| 固定样本 | Case/run 数、输入内容哈希、版本身份变化 |
| 判定可用性 | 两边有判定、新增判定、新增缺失、两边缺失 |
| 逐运行变化 | 任务正确性、出票事实、质量分，旧/新完整判定 |
| 根因变化 | 新增/删除的独立根因、首错位置变化、证据集合变化 |
| Agent A/B 结论 | 两次裁判给出的 direction 与 release_recommendation 变化 |
| 参考质量 | 两边质量报告与指标差值，风险漏判/误报/端到端漏检率及各自分母 |
| 正确性转移 | 任务、出票、完整根因标签多重集、完整标签+定位多重集、14 个标签及 4 个风险类别 |

同标签多个根因按实例匹配：先保留全部标签+首错完全一致的配对，再按事件顺序匹配剩余同标签实例。不会因标签去重掩盖根因数量变化。根因顺序、证据顺序、解释文字、内部因果组 ID 变化不计为业务结论变化；完整旧/新判定仍保留在 JSON 中。只改变扣分明细但不改变总分及上述维度，也不会增加 `changed_runs`。

`changed_runs / total_runs` 使用完整固定样本分母。两边都没有判定虽然不算新变化，但始终列在 `both_missing` 中；不能把“零变化”解释为评测完整。Agent A/B 结论另按 Case 统计，缺失成对结论与旧结论之间的变化也会显示。

## 正确性转移如何解读

| 转移 | 意义 |
|---|---|
| improvement / regression | 相对参考标注，错→对 / 对→错 |
| resolved_correct / resolved_incorrect | 未判定→正确 / 未判定→错误 |
| lost_correct / lost_incorrect | 正确→未判定 / 错误→未判定 |
| unchanged_correct / unchanged_incorrect | 两次均正确 / 两次均错误 |
| both_unknown | 两次都不能确定正确性，包括参考根因未知 |

缺失运行、`INSUFFICIENT_EVIDENCE`、空首错均按对应维度保留未知。空首错可能仍支持标签正确，但不能证明定位正确。显式已知的风险标签仍按已知值比较。完整根因多重集正确性与按类别是否出现分别统计，避免多实例数量变化被二分类掩盖。

同一运行可以同时出现修正与退步。`regressed_run_ids` 汇总出现 `regression` 或 `lost_correct` 的运行；`improved_run_ids` 汇总 `improvement` 或 `resolved_correct`，两者可能重叠。失去一个原本正确的判定应复核，但它与明确给出错误判定分开记录。分角色质量、逐运行角色、样本支持数、两边 Wilson 区间和原始分歧都在 JSON 中。

分数提高、根因减少、判定翻转本身不代表裁判改善。无参考标注时，报告只解释漂移；有参考标注时，结论也仅代表与给定参考的一致性。报告不更改业务评分和发布门控，不自动生成“新评测器可发布”的判定。命令成功写出诊断返回 0，即使存在回归；输入校验失败返回 2。

## 模型随机性与验收边界

所有指标差值为 `new - old`；漏判/误报通常越低越好，准确率通常越高越好。两端区间不是差值置信区间；相关轨迹与有限样本不支持自动显著性结论，零分母差值为 null。

需要观察模型随机性时，在各版本下用 `stability --fresh` 做重复运行，再结合固定样本对比复核。一次 old/new 执行不能分离代码、规则、提示词、模型及随机性的贡献，缓存回放也不能代表独立模型采样。开发集回归和定向构造测试均不替代 [独立人工验收](review.md)。
