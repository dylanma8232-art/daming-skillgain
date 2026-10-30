# 宿主接入（被测 AI 工具）

`--host` 等 CLI 参数是**评测器自己的入口**，不是对被测工具的要求。被测工具（Claude Code、Cursor、Trae、Windsurf、通义灵码、OpenCode、Cindy……）**不需要装任何额外东西**——只要能按命令唤起干活（命令行入口 / headless 模式 / 自带 API），就能接入。

评测器经统一宿主调度层（`evaluator/executors/`）+ Subject Adapter 分发，三种接入方式：

| 接入方式 | 适用对象 | 你需要做什么 |
|---|---|---|
| ① 原生适配器 | 已内置适配器的工具 | 什么都不用装，`--host opencode` 等直接用 |
| ② 脚本契约（万能，推荐） | 任何能在命令行被唤起、收 prompt、交文件的工具 | 写一个 ≤10 行包装脚本（见下） |
| ③ 直调模型 API | 只有 API、没有工具执行环境的场景 | 配 provider key（`--host api` / `standalone` / `agent`） |

## 接入方式 × 工具矩阵

| 工具 | 走哪条道 | 验证状态（如实标注） |
|---|---|---|
| OpenCode | ① 原生适配器（`--host opencode`） | ✅ 真实端到端已验证（真实 CLI 驱动 + 多次真实评测） |
| Claude Code | ① 原生适配器（`--host claude-code`）或 ② 脚本契约 | ① 代码+契约测试就绪，真实端到端待验证；② 可用 |
| Codex | ① 原生适配器（`--host codex`）或 ② 脚本契约 | ① 代码+契约测试就绪，真实端到端待验证；② 可用 |
| cursor-agent | ① 原生适配器（`--host cursor-agent`） | ✅ 真机 `--version` 探活通过（2026.10.22-27dcf5f）；非交互执行需登录后可用 |
| Cursor | ② 脚本契约（包装 `cursor-agent` CLI） | 链路同 script 宿主（已验证），具体包装待用户环境实测 |
| Trae | ② 脚本契约（包装其 CLI / headless） | 同上 |
| Windsurf | ② 脚本契约（包装 `windsurf`/`cascade` CLI 或 headless） | 同上 |
| 通义灵码 | ② 脚本契约（包装其 CLI 入口） | 同上 |
| Cindy / 其他桌面 AI 工具 | ② 脚本契约（包装其命令行/自动化入口），或 ③ 若仅暴露 API | 链路已验证，具体包装待实测 |
| 任意自写脚本 | ② 脚本契约（本身就是契约脚本） | ✅ script 宿主端到端已验证 |
| 仅 API 可用（无执行环境） | ③ 直调 API（`--host api` / `standalone` / `agent`） | 代码就绪；产物型任务被能力预检显式拦截（如实告知） |

## 脚本契约：5 行接入模板

任何工具只要能被「收一段 prompt → 在工作目录干活 → 交出文件」这样唤起，就能用同一模板包装：

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

评测器负责其余一切：把 skill 按约定注入工作区（`.skills/`）、投递任务、收集 `artifacts/`、验证 skill 真被加载（防假对照）、断言判定与报告。完整契约（字段说明、超时、`skill_dir` 语义）见 `evaluator/executors/script.py` 模块 docstring 与 `runs/p2-two-hosts-demo/mock_host_script.py` 真实样例。

## 原生适配器现状（不夸大）

| 宿主（`--host`） | skill 注入约定 | 现状 |
|---|---|---|
| `opencode` | `.opencode/skills/` + `opencode.json` 权限放行 | 开箱即用，真实端到端已验证 |
| `claude-code` | `.claude/skills/` 目录 | 需本机装 claude CLI；executor+适配器+契约测试就绪，真实端到端待验证 |
| `codex` | 项目根 `AGENTS.md` 注入 | 需本机装 codex CLI；同上待验证 |
| `cursor-agent` | 项目根 `AGENTS.md` 注入 | 本机已装（`--version` rc=0）；`--list-models` 需登录 |
| `script` | `.skills/` 目录注入 + 加载验证 | 开箱即用（脚本契约宿主，端到端已验证）；它不是某个具体工具，而是任何工具的接入通道 |
| `api` / `standalone` / `agent` | 无目录注入 | 需 provider key；无文件操作能力，产物型任务被能力预检显式拦截 |

注意：未安装对应 CLI 的原生适配器（claude-code / codex）在连通性/配置校验阶段显式报错，不静默降级，此时改走脚本契约。每个宿主显式声明能力（`file_ops` / `skill_injection` / `transcript`），不支持的能力显式报错。报告「适用范围」只展示本次实际已测宿主，结论禁止跨宿主外推。

**新增宿主统一按 `references/host-protocol.md` 的三件套（`build_invocation` / `parse_output` / `probe`）接入**；排查工具：`python3 -m evaluator.cli hosts`。
