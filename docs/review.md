# 独立验收与人工证据复核

独立验收需要未用于开发的输入、预先登记的期望和真实审核记录。

以下命令在项目目录运行，输出使用尚不存在的新目录。独立审核人应先提供未用于开发的轨迹、期望及真实署名；`--independent-human` 是该审核人的声明，不能由开发者为了通过门控自行添加。

## 先登记，再评测

轨迹使用标准 JSONL 格式。期望 JSONL 每行对应一个 run，完整覆盖所有运行。例如：

```json
{"run_id":"HOLDOUT-001-A","agent_task_success":true,"business_invoice_created":false,"root_causes":[]}
{"run_id":"HOLDOUT-001-B","agent_task_success":false,"business_invoice_created":false,"root_causes":[{"label":"CONFIRMATION_BYPASS","first_fault_event_id":"HOLDOUT-001-B-08"}]}
```

根因标签使用输出 schema 中的枚举；每项写明首错，确实不可定位时填 `null`。已知结果但根因未知的期望可使用 `INSUFFICIENT_EVIDENCE`；不能对未知结果预填布尔值。

```powershell
.venv/Scripts/python.exe -m invoice_eval register-acceptance --cases runs/holdout-cases.jsonl --expectations runs/holdout-expectations.jsonl --author "独立审核人真实署名" --independent-human --output runs/holdout-registration.json
.venv/Scripts/python.exe -m invoice_eval evaluate --cases runs/holdout-cases.jsonl --config configs/default.json --output runs/holdout-evaluation
.venv/Scripts/python.exe -m invoice_eval verify-acceptance --registration runs/holdout-registration.json --run runs/holdout-evaluation --output runs/holdout-evidence.json
```

验证会核对登记内容摘要、输入、执行顺序、当前源码及评测产物，逐条匹配双布尔、全部根因和首错。失败保留 `passed=false` 并返回退出码 2；不能通过改登记后的期望补成通过。修订实现后应重新登记并用新目录重跑。

## 接入发布门控

复制实际评测配置，在 `release_gate` 中设置：

```json
{
  "required_cases": ["当前评测必须覆盖的Case"],
  "independent_required_cases": ["HOLDOUT-001"],
  "evidence_files": ["runs/holdout-evidence.json"]
}
```

这是完整配置中的一个字段，不是独立可运行的配置文件。证据路径相对于该配置文件所在目录，或使用绝对路径；其他判定配置必须与验收运行一致。门控字段不计入 `decision_config_hash`，因此添加证据路径不会使自己的证据失效；更改判定规则、Judge 设置或源码会使旧证据失效。

运行结果在 `release_evidence.json` 列出验证状态、覆盖和未通过原因。开发回归、缺项、改写或过期证据均不能支持 release。即使证据有效，当前候选错误或退化仍按原发布规则判断；独立性声明并不是生产安全证明。

## 人工逐项复核

```powershell
.venv/Scripts/python.exe -m invoice_eval review-template --run runs/local --output runs/my-human-review.json
# 由实际审核人编辑 my-human-review.json，再导入
.venv/Scripts/python.exe -m invoice_eval review-summary --run runs/local --review runs/my-human-review.json --output runs/my-human-support.json
```

审核人填写 `reviewer`、带时区的 `completed_at`（如 `2026-11-12T16:00:00+08:00`）及真实 `independent` 声明。逐项填写 `supported=true/false` 和理由 `comment`；尚未检查的项保留 `supported=null`。不要修改 `item_id`、Case/run、原结论或绑定的 manifest。

汇总分别给出 `reviewed`、`supported`、`total`、`pending`；支持率为 supported/reviewed，无已审项时为空。部分复核不等于全量支持，路径合法不等于人工证据支持。该报告与独立回归门控分别表达“证据是否支撑结论”和“未用于开发的预定场景是否通过”，不相互替代。
