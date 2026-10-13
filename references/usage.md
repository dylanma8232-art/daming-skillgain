# 编程接入（SDK / REST / MCP）与宿主能力边界

`SKILL.md` 的 CLI 是最常用入口。需要用代码调度、或对接纯 API 宿主时读本文件。

## 宿主能力边界（先读这个）

| 宿主 | file_ops | skill_injection | 适用任务 |
|------|:---:|------|------|
| `opencode` / `claude-code` / `codex` / `script` | ✅ | 按宿主约定注入 | 产物型评测（读写工作区文件） |
| `standalone` / `api` | ❌ | ❌（无注入约定） | 纯文本往返任务 |
| `agent` | 实验性 | ❌（未接入 Subject Adapter） | 纯文本往返 / 多轮工具循环参考实现 |

产物型任务（绝大多数 skill 评测）请使用 CLI 型或 script 宿主。对纯 API 宿主发起产物型评测时，
`run_from_plan` 会抛出 `UnsupportedCapabilityError`，明确指出缺少 `file_ops` 能力——不会静默降级。
`agent` 宿主未接入 Subject Adapter 统一契约（无 skill 注入与加载验证），产物型评测同样会被能力预检拦截；
保留原因是作为纯 API 多轮工具循环的参考实现。生产评测请用 `opencode` / `claude-code` / `codex` / `script`。

## Python SDK

```python
from evaluator import SkillEvaluator

# 初始化
evaluator = SkillEvaluator(
    skill_path="./my-skill",
    executor_type="api",  # 或 "opencode" / "claude-code" / "codex" / "script"
    api_key="your-api-key",
)

# 执行评测
result = evaluator.run(
    task_prompt="评测这个 skill 的功能",
    models=["gpt-4", "claude-3"],
    voting_models=["gpt-4"],
    summary_model="gpt-4",
)

print(result["summary"])
print(result["report_path"])
```

快捷函数 `evaluate_skill(...)` 参数一致，一步完成。

`SkillEvaluator(skill_path="./my-skill")` 不传 `executor_type` 时会**自动发现**本地 CLI 与 API key。

## REST API

```bash
python3 -m evaluator.api_server --port 8000

curl -X POST http://localhost:8000/api/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "skill_path": "./my-skill",
    "task_prompt": "评测这个 skill",
    "models": ["gpt-4", "claude-3"],
    "voting_models": ["gpt-4"],
    "summary_model": "gpt-4"
  }'
```

## MCP Server

```bash
python3 -m evaluator.mcp_server
```

任何支持 MCP 的 agent 都能调用。

## 配置文件

创建 `eval-config.json`：

```json
{
  "skill_path": "./my-skill",
  "task_prompt": "评测这个 skill",
  "models": ["gpt-4", "claude-3"],
  "voting_models": ["gpt-4"],
  "summary_model": "gpt-4",
  "executor": {
    "type": "api",
    "provider": "openai",
    "api_key": "${OPENAI_API_KEY}"
  }
}
```

## 执行器类型

- `opencode`（默认）：经 opencode CLI 执行；`cli_path`、`server_url` 可配。
- `claude-code` / `codex`：需本机安装对应 CLI。
- `script`：脚本契约宿主（见 [hosts.md](hosts.md)），任何工具经包装脚本接入。
- `api`：直连模型 API（openai / anthropic），不依赖 CLI。
- `standalone`：SDK 内置 HTTP 客户端（openai / anthropic / minimax / dashscope 兼容），不依赖 CLI。
- `agent`：实验性，多轮工具循环参考实现（工具 `read_file` / `write_file` / `list_files` / `execute_command`）。

`standalone` / `api` / `agent` 都只做「prompt 进、text 出」的会话，没有工作区文件系统，
skill 注入、产物收集、加载验证三项契约均不适用。

## 输出格式

```python
{
  "success": True,
  "run_dir": "./runs/20260622-xxx",
  "summary": {
    "overall": "建议开启 Skill",
    "overall_reason": "评委投票：用 Skill 得 8 票，不用得 2 票",
    "skill_helped": ["结构更完整", "数据更准确"],
    "skill_not_helped": ["速度稍慢"],
    "suggestion": "建议在生产环境使用"
  },
  "report_path": "./runs/20260622-xxx/report.html"
}
```

失败时 `success` 为 `False`，原因在 `result["error"]`。

## 常见问题

**Q：为什么 API 型宿主跑 skill 评测直接报错？**
A：产物型评测要求宿主能读写工作区文件（写 `artifacts/`、读输入文件）。`standalone`/`api` 没有文件操作能力，能力预检显式拦截并说明原因。请改用 `opencode` / `claude-code` / `codex` / `script` 宿主。

**Q：没有 API key 怎么办？**
A：使用本地模型网关（ollama / lmstudio / vllm 的 OpenAI 兼容端点）配置 `base_url`，或使用已登录的 CLI 型宿主。

**Q：本机没有这些 CLI 怎么办？**
A：用 `--host script` 提供一个符合契约的可执行脚本（stdin/stdout JSON）充当宿主，可完成端到端链路验证；结论仅适用于 script 宿主，不得外推到真实宿主。
