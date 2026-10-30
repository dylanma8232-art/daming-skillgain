# 📈 daming-skillgain 迭代演进记录 (Changelog)

本文档由自动化工作流与代码治理流水线共同维护，记录项目的每一次优化、重构与功能演进。

---

## [2026-10-30] v2.0.0 通用桌面 Agent 适配与极简报告重构里程碑

- **版本**：v2.0.0（通用 Agent 适配、流程强化与大白话报告全闭环）
- **Release 链接**：https://github.com/dylanma8232-art/daming-skillgain/releases/tag/v2.0.0
- **三大核心改版成果**：
  1. **通用 Agent 深度适配（Codex / Claude Code / Cursor Agent）**：
     - 新增通用 `HostProtocol` 标准抽象基座（探活、非交互传参、stream-json 解析、错误分类）；
     - 深度重构 `codex.py`（支持 `--json` 事件流、`--cd` 工作区隔离、thread_id 追踪）；
     - 深度重构 `claude_code.py`（prompt 走 stdin、`--add-dir`、`--resume`、会话恢复）；
     - 新增 `cursor-agent` 第六宿主适配器，支持无头非交互模式一键唤起。
  2. **四步流水线强化与零卡点一键闭环**：
     - 新增 `evaluator/profile.py` 配置文件机制（`--profile` / `--config`），实现免交互静默一键跑通；
     - 缺参返回结构化 `needs_user_input` 状态码 2，彻底消灭管道环境下 `input()` 卡死；
     - 失败细化为 retryable / fatal 两类，支持中断后 `--resume` 断点续跑与 `--force` 重新渲染。
  3. **大白话极简自包含报告（单 HTML）**：
     - 报告重构为三层解耦（Payload / Template / Renderer），零外链网络依赖（CSS/JS 全内联）；
     - 首屏四件套直击要害：【大字裁决（推荐发布/暂缓/负迁移）+ 核心三指标 + 红绿用例清单 + 有/无对照卡片】；
     - 彻底去除学术统计学术语，用通俗直白语言呈现用例成败原因与对比证据。
- **质量门禁**：全量单元测试 106 passed / 1 skipped（新增 39 例针对宿主与报告的自动化断言，通过率 100%）。

## [2026-10-28] 全局代码健康治理与性能优化重构
- **类型**: `refactor` / `perf` / `fix`
- **治理目标**: 一次性统揽解决 Jules 提出的 17 项代码异味、性能瓶颈与潜在异常风险
- **核心改动项**:
  1. **多余引用清理 (Unused Imports)**:
     - 清理 `evaluator/reporting.py` 中的 `shutil` 与 `contains_untranslated_jargon`
     - 清理 `evaluator/engine.py` 中的 `urllib.request` 与 `urllib.error`
     - 清理 `evaluator/cli.py` 中的 `load_config`
     - 清理 `evaluator/sdk.py` 中的 `interactive_setup`
     - 清理 `evaluator/promise_parser.py` 与 `evaluator/assertions/models.py` 中的 `asdict`
     - 清理 `evaluator/executors/` (api, standalone, codex) 中的 `json`
  2. **循环内正则性能优化 (Precompiled Regex)**:
     - 预编译 `analyzer.py` 中的 `DECISION_PATTERNS`、`ASSUMPTION_PATTERNS`、`ERROR_RECOVERY_PATTERNS` 以及 `ERROR_PATTERNS`
     - 预编译 `content_validator.py` 中的 Markdown 列表项与章节标题正则
     - 移除 `reporting.py` 嵌套循环中的局部 `import re`，提升文本处理与评测解析性能
  3. **异步端点阻塞修复 (Async Non-blocking)**:
     - 在 `api_server.py` (`/api/evaluate`, `/api/analyze`, `/api/models`) 中采用 `asyncio.to_thread` 调度同步 CPU/IO 调用，防止阻塞事件循环
     - 在 `mcp_server.py` (`evaluate_skill`, `analyze_skill`, `check_connectivity`) 中采用 `asyncio.to_thread` 调度
  4. **异常捕获与健壮性提升**:
     - 在 `evaluator/assertions/exact_match.py` 的 `extract_regex` 逻辑中完善异常日志与 `re.error` 校验，消除静默吞异常问题
     - 兼容缺少 `fastapi` 及 `pytest-mock` 运行环境下的测试用例执行 (84 个单测全部 100% 绿色通过)

---

## [2026-10-28] 自动化 PR 归档合入
- **PR #35**: 🧹 [Code Health] Remove unused yaml import in engine.py
- **PR #34**: 🧹 [Code Health] Remove unused `sys` import and redundant `stream` arg in logger
- **PR #33**: 🧹 [code health improvement] Remove unused json import from api_server.py
- **PR #32**: ⚡ [performance improvement] Precompile regexes in promise_parser
- **PR #31**: ⚡ Optimize AGENTS.md reading with mtime cache in CodexSubjectAdapter
- **PR #30**: ⚡ perf(evaluator): Optimize regex compilation in engine.py
- **PR #29**: 🧪 Add tests for api_server run_server function
- **PR #28**: 🧪 add unit tests for recomputable assertions
- **PR #27**: ⚡ Optimize check_phase_completion file scanning and string matching
- **PR #26**: ⚡ perf: optimize json extraction fallback candidate loop
- **PR #25**: ⚡ Optimize redundant regex in detect_stuck_state

## 2026-10-29 02:18 迭代

- **PR**：36 ci: add weekly optimization workflow
- **链接**：https://github.com/dylanma8232-art/daming-skillgain/pull/36
- **改动文件**：
- .github/workflows/weekly-optimization.yml

## 2026-10-30 03:51 迭代

- **PR**：45 fix: use _compact_text in report_payload.py
- **链接**：https://github.com/dylanma8232-art/daming-skillgain/pull/45
- **改动文件**：
- evaluator/report_payload.py
- tests/test_report_self_contained.py

## 2026-10-30 03:51 迭代

- **PR**：46 Retain constructor executor configuration in SkillEvaluator.run()
- **链接**：https://github.com/dylanma8232-art/daming-skillgain/pull/46
- **改动文件**：
- evaluator/sdk.py
- tests/test_sdk.py

## 2026-10-30 03:51 迭代

- **PR**：47 Fix workflow host executor resolution when instantiated without executor
- **链接**：https://github.com/dylanma8232-art/daming-skillgain/pull/47
- **改动文件**：
- evaluator/workflow.py
- tests/test_workflow_host_resolution.py

## 2026-10-30 03:52 迭代

- **PR**：48 Fix auto-merge-jules workflow security vulnerability
- **链接**：https://github.com/dylanma8232-art/daming-skillgain/pull/48
- **改动文件**：
- .github/workflows/auto-merge-jules.yml
- tests/test_auto_merge_workflow.py

## 2026-10-30 03:52 迭代

- **PR**：49 Allocate atomically unique run directory for concurrent evaluations
- **链接**：https://github.com/dylanma8232-art/daming-skillgain/pull/49
- **改动文件**：
- evaluator/engine.py
- tests/test_api_server.py

## 2026-10-30 06:23 迭代

- **PR**：50 Fix API server security: bind 127.0.0.1, enforce auth, drop unsafe parameters
- **链接**：https://github.com/dylanma8232-art/daming-skillgain/pull/50
- **改动文件**：
- .github/workflows/auto-merge-jules.yml
- evaluator/api_server.py
- requirements.txt
- tests/test_api_server.py
- tests/test_api_server_startup.py
