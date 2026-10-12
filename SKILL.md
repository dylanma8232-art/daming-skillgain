---
name: daming-skillgain
description: 用可复现的对照实验验证你的 Agent Skill 是否真的有效——同模型 with/without 配对执行、确定性断言判定优先、评委盲评辅助、跨宿主（OpenCode / Claude Code / Codex / script 等可驱动宿主）与版本回归支持，产出结果导向的静态 HTML 报告。内置资源预校验（preflight validator）与模型连通性测试，阻止不完整配置进入评测。
license: GPL-3.0
compatibility: 跨宿主（经 Subject Adapter 统一契约；报告标注本次已测宿主，结论仅适用于已测宿主）
metadata:
  audience: developers
  workflow: evaluation
  version: "7.0.1"
  main_metric: Skill Gain Δ (paired, same-model)
---

## What I do

- **分析被评测 skill**：读取 `SKILL.md`，自动识别 skill 类型（代码生成/文件处理/交互操作/数据查询等）、所需资源、核心能力
- **生成评测计划**：构建实验矩阵（宿主 × 模型 × skill 模式 × 重复轮次），并要求同时配置参与盲评的评委模型
- **模型连通性测试**：在生成计划前对所有选中模型经统一宿主调度层执行连通性测试，确保可正常调用
- **资源预校验（Preflight Validator）**：在生成评测计划前，对文件、环境命令、浏览器端口、数据库连接、API endpoint、敏感凭证等进行主动校验，阻止不完整配置进入评测
- **按授权生成测试数据**：当用户明确选择生成测试数据时，强制产出真实可用的数据文件（如 CSV、JSON、SQL 等），而非仅任务描述
- **短执行时间检测**：自动识别执行时间极短（<10s）且无产物的实验，标记为可疑
- **多宿主执行**：被测 skill 可运行于任意已接入宿主（OpenCode / Claude Code / Codex / script 契约宿主等），经 Subject Adapter 统一契约完成「任务投递 + 按宿主规则注入 skill + 产物收集 + 加载验证」四件事；每个宿主显式声明能力，不支持的能力显式报错而非静默降级
- **同模型配对增益**：主指标为各模型 Skill Gain Δ（with/without 配对差值，均值 ± 方差 + 95% 置信区间 + 样本量）；确定性三结论判定 `skill_effective` / `negative_migration` / `inconclusive`（证据不足一律不强判）；负迁移检出并显著标注「建议在该模型上关闭此 skill」
- **确定性断言判定优先**：四类零 LLM 断言（精确匹配 / 包含关键事实 / 可重算 / 脚本判定）先于盲评；判定器版本固定 + 快照落盘可复验（同输入同判定）
- **五项实验纪律**：空产物计失败、基础设施异常（invalid）重试且不计入分母、without-skill 严格隔离（防提示词泄漏）、加载证据（防假对照）、不确定性报告（小样本显式告警）
- **评委盲评（Blind Vote，辅助证据）**：多个独立评委模型对匿名方案按「需求满足度 + 结果文件质量」批量投票；评委只评价 `validated_result_files`；评委一致率与人审按需触发记录落盘
- **空产物自动跳过**：盲评阶段自动过滤掉无产出物的实验，避免浪费评委模型调用
- **结果文件定位**：先根据用户需求和被测 skill 输出契约定位最终结果文件，再让评委只评价这些文件；`summary.md`、`execution_trace.md`、日志、脚本和中间素材只作为过程证据
- **生成结果导向结论报告**：产出静态 HTML 报告——首屏依次为「⚠️ 宿主适用边界（结论仅适用于已测宿主，禁止跨宿主外推）→ ① 总结 → ② 负迁移警报（检出时才展示）→ ③ 按模型看结果」，每个模型一个完整区块（结论、评委打分与评价、带/不带 skill 主交付物并排对照、诊断、技术细节折叠），无重复产物展示层

## When to use me

- 想用可复现的对照实验验证某个 Agent Skill 是否真的有效（而不是「看起来有效」）
- 想知道 skill 在哪些模型上有正增益、在哪些模型上出现负迁移（应关闭）
- 想在多个 Agent 宿主上分别验证 skill 效果（结论不跨宿主外推）
- 需要对 skill 做版本回归：改版前后各跑一次同口径评测，比较 Skill Gain Δ
- 准备发布 skill 前，需要一份可信、可复核的评测报告

## 评测原则

