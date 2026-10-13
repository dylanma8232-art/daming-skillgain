# daming-skillgain — 独立执行宿主指南（standalone / api 宿主）

> 版本 v1.0。本指南描述 `standalone` / `api` 两类**纯 API 宿主**的能力边界与正确用法。
> 与旧版文档的关键差异：**这两类宿主不具备文件操作能力（file_ops）**，跑产物型评测任务
> 会被能力预检显式拦截——这是设计行为，不是缺陷。

## 宿主能力边界（先读这个）

| 宿主 | file_ops | skill_injection | 适用任务 |
|------|:---:|------|------|
| `opencode` / `claude-code` / `codex` / `script` | ✅ | 按宿主约定注入 | 产物型评测（读写工作区文件） |
| `standalone` / `api` / `agent` | ❌ / 实验性 | ❌（无注入约定） | 纯文本往返任务 |

产物型任务（绝大多数 skill 评测）请使用 CLI 型或 script 宿主。对纯 API 宿主发起产物型评测时，
`run_from_plan` 会抛出 `UnsupportedCapabilityError`，明确指出缺少 `file_ops` 能力——不会静默降级。

## 快速开始

### 方式 1：CLI（推荐，零 LLM 编排）

```bash
# opencode 宿主（默认）
python3 -m evaluator.cli plan --generate --skill-path ./my-skill \
  --task-prompt "..." --models m1,m2 --voting-models j1,j2 \
  --summary-model j1
python3 -m evaluator.cli run --plan-dir <run目录>

# 指定宿主
python3 -m evaluator.cli plan --generate ... --host claude-code
python3 -m evaluator.cli plan --generate ... --host script --resources '[{"type":"script_path","value":"/path/to/host.sh"}]'
```

### 方式 2：Python SDK

```python
from evaluator import SkillEvaluator

# 纯 API 宿主（仅限文本往返任务）
evaluator = SkillEvaluator(
    skill_path="./my-skill",
    executor_type="standalone",           # 或 "api"
    executor_config={"provider": "openai", "api_key": "sk-xxx"},
    auto_discover=False,
)

result = evaluator.run(
    task_prompt="评测这个 skill",
    models=["gpt-4"],
    voting_models=["gpt-4"],
    summary_model="gpt-4",
)
```

### 方式 3：自动发现

```python
evaluator = SkillEvaluator(skill_path="./my-skill")  # 自动检测本地 CLI 与 API key
```

## standalone 与 api 的区别

- `standalone`：`SkillEvaluator` SDK 内置 HTTP 客户端（openai / anthropic / minimax / dashscope 兼容），不依赖任何 CLI。
- `api`：直连模型 API 的执行器，同样无文件操作能力。

两者都只做「prompt 进、text 出」的会话；skill 注入、产物收集、加载验证三项 Subject Adapter
契约均不适用——它们没有工作区文件系统。

## 常见问题

### Q: 为什么 standalone 宿主跑 skill 评测直接报错？

A: 产物型评测要求宿主能读写工作区文件（写 artifacts/、读输入文件）。`standalone`/`api`
没有文件操作能力，能力预检显式拦截并说明原因。请改用 `opencode` / `claude-code` /
`codex` / `script` 宿主。

### Q: 没有 API key 怎么办？

A: 使用本地模型网关（ollama / lmstudio / vllm 的 OpenAI 兼容端点）配置 `base_url`，
或使用已登录的 CLI 型宿主（opencode / claude-code / codex）。

### Q: 本机没有这些 CLI 怎么办？

A: 用 `--host script` 提供一个符合契约的可执行脚本（stdin/stdout JSON）充当宿主，
可完成端到端链路验证；结论仅适用于 script 宿主，不得外推到真实宿主。
