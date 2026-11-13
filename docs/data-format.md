# 当前支持的数据格式 / Supported trace format

当前版本评测开票业务的离线轨迹。输入为 UTF-8（可带 BOM）JSONL，每行一个完整 JSON 对象；不能使用整体 JSON 数组、空行、NaN 或 Infinity。仓库不发布轨迹、参考标签或客户商品档案。

The evaluator accepts UTF-8 JSONL, optionally with a BOM, with one complete object per line. JSON arrays, blank lines and nonfinite numbers are rejected. Traces, reference labels and customer/product records are not distributed.

## 获取完整输入契约 / Export the complete input contract

```bash
python -m invoice_eval schema --output runs/input.schema.json
```

该命令不需要数据或模型。生成的 Schema 描述全部字段、枚举与必填要求，运行时还会检查唯一性、事件归属和时间范围。下表列出主要字段；额外字段会保留，但不会自动获得新的业务判定逻辑。

This needs no data or model. The generated JSON Schema specifies fields, enums and required values. Runtime validation additionally checks identity, ownership and time ordering. Extra fields are preserved but do not add evaluation rules automatically.

## 每行一个任务 / One case per line

| 字段 / Field | 类型与含义 / Type and meaning |
|---|---|
| `case_id` | Required nonempty string, unique in the file |
| `user_request` | Required string; actual user request, currently interpreted by Chinese invoice rules |
| `context` | Required object; known business facts and candidate records |
| `reference_result` | Required object; task-specific expected terminal constraints |
| `runs` | Exactly two runs: baseline first, candidate second |
| `scenario` | Optional string for display and grouping |
| `expected_behaviors` | Optional array of unique behavior IDs describing planned coverage; declarations are not execution evidence. Export IDs with `behavior-catalog`; see [behavior coverage](behavior-coverage.md). |

当前不是通用任意 Agent 评测器。用户请求中的金额口径、取消和修订使用中文规则解析；复杂表述需要显式业务字段或可选语义模型。reference_result 是业务约束，不是人工根因标签；evaluate 不读取单独的专家标签文件。

This is not a generic agent evaluator. Request parsing uses Chinese invoice patterns for amount basis, cancellation and revisions. Complex language needs explicit business fields or optional semantic judgment. `reference_result` supplies terminal business constraints, not expert root-cause labels. `evaluate` does not read separate expert-label files.

## 运行记录 / Runs

| 字段 / Field | 要求 / Requirement |
|---|---|
| `run_id` | Nonempty string; unique across the whole input file |
| `agent_version` | Required string; displayed version name |
| `task_id`, `trace_id` | Required strings identifying this run's task and trace |
| `events` | At least one event |
| `final_state` | Required string; observed end state, compared with task constraints |
| `invoice_created` | Optional boolean/null; input-reported invoice outcome, not independently verified service state |
| `resource_usage` | Optional object; input-reported usage, not verified production costs |

## 事件 / Events

| 字段 / Field | 要求 / Requirement |
|---|---|
| `event_id` | Nonempty string; unique across the whole file |
| `seq` | Integer ≥ 1, unique within a run; analysis sorts by this field |
| `step_id`, `module`, `action` | Required strings describing the recorded business step |
| `task_id`, `trace_id` | Must match the containing run |
| `step_status` | `PENDING`, `RUNNING`, `WAITING_USER`, `WAITING_CALLBACK`, `SUCCEEDED`, `FAILED`, `SKIPPED`, `CANCELED`, `TIMED_OUT` |
| `started_at`, `finished_at` | ISO 8601 strings with timezone; finish must be at/after start |
| `duration_ms`, `retry_count` | Integers ≥ 0 |
| `result_code` | `E100`, `E200`, `E400`, `E500`, `E600`, `E700`, `E800` |
| `error_code`, `error_message` | Required fields; string or null |
| `observed_input`, `observed_output` | Required objects carrying actual evidence |
| `tool_calls` | Required array; use `[]` when no call was recorded |

每个工具记录包含 `name`（string）、`arguments`（object）、`result`（object），以及可选 `attempt`（integer ≥1，默认1）。这些是已发生的调用记录，评测器不会执行工具。

Each recorded tool call has `name` (string), `arguments` (object), `result` (object), and optional `attempt` (integer ≥1, default 1). The evaluator never executes recorded calls.

