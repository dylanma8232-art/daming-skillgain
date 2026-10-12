# DaMing SkillGain 核心架构说明

DaMing SkillGain 是面向大语言模型 Skill（能力包）效用评测的开源工具。本文档介绍系统的整体分层、执行流程、执行器抽象与扩展机制。

---

## 1. 架构总览与主流程全景

SkillGain 的主评测流程由六个核心阶段组成：

```
[输入配置 / SKILL.md]
        │
        ▼
┌────────────────────────────────────────────────────────┐
│ 阶段一：计划与契约生成 (Plan & Contract Generation)     │
│ - Skill 能力解析 (analyze_skill)                       │
│ - 契约与四类断言生成 (build_evaluation_contract)        │
│ - 资源预检 (preflight_validators)                      │
│ - 矩阵排期 (build_plans -> plan.json)                  │
└────────────────────────────────────────────────────────┘
        │
        ▼ (交互确认点 1: 计划生成后)
┌────────────────────────────────────────────────────────┐
│ 阶段二：实验矩阵执行 (Experiment Matrix Execution)      │
│ - 独立工作区隔离 (with-skill / without-skill)          │
│ - 提示词对称注入 (无泄漏隔离)                           │
│ - 执行器调度与超时重试 (invalid 判定与重试机制)          │
│ - 产物沉降与收集 (empty product 拦截)                   │
└────────────────────────────────────────────────────────┘
        │
        ▼ (交互确认点 2: 实验执行后)
┌────────────────────────────────────────────────────────┐
│ 阶段三：四类确定性断言判定 (Deterministic Assertions)   │
│ - 精确匹配 / 关键事实 / 可重算 / 脚本判定                │
│ - 判定器版本固定 (v1.1.0)                              │
│ - 断言输入输出全量快照落盘 (assertions_snapshot.json)  │
│ - 确定性判定全程 100% 零 LLM 调用                       │
└────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────┐
│ 阶段四：盲评辅助裁决 (Blind Pair Review - 可选)        │
│ - 方案匿名与洗牌                                       │
│ - 独立评委模型投票 (评委一致率统计)                     │
│ - 过滤空产物方案，仅评估最终交付物                      │
└────────────────────────────────────────────────────────┘
        │
        ▼ (交互确认点 3: 盲评完成后)
┌────────────────────────────────────────────────────────┐
│ 阶段五：同模型配对统计与洞察 (Paired Gain & Summary)    │
│ - 计算每个模型的 Skill Gain Δ = with - without        │
│ - 均值、方差、标准差、95% 置信区间计算                  │
│ - 负迁移检出（Δ < 0 时给出关闭建议）与 inconclusive 防护│
│ - 综合结论生成                                         │
└────────────────────────────────────────────────────────┘
        │
        ▼ (交互确认点 4: 报告生成前)
┌────────────────────────────────────────────────────────┐
│ 阶段六：可视化报告生成 (Reporting)                     │
│ - 静态 HTML 与 Markdown 报告                           │
│ - 首屏增益结论 -> 负迁移警告 -> 配对明细 -> 模型排行   │
└────────────────────────────────────────────────────────┘
```

---

## 2. 核心模块与调用链

系统的关键模块分工如下：

```
evaluator/
├── workflow.py           # 高层工作流编排，负责渐进式分段确认与串联
├── engine.py             # 评测核心引擎：实验调度、产物收集、断言执行、统计汇总
├── host_session.py       # 统一宿主调度层：统一规范各宿主子进程与会话管理
├── paired_gain.py        # 同模型配对增益计算与三结论（effective/negative/inconclusive）判定
├── assertions/           # 确定性断言模块（零 LLM 调用）
│   ├── models.py         # 断言规则与结果数据结构定义，版本标记 (v1.1.0)
│   ├── exact_match.py    # 精确匹配断言（字符串、数值容差、JSON全等）
│   ├── contains_facts.py # 关键事实与排除词断言（自动清洗事件流协议行）
│   ├── recomputable.py   # 数学与逻辑可重算断言（求和/均值/CAGR/表达式）
│   ├── script_eval.py    # 脚本判定（结构化数据 CSV/JSON/XLSX 健全度与产物检查）
│   └── engine.py         # 断言套件统一调度与快照落盘验证
├── executors/            # 执行器接口实现
│   ├── base.py           # 执行器基类 (BaseExecutor)
│   ├── opencode.py       # OpenCode 宿主执行器
│   ├── agent.py          # 通用 Agent 工具调用执行器
│   ├── api.py            # 纯文本补全直连执行器
│   └── standalone.py     # 免安装轻量 HTTP 执行器
├── subject_adapter/      # 被测对象适配层（统一环境感知与 Skill 注入）
├── reporting.py          # 静态 HTML / Markdown 评测报告渲染器
├── skill_analyzer.py     # Skill 意图、契约与输入输出规格解析
└── cli.py                # 命令行入口与交互流程驱动
```