- **配对优先**：核心问题是同一个模型在同需求/同输入/同条件下，with skill 与 without skill 的配对差值 Δ；跨模型对比降为第二层参考。
- **断言判定优先，盲评辅助，人审按需兜底**：能客观判定的任务走四类确定性断言（零 LLM）；主观维度走盲评；断言无法判定、评委分歧大或高风险任务触发人审记录。
- **证据不足不强判**：重复次数不足或置信区间跨 0 时，结论一律为 `inconclusive`；`repetitions` 默认 3（<3 显式警告样本不足）。
- **评委只看结果文件**：评委永远只评价本次任务实际交付的 `validated_result_files` 内容；过程文件不进入评分。
- **`without skill` 是自由基线且严格隔离**：不注入 skill、不泄露 skill 内容、提示词严格对称；加载证据证明 with 模式确实加载了 skill，否则该对作废。
- **结论仅适用于已测宿主**：不同 Agent 宿主的 skill 注入约定与执行行为差异显著，报告显式标注已测宿主清单并禁止跨宿主外推。
- **合成数据标记**：使用 AI 生成的测试数据在报告中标记为 synthetic/mock。

## Workflow

### 步骤 0：检查更新（轻量，不阻塞）

```bash
python3 -m evaluator.cli update-check
```

每次被调用时 CLI 启动会自动后台检查 GitHub 是否有新版本（每 24h 最多一次，网络失败/超时 3s 静默跳过，绝不阻塞主流程）；有新版会打一行白话提示。也可手动运行上面的子命令强制检查。离线环境可设 `DAMING_SKILLGAIN_SKIP_UPDATE_CHECK=1` 关闭。

### 步骤 1：初始化配置收集

```
python3 -m evaluator.cli plan --init --skill-path <被评测skill目录>
```

系统会输出需要收集的字段列表，包括：
- 被评测 skill 的能力分析和所需资源
- 可用模型列表
- 评测步骤指引

### 准备阶段硬规则

在调用 `plan --generate` 前，必须先完成以下收集：

- **评测需求必问**：必须询问用户"本次评测要让被测 skill 执行什么具体需求"。只有用户明确说"你根据 skill 自己生成需求"时，才允许省略 `--task-prompt` 并添加 `--allow-generated-task-prompt`。
- **文件数据必问**：如果 `plan --init` 或 skill 分析结果显示需要 `file` 资源，必须先问用户是否有测试文件。用户提供文件时传 `--input-files` 或资源 `value`；用户没有且明确允许生成时，才在对应资源 JSON 中传 `allow_auto_generation: true`。
- **敏感资源不能生成**：账号、密码、token、API key、数据库连接、浏览器测试环境等只能由用户提供测试信息，不能由 AI 生成。
- **遇到 `needs_user_input` 必须停止**：如果 `plan --generate` 返回 `error=needs_user_input`，必须把返回的 `questions` 展示给用户并等待回答，不要继续执行 run，也不要默认生成数据。

### 步骤 2：生成评测计划

```
python3 -m evaluator.cli plan --generate \
  --skill-path <被评测skill目录> \
  --skill-name <skill名称> \
  --models <模型1,模型2> \
  --task-prompt <测试任务描述> \
  --input-files <输入文件路径（可选）> \
  --resources <资源JSON（可选）> \
  --voting-models <评审模型1,评审模型2> \
  --host <宿主：opencode / claude-code / codex / script / api / standalone / agent，默认 opencode> \
  --repetitions 3 \
  --execution-mode cli-attach
```

`--voting-models` 为必填，因为盲评是必选流程。`--task-prompt` 默认必填；只有用户明确授权时才可加 `--allow-generated-task-prompt`。

**宿主选择（--host）**：实验执行、盲评评委与总结模型都经统一宿主调度层分发到所选宿主。各宿主的能力声明不同（如 `api`/`standalone` 为纯 API 宿主，不具备文件操作能力，跑产物型任务会被能力预检显式拦截）。未安装对应 CLI 的宿主（如 claude-code / codex）会在连通性/配置校验阶段显式报错，不会静默降级。

**模型连通性测试**：此阶段自动对所有选中模型（参与模型 + 评委模型 + 总结模型）经所选宿主执行连通性测试。默认 `--execution-mode cli-attach`（宿主域内 attach 模式）；如需强制本地子进程可改 `--execution-mode cli`，如需宿主 HTTP API 可改 `--execution-mode api`。测试失败会阻止生成评测计划；如需跳过，可添加 `--skip-connectivity-test`（不推荐）。