出票来源及外部核验边界见 [业务证据说明](business-evidence.md)。`business_invoice_created` 表示记录支持的出票判断；运行摘要和回调都属于输入证据。`evaluate` 自动导出 `business_evidence.json/.md`，缺失或冲突保留未知，独立外部核验标记为 `not_performed`。输入中的额外“已核验”声明不会被当作程序已完成外部查询。

常用业务步骤包括 `PARSE_INTENT`、`COLLECT_INVOICE_DATA`、`VALIDATE_DATA`、`CONFIRM_INVOICE`、`SUBMIT_INVOICE`、`WAIT_INVOICE_CALLBACK` 和终态步骤。完整流程定义见 [workflow.json](../src/invoice_eval/resources/workflow.json)。结果码与步骤成功不能单独证明实际出票。

Common steps include intent parsing, collection, validation, confirmation, submission, callback and termination. See [workflow.json](../src/invoice_eval/resources/workflow.json). A step status or result code alone does not establish invoice creation.

## 当前业务证据 / Current business evidence

| 位置 / Location | 支持的证据 / Supported evidence |
|---|---|
| `context.buyer_candidates` | Candidate objects with `buyer_id`; multiple candidates require disambiguation |
| `context.item_candidates` | Objects with `item_id`, `display_name`, `configured_rate`; rates are supplied by the input |
| `context.user_confirmation_recorded` | Boolean recording prior confirmation of the current task |
| `context.required_missing_fields`, `amount_basis_unspecified`, `buyer_disambiguation_required` | Explicit missing-field and ambiguity conditions |
| `context.risk_flags` | Known risks such as `DUPLICATE_INVOICE` |
| `context.conversation_rounds`, `line_items`, `original_invoice_id`, `original_invoice_record`, `existing_draft` | Supported history, batch and original-invoice information; see [methodology](方法说明.md) |
| `reference_result` | `acceptable_snapshot_state`, `required_action`, `invoice_should_exist` and optional terminal field/amount constraints |
| `observed_output` | Actual fields, `preview`, `computed_amounts`/`amounts`, `requested_fields`, `risk_flags`, `callback_status` and recorded feedback |
| `observed_input.user_update`, `user_confirmation` | Explicit revisions and confirmation that take effect at that event |

金额口径为 `tax_exclusive` 或 `tax_inclusive`；金额计算使用配置税率、Decimal 和两位小数四舍五入。金额结果对象包含 `exclusive_amount`、`tax_amount`、`inclusive_amount`。结构化字段必须来自实际证据，不可为通过检查补造确认、回调或幂等授权。缺少关键事实会进入复核，而不是自动判成功。

Amount basis is `tax_exclusive` or `tax_inclusive`. Calculations use input-configured rates, Decimal and half-up rounding to cents. Result objects contain `exclusive_amount`, `tax_amount`, `inclusive_amount`. Supply only actual evidence; never invent confirmations, callbacks or retry authorization. Missing critical facts require review.

## 运输格式 / Transport formats

- `canonical`：上述逐任务 JSONL / the canonical case structure above.
- `envelope`：每行包含 `task`（不含 runs 的任务字段）和 `trajectories`（两个 runs）/ task fields plus a two-run trajectories array.
- `otel`：JSONL 包装的显式发票 OTLP 导出，包含 `case`、`run_metadata`、`resourceSpans`，span 属性须有 `invoice.run_id` 与完整的 `invoice.event_json` / explicit invoice-aware OTLP exports with full business events.

详见 [轨迹适配](轨迹适配.md)。普通 OpenTelemetry span、CSV、Excel 或任意 JSON 日志不能直接评测；适配器不会补造业务事实。

See [trace adapters](轨迹适配.md). Generic telemetry, CSV, Excel and arbitrary JSON logs cannot be evaluated directly. Adapters do not synthesize missing facts.

## 输出 / Output

完整有效批次输出 `submission.jsonl`，每行包含 `case_id`、两条 `run_assessments`、`version_comparison`，字段由 [output.schema.json](../src/invoice_eval/resources/output.schema.json) 约束。缺少关键结论时保留复核队列及已知结果，不导出不完整批次。评测退出码 0 表示成功，2 表示输入问题或待复核；其他未预期错误也返回非零。

Complete valid batches produce `submission.jsonl`, constrained by [output.schema.json](../src/invoice_eval/resources/output.schema.json). Unresolved decisions retain review items and valid partial assessments without publishing an incomplete batch. Exit code 0 means success; 2 indicates input issues or review; unexpected failures are also nonzero.
