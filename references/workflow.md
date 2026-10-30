# 评测执行流程（命令细节）

`SKILL.md` 只给入口命令。需要分步执行、调参或排查时读本文件。

## 步骤 0：检查更新（轻量，不阻塞）

```bash
python3 -m evaluator.cli update-check
```

CLI 每次启动会自动后台检查 GitHub 新版本（每 24h 最多一次，网络失败/超时 3s 静默跳过，不阻塞主流程）。离线环境设 `DAMING_SKILLGAIN_SKIP_UPDATE_CHECK=1` 关闭。

## 步骤 1：初始化配置收集

```bash
python3 -m evaluator.cli plan --init --skill-path <被评测skill目录>
```

输出需要收集的字段：被评测 skill 的能力分析与所需资源、可用模型列表、评测步骤指引。

### 准备阶段硬规则（调用 `plan --generate` 前必须完成）

- **评测需求必问**：必须问用户「本次评测要让被测 skill 执行什么具体需求」。只有用户明确说「你根据 skill 自己生成需求」时，才允许省略 `--task-prompt` 并加 `--allow-generated-task-prompt`。
- **文件数据必问**：若 `plan --init` 或 skill 分析显示需要 `file` 资源，先问用户是否有测试文件。有则传 `--input-files` 或资源 `value`；无且用户明确允许生成时，才在该资源 JSON 传 `allow_auto_generation: true`。
- **敏感资源不能生成**：账号、密码、token、API key、数据库连接、浏览器测试环境等只能由用户提供，不能由 AI 生成。
- **遇到 `needs_user_input` 必须停止**：若 `plan --generate` 返回 `error=needs_user_input`，把返回的 `questions` 展示给用户并等待回答；不要继续 run，也不要默认生成数据。

## 步骤 2：生成评测计划

```bash
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

`--voting-models` 必填（盲评是必选流程）。`--task-prompt` 默认必填；仅用户明确授权时才可加 `--allow-generated-task-prompt`。

**宿主选择（--host）**：实验执行、盲评评委与总结模型都经统一宿主调度层分发到所选宿主。未安装对应 CLI 的宿主（claude-code / codex）会在连通性/配置校验阶段显式报错，不静默降级。`api` / `standalone` 无文件操作能力，产物型任务会被能力预检显式拦截。详见 [hosts.md](hosts.md)。

**模型连通性测试**：此阶段自动对所有选中模型（参与模型 + 评委 + 总结）经所选宿主做连通性测试。默认 `--execution-mode cli-attach`（宿主域内 attach）；强制本地子进程用 `cli`；宿主 HTTP API 用 `api`。测试失败会阻止生成计划；跳过加 `--skip-connectivity-test`（不推荐）。

**Preflight 校验**：自动执行资源预校验（file / environment / browser / database / api_key / credential）。存在 blocking 问题时阻止生成计划。资源类型与识别规则见 [resources.md](resources.md)。

## 步骤 3：执行评测实验

```bash
python3 -m evaluator.cli run \
  --plan-dir <plan生成的run目录> \
  --parallelism 5 \
  --review-parallelism 4 \
  --progress-interval 5
```

每个实验独立并行执行，实时输出进度（同时写入 `progress.json` 与 `progress.ndjson`）。with-skill 模式经 Subject Adapter 按宿主规则把 skill 注入工作区（如 `.opencode/skills/`、`.claude/skills/`、`AGENTS.md` 等），并立即做加载验证（防假对照）；without-skill 模式严格隔离不注入。两种模式使用完全相同的用户需求和测试数据。基础设施异常（网络/超时）标 `invalid` 并自动重试，不计入能力分母；断言失败计 `failure`。

并发建议：实验总数 = 被测模型数 × 2 模式 × repetitions。稳定默认 `--parallelism 5`；盲评阶段 `--review-parallelism 4`~`6`。

## 步骤 4：评委盲评

```bash
python3 -m evaluator.cli evaluate \
  --run-dir <run目录> \
  --voting-models <评审模型1,评审模型2>
```

**批量投票模式（默认）**：评委一次性拿到所有匿名化方案，直接投票给认为最好的 N 个（N = 配对组数，不超过候选方案数）。评委只看到统一的用户需求和匿名方案结果，不知道 skill 名称、流程、约束，也不知道方案来自哪个宿主。空产出物的实验自动跳过。

进入评委 prompt 前，评测器为每个实验生成 `artifacts/result_selection.json`：

- `validated_result_files`：评委真正看到和评价的最终结果文件
- `missing_required_outputs`：用户明确要求但模型没交付的结果格式
- `evidence_files`：summary、trace、脚本、日志、中间素材等过程证据

用户没指定交付格式时，评测器读被测 skill 的 `SKILL.md`，从中声明/承诺的文件格式（`.pptx`/`.md`/`.xlsx`/`.html` 等）推断实际验收产物；skill 也未声明时按通用报告任务兜底选 `.md`。

评委不会看到完整 artifacts 文件夹，也不会基于文件数量、耗时、token、执行过程或自评摘要投票。评委一致率随评测统计落盘；分歧大时触发人审记录。

## 步骤 5：洞察总结

```bash
python3 -m evaluator.cli pipeline --auto   # 或分步执行
```

基于评委评价生成结构化洞察结论 `summary.json`（总结模型经统一宿主调度层调用）。

## 步骤 6：生成报告

```bash
python3 -m evaluator.cli report --run-dir <run目录>
```

生成静态 HTML 报告，结构见 [report-and-contract.md](report-and-contract.md)。

## 快照复验

```bash
python3 -m evaluator.cli verify-snapshot --run-dir <run目录>
```

优先复验判定快照（`p1/judgment_snapshot.json`），否则复验断言快照（`assertions_snapshot.json`）——同输入同判定，可复现。
