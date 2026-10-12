# daming-skillgain - 标准化使用指南（v7.0.0）

## 快速开始

### 方式 1：Python SDK（推荐）

```python
from evaluator import SkillEvaluator

# 初始化
evaluator = SkillEvaluator(
    skill_path="./my-skill",
    executor_type="api",  # 或 "opencode" / "claude-code" / "codex" / "script"
    api_key="your-api-key"
)

# 执行评测
result = evaluator.run(
    task_prompt="评测这个 skill 的功能",
    models=["gpt-4", "claude-3"],
    voting_models=["gpt-4"],
    summary_model="gpt-4"
)

print(result["summary"])
print(result["report_path"])
```

### 方式 2：快捷函数

```python
from evaluator import evaluate_skill

result = evaluate_skill(
    skill_path="./my-skill",
    task_prompt="评测这个 skill 的功能",
    models=["gpt-4", "claude-3"],
    voting_models=["gpt-4"],
    summary_model="gpt-4",
    executor_type="api"
)
```

### 方式 3：CLI

```bash
# 使用 opencode 执行器
python3 -m evaluator.cli plan --init --skill-path ./my-skill
python3 -m evaluator.cli plan --generate \
  --skill-path ./my-skill \
  --models "gpt-4,claude-3" \
  --task-prompt "评测这个 skill" \
  --voting-models "gpt-4" \
  --summary-model "gpt-4"
python3 -m evaluator.cli run --plan-dir ./runs/xxx

# 使用 API 执行器
python3 -m evaluator.cli run \
  --plan-dir ./runs/xxx \
  --executor api \
  --api-key your-api-key
```

### 方式 4：REST API

```bash
# 启动 API 服务
python3 -m evaluator.api_server --port 8000

# 调用 API
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

### 方式 5：MCP Server

```bash
# 启动 MCP Server
python3 -m evaluator.mcp_server

# 任何支持 MCP 的 agent 都能调用
```

## 执行器类型

### 1. OpenCode 执行器

通过 opencode CLI 执行，适合本地开发。

```python
evaluator = SkillEvaluator(
    skill_path="./my-skill",
    executor_type="opencode",
    cli_path="~/.opencode/bin/opencode",
    server_url="http://127.0.0.1:4096"
)
```

### 2. API 执行器

直接调用模型 API，不依赖任何工具。

```python
evaluator = SkillEvaluator(
    skill_path="./my-skill",
    executor_type="api",
    provider="openai",  # 或 "anthropic"
    api_key="your-api-key"
)
```

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

然后使用：

```python
import json
from evaluator import SkillEvaluator

with open("eval-config.json") as f:
    config = json.load(f)

evaluator = SkillEvaluator(**config)
result = evaluator.run(**config)
```

## 输出格式

```python
{
  "success": true,
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

## 错误处理

```python
result = evaluator.run(...)

if result["success"]:
    print("评测成功！")
    print(result["summary"])
else:
    print(f"评测失败：{result['error']}")
```

## 高级用法

### 自定义执行器

```python
from evaluator import SkillEvaluationWorkflow, BaseExecutor

class MyExecutor(BaseExecutor):
    def execute(self, model, prompt, **kwargs):
        # 自定义执行逻辑
        pass
    
    def check_connectivity(self, model):
        return True
    
    def list_models(self):
        return ["my-model"]

workflow = SkillEvaluationWorkflow(executor=MyExecutor())
result = workflow.run(config)
```

### 批量评测

```python
from evaluator import SkillEvaluator

evaluator = SkillEvaluator(executor_type="api", api_key="...")

skills = ["./skill-1", "./skill-2", "./skill-3"]
results = []

for skill in skills:
    evaluator.skill_path = skill
    result = evaluator.run(
        task_prompt="评测这个 skill",
        models=["gpt-4"],
        voting_models=["gpt-4"],
        summary_model="gpt-4"
    )
    results.append(result)

# 比较结果
for skill, result in zip(skills, results):
    print(f"{skill}: {result['summary']['overall']}")
```
