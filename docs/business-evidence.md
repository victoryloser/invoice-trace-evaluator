# 出票证据来源与外部核验边界

本项目根据离线轨迹评估 Agent。`business_invoice_created` 表示“输入记录是否支持出票”，并不表示评测器已经查询开票服务、税务平台或业务账本。报告统一使用“轨迹记录出票”表述，并单独显示外部核验状态。

## 三个不同问题

| 问题 | 当前依据 | 不能据此证明什么 |
|---|---|---|
| Agent 是否正确处理任务 | 当时用户目标、输入上下文、事件与业务规则，必要时使用语义 Judge | 外部业务系统当前是否存在发票 |
| 轨迹是否记录出票 | 输入运行摘要与明确回调记录 | 记录真实、完整或来自独立来源 |
| 外部业务状态是否被独立核验 | 需要另行查询权威服务并关联任务或发票标识 | 当前程序没有此连接，不能声称已核验 |

正确等待确认可以是处理正确、记录未出票；未经确认提交后收到成功回调可以是处理错误、记录出票。两个维度独立，外部核验又是第三个维度。

## 自动生成的产物

执行原有 `evaluate` 命令即可，无需新参数：

```powershell
python -m invoice_eval evaluate --cases data/your-cases.jsonl --output runs/local
python -m invoice_eval timeline --run runs/local --output runs/local.html
```

- `business_evidence.json`：全部输入运行的来源明细、字段路径、记录时间、记录一致性、未用于出票判定的信号，以及按 Agent 角色统计的汇总。
- `business_evidence.md`：可读的来源表与解释边界，主报告链接到此文件。
- `run_assessments.jsonl`、`assessments.jsonl`、`submission.jsonl`：有效判定增加 `business_invoice_evidence`，让离开报告页面的结果也保留来源说明。
- `manifest.json`：声明 `offline_trace` 范围与 `not_performed` 外部核验状态，并记录证据文件哈希。
- `metrics.json`：保留原有 `service_created` 字段并补充范围；另增 `business_evidence`，以全部输入运行作为证据统计分母。
- HTML 时间线：显示全局核验边界，每条运行可展开“出票来源与核验边界”，点击回调来源的事件编号跳转到原始事件。

`business_invoice_created` 继续使用布尔值，保持既有提交契约；无法判断时不生成该运行的有效提交判定，但证据报告保留对应的 `invoice_created: null`。旧产物没有来源字段时仍可阅读，会提示历史产物未保存逐条说明；不会补造核验记录。

## 来源和推导规则

| 来源类型 | 字段 | 用于出票判断 |
|---|---|---|
| `run_summary` | `run.invoice_created` | 非 null 的布尔值 |
| `callback_output` | `event.observed_output.callback_status` | `success` → true；`failed` → false |
| `callback_tool_result` | `invoice.callback` 的 `result.status` | `success` → true；`failed` → false |

每条来源包含相对 run 的 JSON Pointer、事件 ID、输入记录的事件结束时间、原始值和解读值。事件数组即使乱序，路径也指向原数组中的正确位置。运行摘要没有事件时间，不用评测器执行时间代填。

- `consistent`：存在来源且所有解读值一致，输出对应布尔值。
- `missing`：没有可用来源，输出 null。
- `conflicting`：出现相反值，输出 null，并进入原有复核流程。

不做多数表决：两条成功记录与一条失败记录仍是冲突。多条记录也可能来自同一链路，不能因此提高“独立可信度”。当前规则不推断历史失败后重试成功的最终状态；这类多阶段回调需要明确任务/发票关联和状态语义，否则保留冲突。

以下信号不能单独补全出票结果：Agent 的 `observed_output.invoice_created` 自述、`invoice.submit` 的受理结果、成功步骤、`E200`、`final_state`、参考期望、专家标签或 Judge 的回答。证据报告另外列出出票自述和提交受理记录，并说明未采用的原因。

来源提取与业务判定共享同一个函数，避免报告列出的来源和实际推导规则不一致。`validate` 对包含新来源字段的提交重新从原始轨迹生成来源说明，拒绝篡改的字段路径、虚构的外部核验状态及与来源不一致的出票布尔值。旧提交未提供此可选字段时仍兼容，不能因此获得“已核验”的标记。

## 外部核验状态

当前每条运行的外部核验记录为：

```json
{"status": "not_performed", "invoice_created": null}
```

`external_verified_runs` 为 0，表示本评测器没有完成独立外部核验；`externally_verified_invoice_created` 为 null，表示外部真实出票数未知，不能把它填写为零。用户在输入的额外字段中声明 `verified` 不会改变此状态。

参考标注一致性、独立人工验收、双 Judge 复核和产物哈希检查各有用途，但都不自动构成业务系统核验。哈希能绑定记录版本，不能证明记录内容真实。当前功能补齐的是证据边界和来源追溯，不是在线核验接口。

未来接入外部核验时，需要明确：权威来源与授权查询方式、任务/发票标识关联、查询发生时间与要核验的业务时间、原始响应或可追溯引用、查无结果的完整性边界，以及回调与查询冲突的处理规则。不能把一个自填的成功布尔值当作独立核验。
