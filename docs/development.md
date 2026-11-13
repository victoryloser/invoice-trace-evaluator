# 架构与接口

## 判定流水线

契约校验 → 时序状态重建 → 确定性业务检查 → 必要语义判断 → 根因归并与传播 → 扣分与 A/B 比较 → 导出和时间线。

用户目标随事件更新，行为按当时已知证据判断。取消、暂缓、正常终止和开票成功分别处理；工具成功不等于业务成功，缺证据时保留未知。

运行契约位于 src/invoice_eval/resources/，通过 setuptools package-data 随安装分发，不依赖 material/。该目录仅有程序配置、流程定义、规则说明和输出 schema。判定规则由 Python 实现，修改说明文档不会自动改变执行逻辑。轨迹、专家标签和档案文件保留在本地，排除 Git 与源码包。独立专家标签仅由 compare-labels 读取，evaluate 不读取该标签文件。

## CLI

```powershell
.venv/Scripts/python.exe -m invoice_eval --help
.venv/Scripts/python.exe -m invoice_eval report --run runs/local
.venv/Scripts/python.exe -m invoice_eval schema --output runs/input.schema.json
.venv/Scripts/python.exe -m invoice_eval validate --cases data/your-cases.jsonl --submission runs/local/submission.jsonl
.venv/Scripts/python.exe -m invoice_eval stability --cases data/your-cases.jsonl --output runs/stability --repeats 3
.venv/Scripts/python.exe -m invoice_eval adapt --input data/your-envelope.jsonl --format envelope --output runs/normalized.jsonl
```

当前输入契约见 [数据格式](data-format.md)，运输格式见 [轨迹适配](轨迹适配.md)，适配不补造业务事实或执行原工具。默认 CLI 配置从安装包读取；--config 可指定编辑过的完整配置。--env-file 指定环境文件，省略时读取当前工作目录的 .env。

## 结果契约

| 文件 | 内容 |
|---|---|
| manifest.json / cases.snapshot.jsonl | 输入、源码、配置、规则摘要及输入快照 |
| states.jsonl / checks.jsonl / findings.jsonl | 重建状态、检查与证据 |
| run_assessments.jsonl / assessments.jsonl | 运行结论及 A/B 比较 |
| judge_results.jsonl / judge_conflicts.json | 模型判断和冲突 |
| review_queue.json / release_evidence.json | 复核项与发布门控证据 |
| metrics.json / report.md | 指标及报告 |
| behavior_catalog.snapshot.json / behavior_coverage.json / behavior_coverage.md | 版本化业务行为清单、实际覆盖/通过率和用例事件映射 |
| submission.jsonl | schema 约定的完整批次输出 |

全量有效且导出校验通过后才生成 submission.jsonl。部分有效运行仍保存，不导出不完整批次。退出码 2 表示可报告的输入问题或待复核；程序错误返回非零并保留诊断。

业务结果已知但根因不可定位，可使用 INSUFFICIENT_EVIDENCE 及空首错；结果未知时不预填成功值。

`behavior.py` 在最终规则与语义判定之后重建行为触发与处理证据。行为覆盖和通过分别统计，清单分母固定，基线与候选分别汇总，待复核运行不丢弃。首批 12 项、计划标签及判定边界见 [业务行为覆盖](behavior-coverage.md)。本层仅产生诊断，不修改评分与发布门控。

## Judge 与缓存

live 输出校验标签、字段路径、时间界限和同 run 证据引用，结构修复最多一次。disabled/mock 不产生有效语义结论。缓存绑定模型、提示词、规则、源码及输入，仅保存有效 live 判断。

evaluate、stability、judge-audit 支持 --fresh 关闭缓存读写。双 Judge 配置须不同，比较全部独立根因、遗漏、额外根因及首错。

```powershell
.venv/Scripts/python.exe -m invoice_eval --env-file .env judge-audit --run runs/local --config configs/dual.json --output runs/audit-selected --case-ids INV-V01 INV-V08 --workers 3
.venv/Scripts/python.exe -m invoice_eval timeline --run runs/local --audit runs/audit/dual-judge.json --output runs/audit-timeline.html
```

scripts/replay_judges.py 可离线重验已记录的模型响应。独立回归及人工复核见 [验收与复核](review.md)。

## 无数据检查、打包与截图

`python -m pytest -q` 包含临时虚构轨迹的加载、格式适配、根因定位、待复核与 CLI 导出检查，不调用真实模型。历史 material/ 样例回归在材料缺失时跳过。原始本地资料保留，不为测试通过而提交它们。

`python scripts/verify_all.py --output runs/check` 不需要数据，使用测试辅助函数生成临时虚构输入；也可通过 --cases 指定自己的文件，按需加 --labels 检查专家一致性。输出目录必须是新目录。

源码打包与检查分别运行 `python scripts/build_release.py`、`python scripts/check_archive.py`。完整隔离验证运行 `python scripts/verify_release.py`：创建新环境、安装锁定依赖、构建并安装真实程序包，确认资源包含在安装包中，再运行无数据帮助、Schema 与离线测试。

README 截图运行 `python scripts/capture_readme.py`；可选安装 Playwright，Windows 使用 Edge，其他平台先安装 Chromium。脚本只生成临时虚构记录并评测，验证搜索、筛选、根因跳转及移动端无水平溢出后输出 docs/images/，不读取本地业务资料，不调用模型。

## 本地历史浏览器与录屏

以下为保留的历史开发脚本，除 verify_browser.py 读取既有 HTML 外，其余需要本地 material/ 或旧版演示产物，并非下载后即可运行的首启入口。可选安装 playwright==1.65.0 并执行 `python -m playwright install ffmpeg`，脚本使用本机 Edge：

```powershell
.venv/Scripts/python.exe scripts/verify_browser.py --html runs/local-timeline.html --output .build/browser
.venv/Scripts/python.exe scripts/verify_gap_browser.py --output .build/browser-boundaries
.venv/Scripts/python.exe scripts/record_demo.py --run-root runs/check --output .build/demo
```

HTML 自包含，无需应用服务。录屏输出 WebM，可用 FFmpeg 转换为 MP4；保存位置由 --output 指定。

## 工程约定

每次评测使用新目录；锁定依赖由开发者显式更新，验证不重写 requirements.lock.txt。评测目录保存该次输入、配置及证据，便于定位和复现。

新增规则覆盖时序、失败回调、重复请求及缺证据场景。接入业务系统须提供输入结构要求的两个版本轨迹及字段映射。

行为面板的可选浏览器检查：安装 Playwright 后，运行 `python scripts/verify_behavior_browser.py --html runs/coverage-check.html --output .build/behavior-browser`。脚本使用本机 Edge，检查证据链接、空搜索、重置和移动端宽度；输出目录须为新目录。

训练标签一致率、模型一致率、独立验收通过率分别统计。开发样例不充当独立留出集，未完成的人工复核保留待审。
