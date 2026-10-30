# 报告结构与评测契约

## HTML 报告结构（R1-R9：三层 + 结论先行 + 自包含）

代码分层：

- `evaluator/report_payload.py`：payload 组装（纯数据，`build_report_payload` / `build_first_screen_payload`）
- `evaluator/report_template.py`：模板层（全部 `_render_*` / `_artifact_*` / 内联 CSS+JS）
- `evaluator/reporting.py`：渲染入口（`render_report_html` / `generate_run_report` / `generate_anonymous_report`）

首屏（手机竖屏不滚到第二屏），顺序固定：

1. **一句话结论（verdict 大字）**：明确有效 / 明确有害 / 没法下结论 + 最多两句补充（`build_plain_summary`）
2. **三个关键指标**：结论分布（有效/有害/没结论）、评委总票（带 X : 不带 Y）、平均差值（对比了几组 + 基础设施故障次数）
3. **红绿用例清单**：逐条自动检查，绿色 ✓ 通过 / 红色 ✗ 失败，每条一句大白话原因
4. **带 Skill vs 不带 Skill 对比卡片**：每个模型一张（票数 + 人话结论 + 主交付物数）
5. **负迁移警报**（如有）：排在对比卡片之前，标注「建议在该模型上关闭」
6. **宿主适用边界**：降为结论区下方一行小字

折叠区（`<details>`）：

- 按模型看完整结果（评委评价、交付物对照、诊断）
- 配对明细与统计口径（逐轮差值、评委一致比例、人工复核记录、对比组数）
- 附录（自动检查规则存档 / 证据链）

**术语大白话**：非折叠区禁止出现 `Δ σ² N= rep With/Without 断言 置信区间 宿主`
（`plain_language.JARGON_TERMS` + `terminology.TERMINOLOGY_MAP` 覆盖，折叠层保留原文兜底）。

**自包含加固**：

- 内联预算 `INLINE_BUDGET_BYTES = 20MB`：主交付物 base64 内联；超预算改为纯文本/SVG 摘要占位（文件名、大小、前几行、本机 run 路径），不再无条件内联。
- 非主交付物支持 base64（小文件）与相对路径降级。
- 自检用例：`check_report_self_contained()` 断言无 `<link>`、无 `http(s)://` 外链、CSS/JS 内联，由 `tests/test_report_self_contained.py` 回归。

## Evaluation Contract

- **实验矩阵**：宿主 × 每个模型 × 每个 skill 模式（with/without）× repetitions 次重复，独立执行
- **配对口径**：同模型同重复轮次的 with/without 一一配对，主指标为配对差值 Δ
- **三结论判定**：`skill_effective`（有效）/ `negative_migration`（负迁移，建议关闭）/ `inconclusive`（证据不足，不强判）——纯确定性函数，零 LLM
- **断言优先**：四类确定性断言（exact_match / contains_facts / recomputable / script_eval）先于盲评；判定器版本固定 + 快照可复验
- **盲评模式**：批量投票，评委匿名评审、只投 `validated_result_files`
- **宿主能力预检**：每个宿主显式声明能力，不支持的能力显式报错，绝不静默降级
- **Subject Adapter 契约**：任务投递 + 按宿主规则注入 skill + 产物收集（artifacts/）+ 加载验证（防假对照）
- **对照基线**：`without skill` 只约束目标、输入和交付，不约束内部步骤
- **合成数据标记**：使用 AI 生成的测试数据会在报告中标记为 synthetic/mock

## 评测原则

- **配对优先**：核心问题是同一模型在同需求/同输入/同条件下，with skill 与 without skill 的配对差值 Δ；跨模型对比降为第二层参考。
- **断言判定优先，盲评辅助，人审按需兜底**：能客观判定的任务走四类确定性断言（零 LLM）；主观维度走盲评；断言无法判定、评委分歧大或高风险任务触发人审记录。
- **证据不足不强判**：重复次数不足或置信区间跨 0 时，结论一律 `inconclusive`；`repetitions` 默认 3（<3 显式警告样本不足）。
- **评委只看结果文件**：评委永远只评价本次任务实际交付的 `validated_result_files`；过程文件不进入评分。
- **`without skill` 是自由基线且严格隔离**：不注入 skill、不泄露 skill 内容、提示词严格对称；加载证据证明 with 模式确实加载了 skill，否则该对作废。
- **结论仅适用于已测宿主**：不同宿主的 skill 注入约定与执行行为差异显著，报告显式标注已测宿主清单并禁止跨宿主外推。
- **合成数据标记**：使用 AI 生成的测试数据在报告中标记为 synthetic/mock。
