---
name: effective-marker-skill
description: 有效 skill 夹具：指导 agent 在 summary.md 中写入规定格式的 EVAL_PASS 行，并遵守格式规范
---
# Effective Marker Skill

## 目标
你必须产出 `artifacts/summary.md`，并且文件中必须包含一行：
`EVAL_PASS: <你的模型名>`

## 规范
1. summary.md 第一行必须是 `# Evaluation Summary`。
2. 必须包含 `EVAL_PASS:` 行。
3. 不要输出与任务无关的内容。
