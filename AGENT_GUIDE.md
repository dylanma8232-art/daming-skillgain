# Agent 执行器 - 支持工具调用

## 核心改进

**Agent 宿主（实验性）**：支持工具调用，让模型自己读取文件，而不是把文件内容都放在 prompt 里。

> ⚠️ **实验性宿主**（7.0.0）：`agent` 宿主未接入 Subject Adapter 统一契约（无 skill 注入与加载验证），
> 产物型评测会被能力预检拦截；生产评测请使用 `opencode` / `claude-code` / `codex` / `script` 宿主。
> 保留原因：纯 API 多轮工具循环的参考实现。

## 工具列表

| 工具 | 功能 | 参数 |
|------|------|------|
| `read_file` | 读取文件内容 | `path`: 文件路径 |
| `write_file` | 写入文件内容 | `path`: 文件路径, `content`: 文件内容 |
| `list_files` | 列出目录下的文件 | `path`: 目录路径 |
| `execute_command` | 执行 shell 命令 | `command`: 要执行的命令 |

## 使用方式

```python
from evaluator import SkillEvaluator

# 使用 Agent 执行器
evaluator = SkillEvaluator(
    skill_path="./my-skill",
    executor_type="agent",
    executor_config={
        "provider": "openai",
        "api_key": "sk-xxx",
        "working_dir": "/path/to/workspace"
    },
    auto_discover=False
)

# 执行评测
result = evaluator.run(
    task_prompt="读取 data.csv 并分析销售趋势",
    models=["gpt-4"],
    voting_models=["gpt-4"],
    summary_model="gpt-4"
)
```

## 工作流程

```
1. 用户发送任务 prompt
2. 模型分析任务，决定需要读取哪些文件
3. 模型调用 read_file 工具读取文件
4. 模型处理文件内容
5. 模型调用 write_file 工具写入结果
6. 返回最终结果
```

## 与直接调用 API 的区别

| 特性 | 直接调用 API | Agent 执行器 |
|------|-------------|--------------|
| 文件读取 | 需要把文件内容放在 prompt 里 | 模型自己调用 read_file |
| 大文件 | ❌ token 爆炸 | ✅ 只读取需要的部分 |
| 本地文件系统 | ❌ 无法访问 | ✅ 可以访问 |
| 工具调用 | ❌ 不支持 | ✅ 支持 |
| 交互循环 | ❌ 一次性 | ✅ 多轮交互 |

## 与 opencode 的区别

| 特性 | opencode | Agent 执行器 |
|------|----------|--------------|
| 依赖 | 需要安装 opencode CLI | 无依赖 |
| 工具 | opencode 提供的工具 | 自定义工具 |
| 文件访问 | 通过 opencode | 直接访问 |
| 可移植性 | 只能在 opencode 环境 | 任何环境都能运行 |

## 配置选项

```python
evaluator = SkillEvaluator(
    skill_path="./my-skill",
    executor_type="agent",
    executor_config={
        "provider": "openai",           # 模型提供商
        "api_key": "sk-xxx",            # API key
        "base_url": "...",              # 自定义 base URL（可选）
        "timeout": 120,                 # 超时时间（秒）
        "max_iterations": 10,           # 最大迭代次数
        "working_dir": "/path/to/dir"   # 工作目录
    },
    auto_discover=False
)
```

## 示例

### 读取文件并分析

```python
result = evaluator.run(
    task_prompt="读取 sales.csv 文件，分析销售趋势，生成报告",
    models=["gpt-4"]
)
```

### 执行命令

```python
result = evaluator.run(
    task_prompt="运行 python analyze.py 并返回结果",
    models=["gpt-4"]
)
```

### 写入文件

```python
result = evaluator.run(
    task_prompt="生成一个 HTML 报告，保存到 report.html",
    models=["gpt-4"]
)
```
