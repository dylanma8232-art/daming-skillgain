---
name: daming-skillgain
description: 用可复现的对照实验验证某个 Skill 是否真的有效——同模型 with/without 配对执行、确定性断言判定优先、评委盲评辅助，产出结果导向的静态 HTML 报告。当用户要判断 skill 有没有用、是否值得发布、换模型后是否失效、改版后变好还是变坏，或需要评测/回归某个 skill 时使用。
license: GPL-3.0
metadata:
  audience: developers
  workflow: evaluation
  version: "2.0.0"
  main_metric: Skill Gain Δ (paired, same-model)
  compatibility: 跨宿主（经 Subject Adapter 统一契约；报告标注本次已测宿主，结论仅适用于已测宿主）
---

# daming-skillgain

Skill 发布前的质检门禁：把 skill 放进真实任务，与不启用时的产出对照，再决定发布它、或在哪些模型上开启/关闭。

## 何时用我

- 判断某个 Skill 是否真的有效（而不是「看起来有效」）
- 找出 skill 在哪些模型上有正增益、哪些出现负迁移（应关闭）
- 在多个宿主上分别验证 skill 效果（结论不跨宿主外推）
- 对 skill 做版本回归：改版前后各跑一次同口径评测
- 发布 skill 前，需要一份可信、可复核的评测报告

## 最短路径（推荐）

```bash
pip install -r requirements.txt
python3 -m evaluator.cli simple
```

问答式引导，只答 4 个问题自动跑完评测，完成后打开生成的 `report.html`。

高级分步（`plan` → `run` → `evaluate` → `report`、参数细节、准备阶段硬规则）见 [references/workflow.md](references/workflow.md)。

## 关键约束（必须遵守）

- **评测需求必问**：先问用户「本次评测要让被测 skill 执行什么具体需求」。只有用户明确说「你根据 skill 自己生成需求」时才可省略 `--task-prompt`。
- **敏感资源不能生成**：账号、密码、token、API key、数据库连接、浏览器测试环境等只能由用户提供，不能由 AI 生成。
- **`needs_user_input` 必须停止**：计划生成返回 `needs_user_input` 时，把 `questions` 展示给用户并等待回答，不要继续 run。
- **证据不足不强判**：重复次数不足或置信区间跨 0 时，结论一律 `inconclusive`；`repetitions` 默认 3。
- **结论仅适用于已测宿主**：报告显式标注已测宿主清单，禁止跨宿主外推。

## 参考文件（按需读取，不要一次全读）

- [references/workflow.md](references/workflow.md) — 分步命令、参数、准备阶段硬规则、盲评与总结流程
- [references/usage.md](references/usage.md) — Python SDK / REST API / MCP Server 接入、宿主能力边界、执行器类型
- [references/hosts.md](references/hosts.md) — 接入被测 AI 工具的三种方式、工具矩阵、脚本契约模板
- [references/resources.md](references/resources.md) — 资源识别规则与预校验
- [references/report-and-contract.md](references/report-and-contract.md) — HTML 报告结构、评测契约与评测原则

## 前置条件

- Python 3.10+（推荐 3.12+）
- 至少一个能被程序唤起干活的 AI 工具：原生适配器宿主（如 OpenCode，开箱即用）、任何工具经脚本契约包装（见 hosts.md），或纯 API 宿主（`api`/`standalone` 需 provider key，不具备文件操作能力）
- 被测工具本身不需要装额外东西——评测器的 CLI 参数是评测器自己的入口