**Preflight 校验**：此阶段会自动执行资源预校验，包括：
- `file`：验证文件是否存在、可读、非空
- `environment`：探测环境命令是否存在及版本
- `browser`：检测浏览器命令和远程调试端口
- `database`：验证数据库连接结构，确认测试环境标识
- `api_key`：验证 API endpoint 结构，检测 sandbox/test 标识
- `credential/account`：脱敏校验，检测 test 标识

如果存在 blocking 级别的问题，会阻止生成评测计划。

### 步骤 3：执行评测实验

```
python3 -m evaluator.cli run \
  --plan-dir <plan生成的run目录> \
  --parallelism 5 \
  --review-parallelism 4 \
  --progress-interval 5
```

每个实验独立并行执行，实时输出进度（同时写入 `progress.json` 与 `progress.ndjson`）。with-skill 模式经 Subject Adapter 按宿主规则把 skill 注入工作区（如 `.opencode/skills/`、`.claude/skills/`、AGENTS.md 等），并立即做加载验证（防假对照）；without-skill 模式严格隔离不注入。两种模式使用完全相同的用户需求和测试数据。基础设施异常（网络/超时）按纪律标 `invalid` 并自动重试，不计入能力分母；断言失败计 `failure`。

并发建议：
- 5 个被测模型 × 2 模式 × repetitions 轮 = 实验总数。稳定默认建议 `--parallelism 5`。
- 盲评阶段建议 `--review-parallelism 4` 到 `6`。

### 步骤 4：评委盲评

```
python3 -m evaluator.cli evaluate \
  --run-dir <run目录> \
  --voting-models <评审模型1,评审模型2>
```

**批量投票模式（默认）**：评委一次性拿到所有方案（匿名化），根据方案质量直接投票给认为最好的 N 个方案（N = 配对组数，不超过实际候选方案数）。评委只看到统一的用户需求和匿名方案结果，不知道 skill 名称、流程和约束，也不知道方案来自哪个宿主。空产出物的实验会自动跳过。

在进入评委 prompt 前，评测器会为每个实验生成 `artifacts/result_selection.json`：
- `validated_result_files`：评委真正看到和评价的最终结果文件
- `missing_required_outputs`：用户明确要求但模型没有交付的结果格式
- `evidence_files`：summary、trace、脚本、日志、中间素材等过程证据

当用户没有明确指定交付格式时，评测器会读取被测 skill 的 `SKILL.md`，从其中声明或承诺的文件格式（如 `.pptx`、`.md`、`.xlsx`、`.html` 等）推断实际验收产物；如果 skill 也没有声明输出格式，才按通用报告任务兜底选择 `.md`。

评委不会看到完整 artifacts 文件夹，也不会基于文件数量、耗时、token、执行过程或自评摘要投票。评委一致率（同一配对内评委同判定比例）随评测统计落盘；评委分歧大时触发人审记录。

### 步骤 5：洞察总结（Summary）

```bash
python3 -m evaluator.cli pipeline --auto   # 或分步执行
```

基于评委评价生成结构化洞察结论 `summary.json`（总结模型经统一宿主调度层调用）。

### 步骤 6：生成报告

```bash
python3 -m evaluator.cli report --run-dir <run目录>
```

生成静态 HTML 报告（结构见下）。

### 快照复验

```bash
python3 -m evaluator.cli verify-snapshot --run-dir <run目录>
```

优先复验判定快照（`p1/judgment_snapshot.json`），否则复验断言快照（`assertions_snapshot.json`）——同输入同判定，可复现。

## HTML 报告结构

区块顺序：

1. **⚠️ 宿主适用边界（Host Boundary）**：已测宿主清单 + 「结论仅适用于已测宿主，禁止跨宿主外推」声明
2. **① 总结**：三句话以内说清这次评测结果（确定性拼接，零 LLM）
3. **② 负迁移警报**（如有）：配对增益为负且证据充分 → 显著标注「建议在该模型上关闭此 skill」；未检出时不渲染该区块
4. **③ 按模型看结果**：每个模型一个完整区块，报告唯一的产物展示层——
   - 模型名 + 结论 pill（如「这次没测出明显区别」）
   - 评委打分：「X 位评委中有 Y 位认为带 skill 更好」+ 评委原话引用
   - 评委评价：各匿名方案的评委评价原话/提炼（无评价时给出兜底说明，不丢信息）
   - 带 / 不带 skill 主交付物并排预览（默认展开，含完整文件下载）
   - 诊断信息：状态、文件数、耗时、tokens
   - ▸ 技术细节（默认收起）：平均分差 / 波动范围 / 轮次等统计口径 + 判定依据 + 该模型其余过程证据