### 关键调用链路

1. **实验执行调用链**:
   `cli.cmd_run` / `workflow.run` 
   → `engine.run_from_plan` 
   → `engine._execute_experiment_impl` 
   → `host_session.run_host_session` 
   → 宿主进程执行并写入 `stdout.txt`、`result.json`、`artifacts/`。

2. **断言判定调用链**:
   `engine.run_from_plan` 
   → `AssertionEngine.evaluate_suite` 
   → 分发到 `exact_match` / `contains_facts` / `recomputable` / `script_eval` 
   → 产物写入 `analysis_data["assertions"]` 
   → 生成 `assertions_snapshot.json`。

3. **配对增益计算调用链**:
   `engine.run_from_plan` 
   → `paired_gain.compute_paired_gain` 
   → 按模型计算各轮次 $\Delta = \text{Score}_{\text{with}} - \text{Score}_{\text{without}}$ 
   → 判定 `skill_effective` / `negative_migration` / `inconclusive` 
   → 输出至 `paired_gain.json` 与 HTML 报告。

---

## 3. 四类确定性断言设计 (零 LLM 保证)

为了彻底避免“LLM 评测 LLM”带来的漂移与不确定性，SkillGain 引入四类确定性断言：

| 断言类型 | 对应模块 | 判定逻辑 | 典型应用场景 |
| :--- | :--- | :--- | :--- |
| **精确匹配** (`exact_match`) | `exact_match.py` | 字符串全等、数值浮点容差（$\pm \epsilon$）、JSON 深度等价、文件哈希 | 状态码返回、固定命名规范、固定枚举 |
| **包含事实** (`contains_facts`) | `contains_facts.py` | 核心事实点、关键字、正则表达式命中，结合负向禁用词 | 方案结论要点覆盖、关键步骤存在性、无错误码提示 |
| **可重算验证** (`recomputable`) | `recomputable.py` | 使用安全 AST 解析器对声称数值重新执行数学公式运算 | 财务求和、平均值、比率计算、复合年均增长率验证 |
| **脚本判定** (`script_eval`) | `script_eval.py` | Python 确定性逻辑函数（如 CSV/JSON 结构合规、借贷平衡校验） | 表格数据对齐、资产负债表平衡、关键文件存在性 |

- **事件流与产物隔离**：断言在判定模型产物时，自动过滤底层调度事件流中的调试信息，优先聚合 `artifacts/` 目录下的交付文档与模型最终回答，确保评分反映真实的交付质量。
- **快照可复验机制**：所有断言输入、实际产出和判定证据均实时保存为 `assertions_snapshot.json`。使用者可随时通过 `evaluator.cli verify-snapshot` 离线重验，保证相同输入必然得到 100% 相同判定。

---

## 4. 执行器与跨宿主适配层 (Subject Adapter)

系统支持通过统一接口适配不同的大模型运行环境与宿主工具：

| 执行器名称 | 标识符 | 运行依赖 | 通信机制 | 适用场景 |
| :--- | :--- | :--- | :--- | :--- |
| **OpencodeExecutor** | `opencode` | 本地安装 `opencode` CLI | 子进程 CLI (`opencode run`) 或 Attach Server | OpenCode 本地开发与标准 agent 环境评测 |
| **AgentExecutor** | `agent` | Python 环境 + HTTP 客户端 | OpenAI 兼容接口 + 本地工具循环 | 具备自主读写文件与命令执行能力的 Agent 环境 |
| **ApiExecutor** | `api` | Python 环境 + HTTP 客户端 | 直连 HTTP POST 接口 | 单轮提示词直接输出场景 |
| **StandaloneExecutor** | `standalone` | Python 环境 + HTTP 客户端 | 内置通用 HTTP 客户端 | 轻量免安装模式 |

通过 `Subject Adapter` 机制，评测系统在运行不同宿主时，自动完成「工作区准备 → Skill 规则注入 → 隔离执行 → 产物收集」的标准生命周期管理。
