# 业务行为覆盖 / Business behavior coverage

`evaluate` 自动生成行为清单快照、覆盖明细和 Markdown 报告，`timeline` 显示基线/候选覆盖表及当前任务的事件链接。默认离线运行，不增加模型请求。

## 分母与适用范围

首批清单位于 `src/invoice_eval/resources/behaviors.json`，版本为 `1.0`，固定包含 12 项：

| ID | 行为 |
|---|---|
| confirmation_gate | 提交前取得有效确认 |
| reconfirm_after_update | 已确认内容修改后重新确认 |
| cancel_stop | 取消后停止提交 |
| hold_stop | 暂缓后停止提交 |
| duplicate_block | 阻断已知重复开票风险 |
| unknown_submit_recovery | 受理未知后的受控处理 |
| failed_callback_reporting | 失败回调后如实反馈 |
| buyer_clarification | 购方歧义时澄清 |
| missing_field_clarification | 必填字段缺失时补充 |
| amount_basis_clarification | 金额口径不明时澄清 |
| red_reference_required | 红票缺少原票号时补充 |
| batch_integrity | 批量明细完整保留 |

每项包含触发条件、预期行为、关联规则及源码依据。这是项目内首批需要测试的行为清单，不宣称覆盖全部税务需求、业务行为或代码分支。业务负责人应审阅其范围；不能为了提高比例删掉未覆盖项。扩充或调整语义时，同时修改识别代码、测试和清单版本。修改 JSON 的说明文字不会自动产生新的检测能力。

```powershell
.venv/Scripts/python.exe -m invoice_eval behavior-catalog --output runs/behavior-catalog.json
```

## 计划、触发、覆盖和通过

输入 Case 可以添加可选字段：

```json
"expected_behaviors": ["reconfirm_after_update", "cancel_stop"]
```

这是用例设计者的计划，只用于发现“计划测到但实际没有测到”的缺口。未知或重复 ID 会在输入校验时报错。没有这个字段的旧数据仍可自动识别行为。Case 名称、scenario、计划标签和参考终态不能代替真实触发条件。

系统按事件 `seq` 重建目标和确认状态，分别记录：

1. **触发**：上下文或事件出现对应条件，如用户明确取消、提交返回 timeout。
2. **覆盖**：还必须观察到对应处理，例如取消/继续提交、等待回调/重试。只有 timeout 事件、没有后续动作时，属于已触发但未覆盖。
3. **判定**：已覆盖后，依据明确行为证据及对应规则/语义结论判为 pass、fail 或 unknown。未知不自动通过，也不自动描述为已确认违规。

`not_covered` 表示没有可观察的对应处理；具体是未触发还是触发后轨迹截断，可以从 `triggered` 和 `occurrences` 区分。

每个实例记录触发字段路径、触发事件、处理事件、判定说明和关联 Finding ID。重复修改、重试等实例独立保留，不会因为第一次通过就隐藏后续未知或失败。行为汇总中失败优先；否则存在未完成实例/未知结论则保留 unknown；全部已触发实例通过才计为通过。

例如：用户取消后仍提交，这项行为**已覆盖、但失败**。只写 scenario="取消"，实际请求和轨迹没有取消，则**未覆盖**。

## 指标口径

| 字段 | 分子 / 分母 |
|---|---|
| coverage | 至少有一次真实处理证据的不同业务行为数 / 清单行为总数 |
| behavior_pass_rate | 全部已触发实例均通过的行为数 / 已覆盖行为数 |
| run_behavior_pass_rate | 判为通过的运行-行为组合数 / 已覆盖的运行-行为组合数 |

失败和已覆盖但证据不足的记录留在通过率分母里。无已覆盖行为时，通过率为 null，而非 100%。增加相同路径的样本不会增加行为覆盖率的分子；它会影响运行-行为通过率的样本数。

分别提供合并、baseline、candidate 三种范围。角色依据输入 runs 的顺序，而非版本名称。两个版本可能覆盖不同分支，合并比例不能代替单版本比例，也不能仅凭覆盖较高断言版本较好。

原有 `metrics.coverage` 是**有效评测运行数 / 输入运行数**；新指标在 `metrics.behavior_coverage` 下，避免混淆评测有效率与业务行为覆盖率。

## 产物与查看方式

- `behavior_catalog.snapshot.json`：本次清单，版本与哈希写入 manifest。
- `behavior_coverage.json`：汇总、各版本结果、未覆盖清单、计划缺口及所有运行的证据映射；待复核运行也保留。
- `behavior_coverage.md`：可读清单和 Case/run/事件映射。
- `report.md`：覆盖及通过率概览。
- HTML 时间线：全局对比表、当前任务的行为实例及可点击事件证据。

```powershell
.venv/Scripts/python.exe -m invoice_eval evaluate --cases data/your-cases.jsonl --output runs/coverage-check
.venv/Scripts/python.exe -m invoice_eval timeline --run runs/coverage-check --output runs/coverage-check.html
```

旧运行目录没有覆盖产物时，report/timeline 仍能读取；需重新 evaluate 才有新覆盖数据。重新渲染报告读取本次保存的清单与结果，不用当前清单覆盖历史口径。

## 判定边界

本版覆盖结果用于诊断测试充分性，不改变既有质量扣分、submission 输出契约和发布门控。行为通过仅表示该行为的证据支持通过，不表示整个任务通过；行为未知也可能与已确定的任务结论并存。

明确取消/暂缓由现有结构化状态与中文解析器识别；仅靠模型猜测某条请求属于哪个行为，不计覆盖。等待、停止及失败反馈的部分通过判据要求明确、无冲突的未出票事实；缺少这些证据时保留 unknown。

批量明细沿用当前支持范围，比较商品 ID、各行不含税金额及重复次数，不能扩展解释为对任意明细字段的完整验证。离线轨迹中的证据仍由提供方采集，本功能不会查询真实发票系统或重新执行原工具。