5. **④ 配对明细与统计口径**（折叠）：逐对逐轮 Δ、评委一致率、人审触发记录、有效样本量 N（N<5 显式告警）
6. **⑤ 附录**（折叠）：判定器版本与断言规则快照（零 LLM 可复验）、证据链与可信度

## Evaluation Contract

评测遵循以下契约：

- **实验矩阵**：宿主 × 每个模型 × 每个 skill 模式（with/without）× repetitions 次重复，独立执行
- **配对口径**：同模型同重复轮次的 with/without 一一配对，主指标为配对差值 Δ
- **三结论判定**：`skill_effective`（有效）/ `negative_migration`（负迁移，建议关闭）/ `inconclusive`（证据不足，不强判）——纯确定性函数，零 LLM
- **断言优先**：四类确定性断言（exact_match / contains_facts / recomputable / script_eval）先于盲评；判定器版本固定 + 快照可复验
- **盲评模式**：批量投票，评委匿名评审、只投 `validated_result_files`
- **宿主能力预检**：每个宿主显式声明能力，不支持的能力显式报错，绝不静默降级
- **Subject Adapter 契约**：任务投递 + 按宿主规则注入 skill + 产物收集（artifacts/）+ 加载验证（防假对照）
- **对照基线**：`without skill` 只约束目标、输入和交付，不约束内部步骤
- **合成数据标记**：使用 AI 生成的测试数据会在报告中标记为 synthetic/mock

## Required Resources

评测前会自动扫描被评测 skill 的 SKILL.md 内容，根据执行依赖上下文**动态识别**所需资源。识别结果会附带 `confidence`、`evidence` 和 `detection_reason`，避免把说明性示例或资源类型清单误当成真实执行依赖。常见资源类型包括：

| 类型 | 识别依据 | 是否 blocking | 可自动生成 |
|------|------|:---:|:---:|
| file | 提到 xlsx/csv/json/文件/数据集等 | 视情况 | 是 |
| credential | 提到账号/密码/token/key 等 | 是 | 否 |
| database | 提到数据库/连接/sql 等 | 是 | 否 |
| api_key | 提到 API/endpoint/webhook 等 | 是 | 否 |
| browser | 提到 chrome/playwright/浏览器等 | 是 | 否 |
| environment | 提到 python/依赖/环境/安装等 | 否 | 否 |
| user_confirmation | 提到确认/询问用户/请选择等 | 否 | 否 |
| business_context | 提到业务问题/分析目标/需求等 | 否 | 否 |

> 注意：资源检测不是固定清单，而是基于 SKILL.md 内容动态匹配。只有在资源/输入/前置条件/执行流程等上下文中出现的依赖，才会作为需要收集的资源；说明性文字、示例和资源类型清单会被降权处理。

如果被测 skill 的 frontmatter 显式声明了 `required_resources`，评测器会优先使用该声明；没有声明时才自动识别。

## 支持的 Agent 工具：三种接入方式，CLI 不是门槛

**先说清一个常见误会**：本 skill 的 `--host` 等 CLI 参数是**评测器自己的入口**，不是对被测工具的要求。被测的 Agent 工具（Claude Code、Cursor、Trae、Windsurf、通义灵码、OpenCode、Cindy……）**不需要装任何额外东西**——只要它能被程序唤起干活（有命令行入口 / headless 模式 / 自带 API），就能接入评测。

评测器经统一宿主调度层（`executors/`）+ Subject Adapter 分发，提供三种接入方式：

| 接入方式 | 适用对象 | 你需要做什么 |
|---|---|---|
| **① 原生适配器**（开箱即用） | 已内置适配器的工具 | 什么都不用装，`--host opencode` 等直接用 |
| **② 脚本契约**（万能接入，推荐） | **任何**能在命令行被唤起、收 prompt、交文件的 AI 工具 | 写一个 ≤10 行的包装脚本（见下） |
| **③ 直调模型 API** | 只有 API、没有工具执行环境的场景 | 配 provider key（`--host api` / `standalone` / `agent`） |

### 接入方式 × 工具矩阵

