# 📈 daming-skillgain 迭代演进记录 (Changelog)

本文档由自动化工作流与代码治理流水线共同维护，记录项目的每一次优化、重构与功能演进。

---

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