| 工具 | 走哪条道 | 验证状态（如实标注） |
|---|---|---|
| OpenCode | ① 原生适配器（`--host opencode`） | ✅ 真实端到端已验证（真实 CLI 驱动 + 多次真实评测） |
| Claude Code | ① 原生适配器（`--host claude-code`）或 ② 脚本契约 | ① 代码+契约测试就绪，真实端到端待验证；② 可用 |
| Codex | ① 原生适配器（`--host codex`）或 ② 脚本契约 | ① 代码+契约测试就绪，真实端到端待验证；② 可用 |
| Cursor | ② 脚本契约（包装 `cursor-agent` CLI / agent 命令） | 链路同 script 宿主（已验证），具体包装待用户环境实测 |
| Trae | ② 脚本契约（包装其 CLI / headless 模式） | 同上 |
| Windsurf | ② 脚本契约（包装 `windsurf`/`cascade` CLI 或 headless） | 同上 |
| 通义灵码 | ② 脚本契约（包装其 CLI 入口） | 同上 |
| Cindy / 其他桌面 Agent | ② 脚本契约（包装其命令行/自动化入口），或 ③ 若仅暴露 API | 链路已验证，具体包装待实测 |
| 任意自写 Agent 脚本 | ② 脚本契约（本身就是契约脚本） | ✅ script 宿主端到端已验证 |
| 仅 API 可用（无执行环境） | ③ 直调 API（`--host api` / `standalone` / `agent`） | 代码就绪；产物型任务被能力预检显式拦截（无文件操作能力，如实告知） |

### 脚本契约：5 行接入指南（照抄即可）

任何 AI 工具只要能被这样唤起：收一段任务 prompt → 在工作目录干活 → 交出文件，就能用同一个模板包装：

```python
#!/usr/bin/env python3
# my_tool_wrapper.py — 把你的 AI 工具接进评测（stdin 收 JSON，stdout 回 JSON）
import json, subprocess, sys
p = json.loads(sys.stdin.read())   # p = {"model", "prompt", "cwd", "skill_dir"}
r = subprocess.run(["你的工具命令", p["prompt"]], cwd=p["cwd"], capture_output=True, text=True)  # ← 只改这一行
print(json.dumps({"exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr,
                  "text": r.stdout, "transcript": r.stdout, "usage": {}}))
```

```bash
chmod +x my_tool_wrapper.py
python3 -m evaluator.cli plan --generate ... --host script \
  --host-config '{"script": "./my_tool_wrapper.py"}'
python3 -m evaluator.cli run --plan-dir <run目录>
```

评测器负责其余一切：把 skill 按约定注入工作区（`.skills/`）、投递任务、收集 `artifacts/`、验证 skill 真被加载（防假对照）、断言判定与报告。完整契约（字段说明、超时、skill_dir 语义）见 `evaluator/executors/script.py` 模块 docstring 与 `runs/p2-two-hosts-demo/mock_host_script.py` 真实样例。

### 原生适配器现状（如实标注，不夸大）

| 宿主（`--host`） | skill 注入约定 | 现状 |
|---|---|---|
| `opencode` | `.opencode/skills/` + `opencode.json` 权限放行 | **开箱即用，真实端到端已验证** |
| `claude-code` | `.claude/skills/` 目录 | **需本机装 claude CLI**；executor+适配器+契约测试就绪，真实端到端待验证 |
| `codex` | 项目根 `AGENTS.md` 注入 | **需本机装 codex CLI**；同上待验证 |
| `script` | `.skills/` 目录注入 + 加载验证 | **开箱即用**（脚本契约宿主，端到端已验证）；它本身不是某个具体工具，而是任何工具的接入通道 |
| `api` / `standalone` / `agent` | 无目录注入 | **需 provider key**；无文件操作能力，产物型任务被能力预检显式拦截 |

注意：

- 未安装对应 CLI 的原生适配器（claude-code / codex）在连通性/配置校验阶段显式报错，不会静默降级；此时改走脚本契约即可
- 每个宿主显式声明能力（`file_ops` / `skill_injection` / `transcript`），不支持的能力显式报错
- 报告「⚠️ 适用范围」只展示本次实际已测宿主，结论禁止跨宿主外推

## Prerequisites

- Python 3.10+（推荐 3.12+）
- 至少一个能被程序唤起干活的 AI 工具：原生适配器宿主（如 OpenCode，开箱即用）、**任何工具经脚本契约包装**（≤10 行模板，见「支持的 Agent 工具」）、或纯 API 宿主（`api`/`standalone` 需 provider key，但不具备文件操作能力）
- 被测工具本身不需要装任何额外东西——评测器的 CLI 参数是评测器自己的入口，不是对被测工具的要求
- 依赖安装：`pip install -r requirements.txt`
