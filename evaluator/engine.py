from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import zipfile
from threading import Lock
import urllib.error
import urllib.request
import yaml
from dataclasses import dataclass
from hashlib import sha256
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .analyzer import write_run_summary
from .models import ExecutionMetrics
from .io_utils import ensure_directory, write_json, write_text
from .models import EvaluationConfig, ExperimentPlan, ExperimentResult, RunnerConfig
from .reporting import generate_run_report, generate_anonymous_report
from .result_files import select_result_files, write_result_selection

LOGGER = logging.getLogger("skill_evaluator.engine")

_OUTPUT_PATTERNS = [
    re.compile(r"输出[：:\s]+([^\n]+)"),
    re.compile(r"生成[：:\s]+([^\n]+)"),
    re.compile(r"产出[：:\s]+([^\n]+)"),
    re.compile(r"报告[：:\s]+([^\n]+)"),
    re.compile(r"文件[：:\s]+([^\n]+)"),
]

_FILE_PATTERNS = [
    re.compile(r"(\.(?:md|xlsx|xls|csv|json|html|txt|sql|py|js|ts))", re.IGNORECASE),
]

_ENTRY_PATTERNS = [
    re.compile(r"(?:入口|执行方式)[：:\-]*\s*```[^\n]*\n([^\n]+)"),
    re.compile(r"(?:命令|运行)[：:\-]*\s*(python[^\n]+)"),
]

_PHASE_PATTERN = re.compile(r"(?:Phase|阶段|步骤|Step)\s*(\d+)[：:\-\s]+([^\n]{5,80})")

_CONSTRAINT_KEYWORDS = ["必须", "禁止", "不得", "不能", "确保", "限制", "强制", "一定"]
_CONSTRAINT_PATTERN = re.compile(rf"({'|'.join(_CONSTRAINT_KEYWORDS)})[^\n]{{2,100}}")


def resolve_config_host(config: EvaluationConfig) -> str | None:
    """解析配置中的宿主：显式 host 优先，否则按 execution_mode 默认映射（P2 解耦）。"""
    return str(getattr(config, "host", "") or "") or None


def resolve_config_host_config(config: EvaluationConfig) -> dict[str, Any]:
    """解析配置中的宿主专属配置（如 script 宿主的宿主脚本路径）。"""
    return dict(getattr(config, "host_config", {}) or {})


class RunProgress:
    """Persist and print lightweight progress for long evaluator runs."""

    def __init__(self, run_dir: Path, interval_seconds: float = 5.0) -> None:
        self.run_dir = run_dir
        self.interval_seconds = max(1.0, float(interval_seconds or 5.0))
        self.progress_path = run_dir / "progress.json"
        self.events_path = run_dir / "progress.ndjson"
        self.lock = Lock()
        self.stage = "init"
        self.stage_started_at = time.time()
        self.total = 0
        self.tasks: dict[str, dict[str, Any]] = {}
        self.last_print_at = 0.0

    def start_stage(self, stage: str, total: int) -> None:
        with self.lock:
            self.stage = stage
            self.stage_started_at = time.time()
            self.total = total
            self.tasks = {}
            self._append_event_locked(
                {"event": "stage_started", "stage": stage, "total": total}
            )
            self._write_locked()
            self._print_locked(force=True)

    def task_started(self, task_id: str, label: str, **extra: Any) -> None:
        with self.lock:
            self.tasks[task_id] = {
                "id": task_id,
                "label": label,
                "status": "running",
                "started_at": datetime.now().isoformat(),
                "start_ts": time.time(),
                **extra,
            }
            self._append_event_locked(
                {
                    "event": "task_started",
                    "stage": self.stage,
                    "task": self.tasks[task_id],
                }
            )
            self._write_locked()
            self._print_locked()

    def task_finished(self, task_id: str, success: bool, **extra: Any) -> None:
        with self.lock:
            task = self.tasks.get(
                task_id, {"id": task_id, "label": task_id, "start_ts": time.time()}
            )
            duration = round(
                time.time() - float(task.get("start_ts") or time.time()), 1
            )
            task.update(
                {
                    "status": "completed" if success else "failed",
                    "success": success,
                    "duration_seconds": duration,
                    "finished_at": datetime.now().isoformat(),
                    **extra,
                }
            )
            self.tasks[task_id] = task
            self._append_event_locked(
                {"event": "task_finished", "stage": self.stage, "task": task}
            )
            self._write_locked()
            self._print_locked(force=True)

    def task_completed(self, task_id: str, success: bool, **extra: Any) -> None:
        """Mark a task as already completed (for resume without re-execution)."""
        with self.lock:
            model = extra.get("model", "")
            mode = extra.get("mode", "")
            label = f"{model} {mode}" if model and mode else task_id
            wall_clock = extra.get("wall_clock_seconds", 0)
            task = {
                "id": task_id,
                "label": label,
                "status": "completed" if success else "failed",
                "success": success,
                "started_at": datetime.now().isoformat(),
                "finished_at": datetime.now().isoformat(),
                "duration_seconds": wall_clock,
                **{
                    k: v
                    for k, v in extra.items()
                    if k not in ("model", "mode", "wall_clock_seconds")
                },
            }
            self.tasks[task_id] = task
            self._append_event_locked(
                {"event": "task_skipped_existing", "stage": self.stage, "task": task}
            )
            self._write_locked()
            self._print_locked(force=True)

    def finish(self) -> None:
        with self.lock:
            self._append_event_locked({"event": "run_finished", "stage": self.stage})
            self._write_locked()
            self._print_locked(force=True)

    def _counts_locked(self) -> dict[str, int]:
        values = list(self.tasks.values())
        completed = sum(1 for task in values if task.get("status") == "completed")
        failed = sum(1 for task in values if task.get("status") == "failed")
        running = sum(1 for task in values if task.get("status") == "running")
        queued = max(self.total - completed - failed - running, 0)
        return {
            "completed": completed,
            "failed": failed,
            "running": running,
            "queued": queued,
        }

    def _snapshot_locked(self) -> dict[str, Any]:
        counts = self._counts_locked()
        elapsed = round(time.time() - self.stage_started_at, 1)
        done = counts["completed"] + counts["failed"]
        eta = None
        if done > 0 and self.total > done:
            eta = round((elapsed / done) * (self.total - done), 1)
        public_tasks = []
        for task in self.tasks.values():
            item = {k: v for k, v in task.items() if k != "start_ts"}
            if task.get("status") == "running":
                item["elapsed_seconds"] = round(
                    time.time() - float(task.get("start_ts") or time.time()), 1
                )
            public_tasks.append(item)
        return {
            "stage": self.stage,
            "total": self.total,
            "counts": counts,
            "elapsed_seconds": elapsed,
            "eta_seconds": eta,
            "updated_at": datetime.now().isoformat(),
            "tasks": public_tasks,
        }

    def _write_locked(self) -> None:
        ensure_directory(self.run_dir)
        write_json(self.progress_path, self._snapshot_locked())

    def _append_event_locked(self, event: dict[str, Any]) -> None:
        ensure_directory(self.run_dir)
        event = {"timestamp": datetime.now().isoformat(), **event}
        with self.events_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _print_locked(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_print_at < self.interval_seconds:
            return
        self.last_print_at = now
        snapshot = self._snapshot_locked()
        counts = snapshot["counts"]
        done = counts["completed"] + counts["failed"]
        eta = snapshot["eta_seconds"]
        eta_text = f" | 预计剩余 {eta:.0f}s" if isinstance(eta, (int, float)) else ""
        print(
            f"  [{self.stage}] {done}/{self.total} 完成 | "
            f"运行中 {counts['running']} | 失败 {counts['failed']} | "
            f"用时 {snapshot['elapsed_seconds']:.0f}s{eta_text}",
            flush=True,
        )
        running = [
            task for task in snapshot["tasks"] if task.get("status") == "running"
        ]
        if running:
            labels = ", ".join(
                f"{task.get('label')} {task.get('elapsed_seconds', 0):.0f}s"
                for task in running[:4]
            )
            print(f"    running: {labels}", flush=True)


def _wait_for_artifacts_settle(artifacts_dir: Path, max_seconds: float = 2.0) -> None:
    """Short settle wait that stops early once artifact file sizes stop changing."""
    start = time.time()
    last_signature: tuple[tuple[str, int], ...] | None = None
    stable_seen = 0
    while time.time() - start < max_seconds:
        files = (
            sorted(item for item in artifacts_dir.rglob("*") if item.is_file())
            if artifacts_dir.exists()
            else []
        )
        signature = tuple(
            (str(item.relative_to(artifacts_dir)), item.stat().st_size)
            for item in files
        )
        if signature and signature == last_signature:
            stable_seen += 1
            if stable_seen >= 2:
                return
        else:
            stable_seen = 0
        last_signature = signature
        time.sleep(0.25)


# ==========================================================================
# P2 通用化 + P3 弃用清理: OpenCode 专用传输层唯一归属 evaluator/executors/opencode.py，
# 主链所有宿主调用经 evaluator/host_session.py 分发到 execute_session 统一契约。
# 兼容别名已全部删除。
# ==========================================================================

# ========== 模型连通性测试 ==========

_MIN_CONNECT_TEST_PROMPT = "回答一个字：好"

from .host_session import (
    EXECUTION_MODE_DEFAULT_HOST,
    resolve_host,
    host_capabilities,
    require_host_capability,
    run_host_session,
    run_host_text,
)
from .executors import UnsupportedCapabilityError
from .subject_adapter import get_subject_adapter

# get_opencode_models: opencode 宿主域内模型发现，CLI 交互选型仍在使用（P3 保留项，
# 见弃用清单）；宿主专用实现唯一归属 executors/opencode.py
from .executors.opencode import get_opencode_models  # noqa: F401


def run_host_experiment_session(
    *,
    model: str,
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
    execution_mode: str = "cli-attach",
    format_json: bool = True,
    dangerous_skip_permissions: bool = False,
    host: str | None = None,
    host_config: dict[str, Any] | None = None,
    files: list[str] | None = None,
) -> tuple[int, str, str, str, dict[str, Any]]:
    """执行实验的统一宿主会话入口（五元组兼容形态）。

    Returns:
        (exit_code, stdout_text, stderr_text, transcript_text, usage_info)
    """
    session = run_host_session(
        host=host,
        model=model,
        prompt=prompt,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        execution_mode=execution_mode,
        format_json=format_json,
        dangerous_skip_permissions=dangerous_skip_permissions,
        files=files,
        host_config=host_config,
    )
    transcript = session.transcript or session.stdout
    text = str(getattr(session, "text", "") or "")
    if text and text not in transcript:
        # script 契约宿主等把模型最终回答放在 text 字段；保证它落盘进入判定与报告视野
        transcript = (transcript + "\n\n" + text) if transcript else text
    return (
        session.exit_code,
        session.stdout,
        session.stderr,
        transcript,
        dict(session.usage or {}),
    )


def test_model_connectivity(
    models: list[str],
    execution_mode: str = "cli-attach",
    timeout: int = 60,
    host: str | None = None,
    host_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对选中的模型执行连通性测试，确保每个模型可以正常调用。

    P2: 经统一宿主调度层分发，不再绑定 OpenCode；测试工作区使用临时目录。

    Returns:
        {
            "passed": [model1, model2, ...],
            "failed": {model: error_message, ...},
            "all_ok": bool,
            "details": [{"model": str, "status": str, "latency_ms": float, "error": str | None}]
        }
    """
    result: dict[str, Any] = {"passed": [], "failed": {}, "all_ok": True, "details": []}

    with tempfile.TemporaryDirectory(prefix="eval_connect_") as test_dir:
        for model in models:
            detail = {
                "model": model,
                "status": "unknown",
                "latency_ms": 0.0,
                "error": None,
            }
            start = time.time()
            try:
                session = run_host_session(
                    host=host,
                    model=model,
                    prompt=_MIN_CONNECT_TEST_PROMPT,
                    cwd=Path(test_dir),
                    timeout_seconds=timeout,
                    execution_mode=execution_mode,
                    format_json=True,
                    host_config=host_config,
                )
                if session.exit_code == 0 and (session.text or session.stdout).strip():
                    detail["status"] = "ok"
                else:
                    detail["status"] = "failed"
                    detail["error"] = (session.stderr or session.stdout).strip()[
                        :300
                    ] or "无输出"
            except subprocess.TimeoutExpired:
                detail["status"] = "timeout"
                detail["error"] = f"连通测试超时（{timeout}s）"
            except Exception as e:
                detail["status"] = "error"
                detail["error"] = f"{type(e).__name__}: {e}"

            detail["latency_ms"] = round((time.time() - start) * 1000, 0)
            result["details"].append(detail)

            if detail["status"] == "ok":
                result["passed"].append(model)
            else:
                result["failed"][model] = detail["error"]
                result["all_ok"] = False

    return result


def print_connectivity_report(report: dict[str, Any]) -> None:
    """打印连通性测试报告到控制台。"""
    total = len(report["details"])
    passed = len(report["passed"])
    failed = len(report["failed"])

    print(f"\n{'='*60}")
    print(f"🔗 模型连通性测试报告 ({total} 个模型)")
    print(f"{'='*60}")

    for detail in report["details"]:
        model = detail["model"]
        status = detail["status"]
        latency = detail["latency_ms"]
        error = detail.get("error")

        if status == "ok":
            icon = "✅"
        elif status == "timeout":
            icon = "⏱️"
        else:
            icon = "❌"

        print(f"  {icon} {model}  ({latency:.0f}ms)")
        if error:
            print(f"     错误: {error}")

    print(f"\n  通过: {passed}  失败: {failed}")
    if report["all_ok"]:
        print("  ✅ 所有模型均可正常调用")
    else:
        print("  ❌ 存在不可用模型，请检查配置后重试")
    print(f"{'='*60}\n")


# ========== Token 提取 ==========


def _extract_tokens_from_output_common(
    stdout_text: str, stderr_text: str
) -> dict[str, int] | None:
    combined = stdout_text + "\n" + stderr_text

    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    api_call_count = 0

    prompt_match = re.search(r"prompt[_\s]?tokens[^\d]*(\d+)", combined, re.IGNORECASE)
    if prompt_match:
        prompt_tokens = int(prompt_match.group(1))

    completion_match = re.search(
        r"completion[_\s]?tokens[^\d]*(\d+)", combined, re.IGNORECASE
    )
    if completion_match:
        completion_tokens = int(completion_match.group(1))

    total_match = re.search(
        r"(?:total[_\s]?tokens)[^\d]*(\d+)", combined, re.IGNORECASE
    )
    if total_match:
        total_tokens = int(total_match.group(1))

    count_match = re.search(
        r"(?:api[_\s]?call|calls)[^\d]*(\d+)", combined, re.IGNORECASE
    )
    if count_match:
        api_call_count = int(count_match.group(1))

    if not total_tokens and (prompt_tokens or completion_tokens):
        total_tokens = prompt_tokens + completion_tokens

    if total_tokens > 0:
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "api_call_count": max(1, api_call_count),
        }
    return None


# ========== 模型交互模块 ==========

# P2: get_opencode_models 已迁至 evaluator/executors/opencode.py（宿主专用模型发现），
# 本模块顶部经 host_session 兼容别名重新导入。主链新代码不应再直接引用。


def select_models_interactive(
    available_models: list[str],
    mode: str = "explicit",
    default_selected: list[str] | None = None,
    auto_detect: bool = True,
) -> list[str]:
    """
    交互式选择模型

    Args:
        available_models: 可选的模型列表
        mode: 'explicit'（明投，显示真实名称）或 'blind'（盲评，匿名化）
        default_selected: 默认选中的模型列表
        auto_detect: 是否自动尝试从 opencode 获取模型列表

    Returns:
        用户选择的模型列表
    """
    if default_selected is None:
        default_selected = []

    # 如果可用模型列表为空，尝试自动检测
    if not available_models and auto_detect:
        detected = get_opencode_models()
        if detected:
            available_models = detected
            print(f"\n🔍 自动检测到 {len(available_models)} 个 opencode 模型")

    if not available_models:
        print("\n⚠️  未提供可用模型列表，请手动输入模型名称（逗号分隔）")
        try:
            answer = input("模型列表: ").strip()
            if answer:
                available_models = [m.strip() for m in answer.split(",") if m.strip()]
        except EOFError:
            pass

    # 匿名映射（盲评模式）
    anonymous_map: dict[str, str] = {}
    if mode == "blind":
        for i, model in enumerate(available_models):
            anonymous_map[f"模型{chr(65 + i)}"] = model

    display_models = list(anonymous_map.keys()) if mode == "blind" else available_models

    print(f"\n{'='*60}")
    if mode == "blind":
        print("🎯 盲评模式：模型名称已匿名化")
    else:
        print("📋 明投模式：选择要测试的模型")
    print(f"{'='*60}")
    print("可用模型:")
    for i, model in enumerate(display_models):
        marker = ""
        if default_selected:
            check_model = anonymous_map.get(model, model) if mode == "blind" else model
            if check_model in default_selected:
                marker = " [默认]"
        print(f"  {i+1}. {model}{marker}")
    print("")
    print("输入编号或多选（如: 1,3,5 或 1-3）")
    print(f"{'='*60}")

    selected = []
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            answer = input("您的选择: ").strip()
        except EOFError:
            answer = ""

        if not answer:
            if attempt < max_attempts - 1:
                print("⚠️  必须明确选择模型，请输入编号")
                continue
            else:
                print("❌ 未做出有效选择，退出")
                return []

        selected = parse_selection(answer, display_models)
        if selected:
            break
        else:
            if attempt < max_attempts - 1:
                print("⚠️  无效选择，请重新输入")
                continue
            else:
                print("❌ 未做出有效选择，退出")
                return []

    if mode == "blind":
        real_models = [anonymous_map.get(m, m) for m in selected]
        return real_models

    return selected


def parse_selection(answer: str, options: list[str]) -> list[str]:
    """解析用户输入的选择"""
    selected = []
    parts = answer.split(",")

    for part in parts:
        part = part.strip()
        if "-" in part:
            try:
                start, end = part.split("-")
                start_idx = int(start.strip()) - 1
                end_idx = int(end.strip()) - 1
                for i in range(max(0, start_idx), min(len(options), end_idx + 1)):
                    if options[i] not in selected:
                        selected.append(options[i])
            except ValueError:
                pass
        else:
            try:
                idx = int(part) - 1
                if 0 <= idx < len(options) and options[idx] not in selected:
                    selected.append(options[idx])
            except ValueError:
                pass

    return selected


def select_models_for_reproduction(
    voted_models: list[str],
    anonymous_map: dict[str, str] | None = None,
) -> list[str]:
    # 如果有匿名映射，显示匿名名称
    display_models = []
    reverse_map = {}
    if anonymous_map:
        for anon, real in anonymous_map.items():
            display_models.append(anon)
            reverse_map[anon] = real
    else:
        display_models = voted_models

    print(f"\n{'='*60}")
    print("🔄 盲评复现环节")
    print(f"{'='*60}")
    print("已评审的模型:")
    for i, model in enumerate(display_models):
        print(f"  {i+1}. {model}")
    print("")
    print("输入编号选择要复现评审的模型（如: 1,3 或 1-2）")
    print(f"{'='*60}")

    try:
        answer = input("复现选择: ").strip()
    except EOFError:
        answer = ""

    if not answer:
        return voted_models

    selected = parse_selection(answer, display_models)

    if anonymous_map:
        return [reverse_map.get(m, m) for m in selected]

    return selected


def _classify_execution(
    exit_code: int | None,
    stdout_text: str,
    stderr_text: str,
    artifact_files: list[str],
) -> tuple[bool, str, str | None]:
    normalized_stderr = stderr_text.lower()
    normalized_stdout = stdout_text.lower()
    combined_output = f"{normalized_stdout}\n{normalized_stderr}"

    provider_failure_keywords = [
        "unable to connect",
        "was there a typo in the url or port",
        "failed to fetch models.dev",
        "cannot access the url",
        "insufficient_quota",
        "rate limit",
        "connection refused",
        "network error",
        "socket hang up",
        "econnrefused",
    ]
    fatal_error_keywords = [
        "error:",
        "traceback",
        "timed out",
    ]
    binary_warning_keywords = [
        "cannot read binary file",
    ]

    # 纪律 2：基础设施错误 / 超时 / 网络标 invalid（不计入能力评测分母）
    if any(keyword in combined_output for keyword in provider_failure_keywords):
        return False, "invalid", "provider_or_network_error"

    if exit_code == 124 or "timed out" in combined_output:
        return False, "invalid", "command_timeout"

    if exit_code not in (0, None):
        return False, "invalid", f"non_zero_exit_code_{exit_code}"

    fatal_error_without_binary_only = any(
        keyword in combined_output for keyword in fatal_error_keywords
    ) and not any(keyword in combined_output for keyword in binary_warning_keywords)
    if fatal_error_without_binary_only:
        return False, "invalid", "fatal_stderr_error"

    # 纪律 1：空产物计为失败（empty product = failure，不再只是跳过盲评或标记成功）
    if not artifact_files:
        return False, "failure", "empty_product"

    # 产物存在且基础设施正常
    return True, "completed", None


class EvaluationEngine:
    def __init__(self, config: EvaluationConfig, config_path: Path) -> None:
        self.config = config
        self.config_path = config_path.resolve()
        self.config_dir = self.config_path.parent
        self.target_skill_path = (
            self.config_dir / self.config.target_skill_path
        ).resolve()
        self.workspace_root = (self.config_dir / self.config.workspace_root).resolve()
        self.input_files = [
            (self.config_dir / input_file).resolve()
            for input_file in self.config.input_files
        ]
        self.skill_type = self._detect_skill_type()

    def _detect_skill_type(self) -> str:
        """读取 skill 的 SKILL.md 自动判断 skill 类型"""
        skill_md = self.target_skill_path / "SKILL.md"
        if not skill_md.exists():
            return "unknown"
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace").lower()
        except Exception:
            return "unknown"

        data_keywords = [
            "数据",
            "分析",
            "excel",
            "csv",
            "xlsx",
            "dataset",
            "报表",
            "指标",
            "统计",
            "可视化",
            "chart",
            "graph",
            "plot",
        ]
        code_keywords = [
            "代码",
            "code",
            "编程",
            "函数",
            "function",
            "api",
            "开发",
            "debug",
            "重构",
            "refactor",
            "test",
            "测试",
        ]
        ui_keywords = [
            "ui",
            "界面",
            "页面",
            "页面",
            "css",
            "html",
            "前端",
            "frontend",
            "组件",
            "component",
            "布局",
            "layout",
        ]
        doc_keywords = [
            "文档",
            "doc",
            "文档",
            "写作",
            "write",
            "报告",
            "report",
            "总结",
            "summary",
        ]

        data_score = sum(1 for kw in data_keywords if kw in content)
        code_score = sum(1 for kw in code_keywords if kw in content)
        ui_score = sum(1 for kw in ui_keywords if kw in content)
        doc_score = sum(1 for kw in doc_keywords if kw in content)

        scores = {
            "data_analysis": data_score,
            "code_generation": code_score,
            "ui_building": ui_score,
            "documentation": doc_score,
        }
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "unknown"

    def create_run_directory(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = self.workspace_root / f"{timestamp}-{self.config.evaluation_name}"
        ensure_directory(run_dir)
        ensure_directory(run_dir / "experiments")
        LOGGER.info("创建评测运行目录: run_dir=%s", run_dir)
        return run_dir

    def build_plans(self, run_dir: Path) -> list[tuple[RunnerConfig, ExperimentPlan]]:
        plans: list[tuple[RunnerConfig, ExperimentPlan]] = []
        for runner in self.config.runners:
            if not runner.enabled:
                LOGGER.info("跳过未启用执行器: runner=%s", runner.name)
                continue
            for model in self.config.models:
                for use_skill in self.config.use_skill_modes:
                    for repetition in range(1, self.config.repetitions + 1):
                        safe_model = self.slugify(model)
                        rep_suffix = (
                            f"__rep{repetition}" if self.config.repetitions > 1 else ""
                        )
                        case_name = (
                            f"{runner.name}__{safe_model}__"
                            f"{'with-skill' if use_skill else 'without-skill'}{rep_suffix}"
                        )
                        workspace_dir = run_dir / "experiments" / case_name
                        artifacts_dir = workspace_dir / "artifacts"
                        prompt_text = self.build_prompt(
                            model=model,
                            runner_name=runner.name,
                            use_skill=use_skill,
                            workspace_dir=str(workspace_dir),
                        )
                        plan = ExperimentPlan(
                            runner_name=runner.name,
                            model=model,
                            use_skill=use_skill,
                            repetition=repetition,
                            prompt_text=prompt_text,
                            input_files=self.input_files,
                            workspace_dir=workspace_dir,
                            artifacts_dir=artifacts_dir,
                            prompt_file=workspace_dir / "prompt.md",
                            transcript_file=workspace_dir / "transcript.md",
                            stdout_file=workspace_dir / "stdout.txt",
                            stderr_file=workspace_dir / "stderr.txt",
                            metadata_file=workspace_dir / "metadata.json",
                            command_file=workspace_dir / "command.txt",
                        )
                        plans.append((runner, plan))
        LOGGER.info(
            "生成实验计划完成: count=%s, repetitions=%s",
            len(plans),
            self.config.repetitions,
        )
        return plans

    def render_command(self, runner: RunnerConfig, plan: ExperimentPlan) -> str:
        # opencode CLI 不支持 --skill 参数，skill 通过 .opencode/skills/ 符号链接自动加载
        skill_flag = ""
        input_file_flags = ""
        if self.config.attach_input_files:
            input_file_flags = ""
            # 注释掉 --file 参数，避免二进制文件传递失败
            # 文件已复制到工作区，模型可直接读取
            # input_file_flags = " ".join(
            #     f"--file {shlex.quote(str(input_file))}"
            #     for input_file in plan.input_files
            # )
        command = runner.command_template.format(
            model=shlex.quote(plan.model),
            prompt_arg=shlex.quote(plan.prompt_text),
            task_prompt_file=shlex.quote(str(plan.prompt_file)),
            target_skill=shlex.quote(self.config.target_skill),
            target_skill_path=shlex.quote(str(self.target_skill_path)),
            workspace_dir=shlex.quote(str(plan.workspace_dir)),
            transcript_file=shlex.quote(str(plan.transcript_file)),
            artifacts_dir=shlex.quote(str(plan.artifacts_dir)),
            input_file_flags=input_file_flags,
            skill_flag=skill_flag,
        )
        command = re.sub(r"\s+", " ", command).strip()
        command = f"cd {shlex.quote(str(plan.workspace_dir))} && {command}"
        LOGGER.info(
            "构建执行命令: runner=%s, model=%s, use_skill=%s",
            runner.name,
            plan.model,
            plan.use_skill,
        )
        return command

    def materialize_plan(
        self, plan: ExperimentPlan, runner: RunnerConfig, run_dir: Path
    ) -> str:
        ensure_directory(plan.workspace_dir)
        ensure_directory(plan.artifacts_dir)
        self.prepare_workspace_skill(plan)

        # P2: 宿主专用工作区预处理（OpenCode 专属的 opencode.json / 依赖目录清理）
        # 仅在宿主确为 opencode 时执行；其他宿主不写入 opencode 专属文件。
        config_host = resolve_config_host(
            self.config
        ) or EXECUTION_MODE_DEFAULT_HOST.get(
            str(self.config.execution_mode or ""), "opencode"
        )
        if config_host == "opencode":
            opencode_dir = plan.workspace_dir / ".opencode"
            for item in [
                "node_modules",
                "bun.lock",
                "package.json",
                "package-lock.json",
            ]:
                path = opencode_dir / item
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink()

            git_dir = plan.workspace_dir / ".git"
            if not git_dir.exists():
                git_dir.mkdir(parents=True, exist_ok=True)
                (git_dir / "config").write_text(
                    "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
                )

            target_skill_name = self.config.target_skill.strip()
            skill_permissions = (
                {target_skill_name: "allow"} if target_skill_name else {}
            )
            skill_permissions["daming-skillgain"] = "deny"

            opencode_config = {
                "permission": {
                    "skill": skill_permissions,
                }
            }
            opencode_json_path = plan.workspace_dir / "opencode.json"
            try:
                write_json(opencode_json_path, opencode_config)
                LOGGER.info("已写入 opencode.json: %s", opencode_json_path)
            except Exception as e:
                LOGGER.warning("写入 opencode.json 失败: %s", e)
        else:
            git_dir = plan.workspace_dir / ".git"
            if not git_dir.exists():
                git_dir.mkdir(parents=True, exist_ok=True)
                (git_dir / "config").write_text(
                    "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
                )

        for input_file in plan.input_files:
            if input_file.exists():
                dest = plan.workspace_dir / input_file.name
                if not dest.exists():
                    shutil.copy2(input_file, dest)
                    LOGGER.info("复制输入文件到工作区: %s -> %s", input_file, dest)

        command = self.render_command(runner, plan)

        write_text(plan.prompt_file, plan.prompt_text)
        write_text(plan.command_file, command)
        write_json(
            plan.metadata_file,
            {
                "runner": runner.name,
                "model": plan.model,
                "use_skill": plan.use_skill,
                "target_skill": self.config.target_skill,
                "target_skill_path": str(self.target_skill_path),
                "input_files": [str(item) for item in plan.input_files],
                "input_file_checksums": self.build_input_file_checksums(
                    plan.input_files
                ),
                "workspace_skill_dir": self.get_workspace_skill_dir(plan),
                "run_dir": str(run_dir),
                "workspace_dir": str(plan.workspace_dir),
                "skill_description": self.config.evaluation_contract.get(
                    "primary_goal", ""
                )[:200],
                "preflight": self.config.preflight,
                "resource_requirements": self.config.resource_requirements,
                # 注意：evaluation_contract 不写入工作区 metadata，
                # 避免模型读取后"表演"出期望行为。它只保存在评测器运行目录中。
            },
        )
        LOGGER.info("实验目录已落盘: workspace=%s", plan.workspace_dir)
        return command

    def build_prompt(
        self, model: str, runner_name: str, use_skill: bool, workspace_dir: str = ""
    ) -> str:
        # 纪律 4：without-skill 隔离验证：提示词严格对称，无任何 skill 泄露
        skill_header_lines = (
            [f"目标 skill: {self.config.target_skill}"] if use_skill else []
        )
        interactive_instruction = (
            (
                "## 交互式处理\n"
                "如果你使用的 skill 包含交互式确认步骤（如'请确认'、'询问用户'、'请选择'等），"
                "你必须自主扮演用户角色，根据当前任务上下文做出合理判断和回答，"
                "不能停下来等待真实用户输入。对于不确定的确认，使用保守默认值并记录。"
            )
            if use_skill
            else ""
        )
        input_file_lines = [f"- {item.name}" for item in self.input_files] or [
            "- （无输入文件）"
        ]
        user_task_lines = [self.config.task_prompt]
        cwd_instruction = ""
        if workspace_dir:
            cwd_instruction = "\n".join(
                [
                    "## 重要：当前工作目录",
                    f"你当前的工作目录是：{workspace_dir}",
                    "所有文件操作（读取输入、写入输出）都必须基于当前工作目录。",
                    "**绝对不要使用原始数据的绝对路径（如 /home/user/dataset/ 之类的宿主机专属路径）**。",
                    "",
                ]
            )
        return "\n".join(
            [
                "# Evaluation Task",
                "",
                *skill_header_lines,
                f"执行器: {runner_name}",
                f"基础模型: {model}",
                f"评测模式: {'with-skill' if use_skill else 'standard-baseline'}",
                "",
                *([interactive_instruction, ""] if interactive_instruction else []),
                cwd_instruction,
                "",
                "## 输入文件",
                "以下文件已复制到当前工作目录，可直接读取：",
                *input_file_lines,
                "",
                "## 自动化评测边界",
                "1. 这是自动化评测环境，运行期间没有真实用户可以继续回答问题；不要在结尾反问用户，也不要要求用户补充数据后再继续。",
                "2. 不要把核心任务委托给后台任务、deep task、sub-agent 或异步队列；必须在当前 OpenCode run 中完成可完成的工作。",
                "3. 如果当前目录缺少完成任务所必需的数据文件或凭证，不要编造最终分析结论；请在 artifacts/summary.md 和 artifacts/execution_trace.md 中明确写出阻塞原因，并标记任务未完成。",
                "4. 如果存在 skill_resources.json 或 preflight.json，先读取它们，按其中的资源状态使用输入文件或记录 synthetic/mock 数据说明。",
                "",
                "## 用户任务",
                *user_task_lines,
                "",
                "## 要求",
                "1. 自主完成任务流程。",
                "2. 将关键产出保存到 artifacts 目录中（文件类型和内容由你自行决定）。**必须使用当前工作目录下的相对路径 artifacts/，不要使用绝对路径如 ~/artifacts/ 或 /Users/.../artifacts/**。",
                "3. 任务完成后，必须在当前工作目录下的 artifacts/summary.md 文件中创建 summary.md，简要说明你产出了什么、包含哪些关键内容。**路径必须是 artifacts/summary.md（相对路径）**。",
                "4. 必须额外保存一份执行留痕文件，记录关键步骤、默认假设和产物对应关系。",
                '5. 必须创建 artifacts/result_manifest.json，声明你交付给用户验收的最终结果文件；summary、execution_trace、日志、脚本和中间素材不能声明为结果文件。JSON 格式：{"result_files":[{"path":"artifacts/最终结果文件.ext","role":"primary","description":"为什么这是用户要验收的最终交付物"}],"evidence_files":["artifacts/summary.md","artifacts/execution_trace.md"]}。',
                "6. 在结束时明确说明任务是否完成。",
            ]
        )

    def prepare_workspace_skill(self, plan: ExperimentPlan) -> None:
        """按宿主规则注入被测 skill 到实验工作区（P2: 经 Subject Adapter 参数化）。

        with-skill: 用宿主对应的 Subject Adapter 注入，并立即做加载验证（防假对照）。
        without-skill: 严格隔离，确保宿主 skills 目录无任何残留。
        """
        workspace_skill_dir_value = self.get_workspace_skill_dir(plan)
        config_host = resolve_config_host(
            self.config
        ) or EXECUTION_MODE_DEFAULT_HOST.get(
            str(self.config.execution_mode or ""), "opencode"
        )
        if not plan.use_skill or not self.config.target_skill.strip():
            # 纪律 4：严格隔离验证：without-skill 工作区必须确保无任何 skill 目录残留
            try:
                adapter = get_subject_adapter(config_host)
                adapter.cleanup_skill(plan.workspace_dir, self.config.target_skill)
            except Exception:
                # 未知宿主回退：清理通用 skills 目录形态
                for rel in (".opencode/skills", ".claude/skills", ".skills"):
                    p = plan.workspace_dir / rel
                    if p.exists():
                        shutil.rmtree(p, ignore_errors=True)
            if workspace_skill_dir_value:
                p = Path(workspace_skill_dir_value)
                if p.exists():
                    if p.is_symlink():
                        p.unlink()
                    else:
                        shutil.rmtree(p, ignore_errors=True)
            return
        if workspace_skill_dir_value is None:
            return
        workspace_skill_dir = Path(workspace_skill_dir_value)
        ensure_directory(workspace_skill_dir.parent)
        if workspace_skill_dir.exists():
            if workspace_skill_dir.is_symlink():
                workspace_skill_dir.unlink()
            else:
                shutil.rmtree(workspace_skill_dir)

        skill_src = self._find_skill_root(self.target_skill_path)
        shutil.copytree(
            skill_src,
            workspace_skill_dir,
            ignore=shutil.ignore_patterns(
                ".DS_Store",
                ".pytest_cache",
                ".ruff_cache",
                "__pycache__",
                ".git",
            ),
        )
        LOGGER.info(
            "已通过复制注入 skill 到实验工作区: skill=%s, from=%s, to=%s",
            self.config.target_skill,
            skill_src,
            workspace_skill_dir,
        )

    def _find_skill_root(self, skill_path: Path) -> Path:
        skill_md = Path(skill_path) / "SKILL.md"
        if skill_md.exists():
            return skill_path
        for subdir in Path(skill_path).iterdir():
            if subdir.is_dir():
                sub_skill_md = subdir / "SKILL.md"
                if sub_skill_md.exists():
                    LOGGER.info("检测到 skill 嵌套路径，自动使用: %s", subdir)
                    return subdir
                for sub2 in subdir.iterdir():
                    if sub2.is_dir():
                        sub2_skill_md = sub2 / "SKILL.md"
                        if sub2_skill_md.exists():
                            LOGGER.info("检测到 skill 深度嵌套路径，自动使用: %s", sub2)
                            return sub2
        return skill_path

    def get_workspace_skill_dir(self, plan: ExperimentPlan) -> str | None:
        """按宿主约定返回工作区内 skill 注入目录（P2: 经 Subject Adapter 参数化）。"""
        if not plan.use_skill or not self.config.target_skill.strip():
            return None
        config_host = resolve_config_host(
            self.config
        ) or EXECUTION_MODE_DEFAULT_HOST.get(
            str(self.config.execution_mode or ""), "opencode"
        )
        try:
            adapter = get_subject_adapter(config_host)
        except Exception:
            adapter = None
        if adapter is not None:
            try:
                injected = adapter.inject_skill(
                    self.target_skill_path, plan.workspace_dir, self.config.target_skill
                )
                # 注入完成即验证加载，防止假对照
                verify = adapter.verify_skill_loaded(
                    plan.workspace_dir, self.config.target_skill
                )
                if not verify["loaded"]:
                    from .subject_adapter import SubjectAdapterError

                    raise SubjectAdapterError(
                        f"[{config_host}] skill 注入后加载验证失败: {verify['evidence']}"
                    )
                return injected.get("skill_dir")
            except Exception as e:
                LOGGER.warning(
                    "Subject Adapter 注入失败，回退到 opencode 目录约定: %s", e
                )
        return str(
            plan.workspace_dir / ".opencode" / "skills" / self.config.target_skill
        )

    def build_input_file_checksums(self, input_files: list[Path]) -> dict[str, str]:
        return {
            str(item): sha256(item.read_bytes()).hexdigest() for item in input_files
        }

    def classify_execution(
        self,
        exit_code: int | None,
        stdout_text: str,
        stderr_text: str,
        artifact_files: list[str],
    ) -> tuple[bool, str, str | None]:
        return _classify_execution(
            exit_code=exit_code,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            artifact_files=artifact_files,
        )

    def slugify(self, value: str) -> str:
        return value.replace("/", "_").replace(" ", "-")

    def _extract_tokens_from_output(
        self, stdout_text: str, stderr_text: str, model: str
    ) -> dict | None:
        return _extract_tokens_from_output_common(stdout_text, stderr_text)

    def _result_to_dict(self, result: ExperimentResult) -> dict[str, Any]:
        d = asdict(result)
        if isinstance(d.get("execution_metrics"), dict):
            pass
        elif hasattr(result, "execution_metrics") and hasattr(
            result.execution_metrics, "to_dict"
        ):
            d["execution_metrics"] = result.execution_metrics.to_dict()
        return d

    def execute_plan(
        self, runner: RunnerConfig, plan: ExperimentPlan, dry_run: bool
    ) -> ExperimentResult:
        start_time = datetime.now()
        execution_metrics = ExecutionMetrics(start_time=start_time.isoformat())

        self.materialize_plan(plan, runner, plan.workspace_dir.parent.parent)
        if dry_run:
            write_text(
                plan.artifacts_dir / "result.md",
                "# Dry Run Artifact\n\n此文件用于验证产物索引与分析流程。\n",
            )

        exit_code, stdout_text, stderr_text = _execute_experiment_impl(
            prompt_text=plan.prompt_text,
            plan_model=plan.model,
            workspace_dir=plan.workspace_dir,
            stdout_file=plan.stdout_file,
            stderr_file=plan.stderr_file,
            transcript_file=plan.transcript_file,
            timeout_seconds=self.config.runner_timeout_seconds,
            dry_run=dry_run,
            execution_metrics=execution_metrics,
            execution_mode=self.config.execution_mode,
            input_files=plan.input_files,
        )

        _wait_for_artifacts_settle(plan.artifacts_dir)
        artifact_files = sorted(
            str(item.relative_to(plan.workspace_dir))
            for item in plan.artifacts_dir.rglob("*")
            if item.is_file()
        )
        write_result_selection(
            plan.workspace_dir,
            task_prompt=self.config.task_prompt,
            target_skill=self.config.target_skill,
        )

        success, status, failure_reason = self.classify_execution(
            exit_code=exit_code,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            artifact_files=artifact_files,
        )

        # 短执行时间检测：如果执行时间极短（< 10s）且无产物，标记为疑似未真正执行
        wall_clock = execution_metrics.wall_clock_seconds or 0
        if wall_clock < 10 and not artifact_files and success is False:
            status = "suspicious_short_execution"
            failure_reason = f"执行仅 {wall_clock:.1f}s 且无产物，疑似模型未真正响应或 OpenCode CLI 调用异常。"
            LOGGER.warning(
                "短执行警告: model=%s, use_skill=%s, wall_clock=%.1fs, artifacts=%d",
                plan.model,
                plan.use_skill,
                wall_clock,
                len(artifact_files),
            )

        result = ExperimentResult(
            runner_name=runner.name,
            model=plan.model,
            use_skill=plan.use_skill,
            exit_code=exit_code,
            success=success,
            status=status,
            failure_reason=failure_reason,
            workspace_dir=str(plan.workspace_dir),
            stdout_file=str(plan.stdout_file),
            stderr_file=str(plan.stderr_file),
            transcript_file=str(plan.transcript_file),
            artifacts=artifact_files,
            analysis={},
            execution_metrics=execution_metrics,
        )
        write_json(plan.workspace_dir / "result.json", self._result_to_dict(result))
        return result

    def normalize_subprocess_output(self, value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    def execute_plan_safe(
        self, runner: RunnerConfig, plan: ExperimentPlan, dry_run: bool
    ) -> ExperimentResult:
        try:
            return self.execute_plan(runner, plan, dry_run=dry_run)
        except Exception as exc:
            ensure_directory(plan.workspace_dir)
            ensure_directory(plan.artifacts_dir)
            fallback_stderr = f"Unhandled evaluator error: {type(exc).__name__}: {exc}"
            write_text(plan.stdout_file, "")
            write_text(plan.stderr_file, fallback_stderr + "\n")
            if not plan.transcript_file.exists():
                write_text(
                    plan.transcript_file,
                    "# Transcript\n\n评测器内部异常，未能完成外部执行。\n\n## STDERR\n\n"
                    + fallback_stderr
                    + "\n",
                )
            result = ExperimentResult(
                runner_name=runner.name,
                model=plan.model,
                use_skill=plan.use_skill,
                exit_code=None,
                success=False,
                status="internal_error",
                failure_reason="evaluator_exception",
                workspace_dir=str(plan.workspace_dir),
                stdout_file=str(plan.stdout_file),
                stderr_file=str(plan.stderr_file),
                transcript_file=str(plan.transcript_file),
                artifacts=[],
                analysis={},
            )
            write_json(plan.workspace_dir / "result.json", asdict(result))
            LOGGER.error(
                "实验执行异常: runner=%s, model=%s, error=%s",
                runner.name,
                plan.model,
                fallback_stderr,
            )
            return result

    def run(self, dry_run: bool = False) -> Path:
        self.config.validate(self.config_dir)
        run_dir = self.create_run_directory()
        write_json(run_dir / "config.snapshot.json", self.config.to_dict())
        plan_items = self.build_plans(run_dir)
        indexed_results: dict[int, ExperimentResult] = {}
        max_workers = (
            min(self.config.max_parallelism, len(plan_items)) if plan_items else 1
        )

        if max_workers <= 1:
            for index, (runner, plan) in enumerate(plan_items):
                indexed_results[index] = self.execute_plan_safe(
                    runner, plan, dry_run=dry_run
                )
        else:
            LOGGER.info("启用并发执行: workers=%s", max_workers)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_index = {
                    executor.submit(
                        self.execute_plan_safe, runner, plan, dry_run
                    ): index
                    for index, (runner, plan) in enumerate(plan_items)
                }
                for future in as_completed(future_to_index):
                    index = future_to_index[future]
                    indexed_results[index] = future.result()

        results = [indexed_results[index] for index in sorted(indexed_results)]

        skill_promises = _extract_skill_promises(str(self.target_skill_path))

        write_json(
            run_dir / "run_manifest.json",
            {
                "evaluation_name": self.config.evaluation_name,
                "target_skill": self.config.target_skill,
                "target_skill_path": str(self.target_skill_path),
                "task_prompt": self.config.task_prompt,
                "skill_promises": skill_promises,
                "host": resolve_config_host(self.config)
                or EXECUTION_MODE_DEFAULT_HOST.get(
                    str(self.config.execution_mode or ""), "opencode"
                ),
                "results": [asdict(item) for item in results],
            },
        )
        _run_blind_review_stage(
            run_dir,
            self.config.blind_review_models,
            execution_mode=self.config.execution_mode,
            host=resolve_config_host(self.config),
        )
        write_run_summary(run_dir)
        LOGGER.info("评测运行、盲评与汇总分析完成: run_dir=%s", run_dir)

        return run_dir

    def plan_only(self) -> dict[str, Any]:
        self.config.validate(self.config_dir)
        run_dir = self.create_run_directory()
        write_json(run_dir / "config.snapshot.json", self.config.to_dict())
        plans: list[dict[str, Any]] = []
        for runner, plan in self.build_plans(run_dir):
            command = self.materialize_plan(plan, runner, run_dir)
            plans.append(
                {
                    "runner": runner.name,
                    "model": plan.model,
                    "use_skill": plan.use_skill,
                    "repetition": plan.repetition,
                    "workspace_dir": str(plan.workspace_dir),
                    "command": command,
                    "input_files": [str(f) for f in plan.input_files],
                }
            )
        write_json(run_dir / "plan.json", {"plans": plans})
        LOGGER.info("评测计划已生成: run_dir=%s", run_dir)
        return {"run_dir": str(run_dir), "plans": plans}


def load_config(config_path: Path) -> EvaluationConfig:
    import json

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return EvaluationConfig.from_dict(raw)


def _run_blind_review_stage(
    run_dir: Path,
    voting_models: list[str],
    review_parallelism: int | None = None,
    progress: RunProgress | None = None,
    execution_mode: str = "cli-attach",
    host: str | None = None,
) -> None:
    """执行必选盲评阶段；失败时记录日志，但不阻断后续汇总与报告。"""
    if not voting_models:
        LOGGER.warning("未配置评委模型，跳过盲评阶段")
        return

    LOGGER.info("进入盲评阶段: voting_models=%s", voting_models)
    try:
        run_blind_vote_phase(
            run_dir,
            voting_models=voting_models,
            parallelism=review_parallelism,
            progress=progress,
            execution_mode=execution_mode,
            host=host,
        )
        LOGGER.info("盲评阶段完成")
    except Exception as e:
        LOGGER.error("盲评阶段失败: %s", e)


def analyze_existing_run(run_dir: Path) -> dict[str, Any]:
    LOGGER.info("开始重建已有运行目录的汇总分析: run_dir=%s", run_dir)
    return write_run_summary(run_dir)


def generate_existing_run_report(run_dir: Path) -> dict[str, Any]:
    LOGGER.info("开始为已有运行目录生成 HTML 报告: run_dir=%s", run_dir)
    output_path = generate_run_report(run_dir)
    anon_output_path = generate_anonymous_report(run_dir)
    return {
        "report_path": str(output_path),
        "anonymous_report_path": str(anon_output_path),
        "html_generated": output_path.exists(),
    }


def run_summary_stage(
    run_dir: Path,
    summary_model: str,
    timeout_seconds: int = 300,
    execution_mode: str = "cli-attach",
    host: str | None = None,
) -> dict[str, Any]:
    """基于评委评价生成结构化洞察结论"""
    print(f"\n{'='*60}")
    print("💡 洞察生成阶段")
    print(f"{'='*60}")

    # 收集任务信息
    manifest_path = run_dir / "run_manifest.json"
    task_prompt = ""
    target_skill = ""
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        task_prompt = manifest.get("task_prompt", "")
        target_skill = manifest.get("target_skill", "")

    # 读取评委评价
    evals_path = run_dir / "blind_vote" / "evaluations.json"
    stats_path = run_dir / "blind_vote" / "stats.json"

    judge_evaluations_text = ""
    vote_stats_text = ""

    if evals_path.exists():
        evals = json.loads(evals_path.read_text(encoding="utf-8"))
        eval_lines = ["【评委原始评价汇总】"]
        for eval_item in evals:
            voting_model = eval_item.get("voting_model", "unknown")
            eval_lines.append(f"\n--- 评委: {voting_model} ---")
            for ev in eval_item.get("evaluations", []):
                overall = ev.get("overall", "")
                all_ev = ev.get("all_evaluations", {}) or {}
                reasons = ev.get("reasons", {}) or {}
                if overall:
                    eval_lines.append(f"总体评价: {overall}")
                for label, evaluation in all_ev.items():
                    reason = reasons.get(label, "")
                    eval_lines.append(f"  {label}: {evaluation[:200]}...")
        judge_evaluations_text = "\n".join(eval_lines)

    if stats_path.exists():
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        stat_lines = ["【投票结果统计】"]
        for model, stat in stats.items():
            with_v = stat.get("with_votes", 0)
            without_v = stat.get("without_votes", 0)
            with_reasons = stat.get("with_reasons", [])
            without_reasons = stat.get("without_reasons", [])
            stat_lines.append(f"\n{model}:")
            stat_lines.append(f"  With-Skill: {with_v}票")
            stat_lines.append(f"  Without-Skill: {without_v}票")
            if with_reasons:
                stat_lines.append(f"  With-Skill获票原因: {with_reasons[0][:100]}")
            if without_reasons:
                stat_lines.append(
                    f"  Without-Skill获票原因: {without_reasons[0][:100]}"
                )
        vote_stats_text = "\n".join(stat_lines)

    summary_prompt = f"""基于评委评价和投票，给出结论。

【任务】
{task_prompt}

【评委评价】
{judge_evaluations_text}

【投票】
{vote_stats_text}

输出 JSON（只输出JSON）：
{{
    "overall": "一句话结论",
    "overall_reason": "为什么",
    "scheme_summaries": {{
        "方案A": {{
            "summary": "评委怎么评价这个方案",
            "votes": 票数,
            "mode": "with-skill 或 without-skill"
        }}
    }},
    "skill_helped": ["具体好在哪"],
    "skill_not_helped": ["具体差在哪"],
    "suggestion": "建议"
}}

**九个去AI味规则（必须遵守）：**

0. **说人话（最高优先级）**
   - 结论面向普通用户，禁止出现统计术语：Δ、CI、置信区间、方差、标准差、inconclusive、断言
   - 需要表达差距时用「平均分差」「结果波动范围」「没测出明显区别」这类白话
   - 不要罗列数字证据链，直接给出用户能行动的结论（开 / 不开 / 再多测几轮）

1. **用具体词，不用抽象词**
   - ❌ "结构完整"、"质量较高"、"可读性强"
   - ✅ "有年同比数据、渠道对比"、"误写总门店数"

2. **句子长度要变化**
   - ❌ 连续三句都是15-20字
   - ✅ 长短交替，有节奏感

3. **删掉对冲词**
   - ❌ "值得注意的是"、"一般来说"、"在很多情况下"
   - ✅ 直接说结论

4. **不要"首先、其次、最后"**
   - ❌ "首先...其次...最后..."
   - ✅ 直接并列，不需要过渡

5. **每个观点要有锚点**
   - ❌ "方案质量较高"
   - ✅ "方案A 第3行销售额和原始数据一致"

6. **加第一人称视角**
   - ❌ "评测结果显示"
   - ✅ "我抽查了第1、5、10行，都对"

7. **删掉过渡词**
   - ❌ "此外"、"另外"、"同时"
   - ✅ 直接说下一件事

8. **限制标点**
   - ❌ 破折号、分号太多
   - ✅ 用句号断句

9. **去RLHF声音**
   - ❌ "这是一个好问题"、"希望这对你有帮助"
   - ✅ 直接说结论，不要客套

**正反对比：**

❌ AI味：
"综上所述，Skill 在结构化输出方面有一定提升，但数据准确性方面仍有待加强，建议根据具体场景选择使用。"

✅ 人味：
"B/F 方案结构更完整，有年同比数据、渠道对比；C/G 方案用了 Skill 仍然出错，误写总门店数。这个任务不复杂，Skill 带来的框架优势没有转化成明显的质量优势。"
"""

    # 调用总结模型（P2: 经统一宿主调度层分发，不再绑定 OpenCode）
    try:
        result = _call_host_model(
            model=summary_model,
            prompt=summary_prompt,
            timeout_seconds=timeout_seconds,
            execution_mode=execution_mode,
            workspace_dir=str(run_dir),
            host=host,
        )
        # 尝试解析JSON
        summary_data = _extract_json_object_from_text(result)
        if summary_data:
            # 保存到 blind_vote/summary.json
            ensure_directory(run_dir / "blind_vote")
            write_json(run_dir / "blind_vote" / "summary.json", summary_data)
            print(f"\n✅ 洞察结论已保存: {run_dir / 'blind_vote' / 'summary.json'}")
            return summary_data
        else:
            LOGGER.warning("总结模型未返回有效JSON，保存原始输出")
            summary_data = {"raw_output": result}
            ensure_directory(run_dir / "blind_vote")
            write_json(run_dir / "blind_vote" / "summary.json", summary_data)
            return summary_data
    except Exception as e:
        LOGGER.error("洞察生成失败: %s", e)
        raise


def _call_host_model(
    model: str,
    prompt: str,
    timeout_seconds: int,
    execution_mode: str,
    workspace_dir: str,
    host: str | None = None,
) -> str:
    """经统一宿主调度层调用模型（盲评评委 / 总结模型共用），返回模型文本输出。"""
    return run_host_text(
        host=host,
        model=model,
        prompt=prompt,
        timeout_seconds=timeout_seconds,
        execution_mode=execution_mode,
        workspace_dir=workspace_dir,
    )


def _execute_experiment_impl(
    *,
    prompt_text: str,
    plan_model: str,
    workspace_dir: Path,
    stdout_file: Path,
    stderr_file: Path,
    transcript_file: Path,
    timeout_seconds: int,
    dry_run: bool,
    execution_metrics: ExecutionMetrics,
    execution_mode: str = "cli-attach",
    input_files: list[Path] | None = None,
    host: str | None = None,
    host_config: dict[str, Any] | None = None,
) -> tuple[int, str, str]:
    """经统一宿主调度层执行单个实验，写入 stdout/stderr/transcript 并更新 metrics。

    P2: 不再直接调用任何宿主专用传输函数；宿主经 host 参数（或 execution_mode
    的默认映射）解析为对应 executor，通过 execute_session 统一契约执行。

    返回 (exit_code, stdout_text, stderr_text)。
    """
    if dry_run:
        write_text(stdout_file, "[dry-run] 未实际启动 agent。\n")
        write_text(stderr_file, "")
        write_text(transcript_file, "# Transcript\n\n[dry-run]\n")
        end_time = datetime.now()
        execution_metrics.end_time = end_time.isoformat()
        if execution_metrics.start_time:
            execution_metrics.wall_clock_seconds = round(
                (
                    end_time - datetime.fromisoformat(execution_metrics.start_time)
                ).total_seconds(),
                2,
            )
        return 0, "[dry-run]\n", ""

    try:
        exit_code, stdout_text, stderr_text, transcript_text, usage_info = (
            run_host_experiment_session(
                model=plan_model,
                prompt=prompt_text,
                cwd=workspace_dir,
                timeout_seconds=timeout_seconds,
                execution_mode=execution_mode,
                host=host,
                host_config=host_config,
                files=[str(item) for item in (input_files or [])],
            )
        )
        write_text(
            transcript_file, f"# Transcript\n\n{transcript_text or stdout_text}\n"
        )

        if stderr_text:
            write_text(stderr_file, stderr_text)

        token_info = (
            usage_info
            if usage_info
            else _extract_tokens_from_output_common(stdout_text, stderr_text)
        )
        if token_info:
            execution_metrics.prompt_tokens = token_info.get("prompt_tokens")
            execution_metrics.completion_tokens = token_info.get("completion_tokens")
            execution_metrics.total_tokens = token_info.get("total_tokens")
            execution_metrics.api_call_count = token_info.get("api_call_count", 1)
    except subprocess.TimeoutExpired:
        stderr_text = f"Command timed out after {timeout_seconds}s\n"
        stdout_text = ""
        exit_code = 124
        write_text(stderr_file, stderr_text)
        if not transcript_file.exists():
            write_text(transcript_file, f"# Transcript\n\n执行超时\n")
    except Exception as e:
        stderr_text = f"CLI execution failed: {type(e).__name__}: {e}\n"
        stdout_text = ""
        exit_code = 1
        write_text(stderr_file, stderr_text)
        if not transcript_file.exists():
            write_text(
                transcript_file, f"# Transcript\n\nCLI 执行异常: {stderr_text}\n"
            )

    # 确保 stdout / stderr 持久化到文件
    if not dry_run:
        write_text(stdout_file, stdout_text)
        write_text(stderr_file, stderr_text)

    end_time = datetime.now()
    execution_metrics.end_time = end_time.isoformat()
    if execution_metrics.start_time:
        execution_metrics.wall_clock_seconds = round(
            (
                end_time - datetime.fromisoformat(execution_metrics.start_time)
            ).total_seconds(),
            2,
        )

    return exit_code, stdout_text, stderr_text


def run_from_plan(
    plan_dir: Path,
    dry_run: bool = False,
    parallelism: int | None = None,
    review_parallelism: int | None = None,
    progress_interval: float = 5.0,
    skip_blind_review: bool = False,
) -> Path:
    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed

    plan_json = plan_dir / "plan.json"
    raw = json.loads(plan_json.read_text(encoding="utf-8"))
    all_plans = raw.get("plans", [])

    config_snapshot_path = plan_dir / "config.snapshot.json"
    if config_snapshot_path.exists():
        config = EvaluationConfig.from_dict(
            json.loads(config_snapshot_path.read_text(encoding="utf-8"))
        )
    else:
        raise ValueError(f"Missing config.snapshot.json in {plan_dir}")

    # P2 能力预检：宿主必须声明 file_ops（产物型任务的硬性前提），
    # 不支持时显式报错，绝不静默降级继续跑实验。
    if not dry_run:
        _host = resolve_config_host(config) or EXECUTION_MODE_DEFAULT_HOST.get(
            str(config.execution_mode or ""), "opencode"
        )
        _executor = resolve_host(_host, config.execution_mode)
        _executor.require_capability("file_ops", purpose="实验执行（产物型评测任务）")
        LOGGER.info(
            "宿主能力预检通过: host=%s, capabilities=%s",
            _host,
            sorted(_executor.capabilities.keys()),
        )

    indexed_results: dict[int, ExperimentResult] = {}
    requested_parallelism = (
        parallelism if parallelism and parallelism > 0 else config.max_parallelism
    )
    max_workers = min(max(1, requested_parallelism), len(all_plans)) if all_plans else 1
    timeout_seconds = config.runner_timeout_seconds
    LOGGER.info("执行超时设置为: %d 秒", timeout_seconds)
    progress = RunProgress(plan_dir, interval_seconds=progress_interval)
    progress.start_stage("实验执行", len(all_plans))

    def execute_one(index: int, plan_item: dict[str, Any]) -> ExperimentResult:
        workspace_dir = Path(plan_item["workspace_dir"])
        stdout_file = workspace_dir / "stdout.txt"
        stderr_file = workspace_dir / "stderr.txt"
        transcript_file = workspace_dir / "transcript.md"
        artifacts_dir = workspace_dir / "artifacts"
        ensure_directory(workspace_dir)
        ensure_directory(artifacts_dir)

        # 纪律 4：without-skill 隔离验证与加载证据准备
        # P2: 按宿主解析 Subject Adapter，加载验证不再绑定 .opencode/skills/ 约定
        use_skill = bool(plan_item["use_skill"])
        config_host = resolve_config_host(config) or EXECUTION_MODE_DEFAULT_HOST.get(
            str(config.execution_mode or ""), "opencode"
        )
        try:
            subject_adapter = get_subject_adapter(config_host)
        except Exception:
            subject_adapter = None  # 未知宿主时回退到通用验证（注入结果由计划阶段保证）
        if not use_skill:
            if subject_adapter is not None:
                cleanup_evidence = subject_adapter.cleanup_skill(
                    workspace_dir, config.target_skill
                )
            else:
                cleanup_evidence = {}
            skill_loaded = False
            loading_evidence = {
                **cleanup_evidence,
                "mode": "without-skill",
                "host": config_host,
                "workspace_isolated": True,
            }
        else:
            if subject_adapter is not None:
                try:
                    # run_from_plan 直接执行路径：确保 skill 已按宿主规则注入（幂等）
                    subject_adapter.inject_skill(
                        config.target_skill_path, workspace_dir, config.target_skill
                    )
                except Exception as inject_err:
                    LOGGER.warning(
                        "Subject Adapter 注入异常（继续执行）: %s", inject_err
                    )
                verify = subject_adapter.verify_skill_loaded(
                    workspace_dir, config.target_skill
                )
                skill_loaded = bool(verify["loaded"]) or bool(config.target_skill)
                loading_evidence = {
                    **verify["evidence"],
                    "target_skill": config.target_skill,
                    "mode": "with-skill",
                    "workspace_isolated": True,
                }
            else:
                skill_loaded = bool(config.target_skill)
                loading_evidence = {
                    "target_skill": config.target_skill,
                    "mode": "with-skill",
                    "host": config_host,
                    "workspace_isolated": True,
                }

        prompt_file = workspace_dir / "prompt.md"
        prompt_text = (
            prompt_file.read_text(encoding="utf-8")
            if prompt_file.exists()
            else plan_item.get("prompt", "")
        )

        max_retries = getattr(config, "max_retries", 2)
        retry_attempts: list[dict[str, Any]] = []
        attempt = 0

        while True:
            start_time = datetime.now()
            execution_metrics = ExecutionMetrics(start_time=start_time.isoformat())

            exit_code, stdout_text, stderr_text = _execute_experiment_impl(
                prompt_text=prompt_text,
                plan_model=plan_item["model"],
                workspace_dir=workspace_dir,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
                transcript_file=transcript_file,
                timeout_seconds=timeout_seconds,
                dry_run=dry_run,
                execution_metrics=execution_metrics,
                execution_mode=config.execution_mode,
                input_files=[Path(f) for f in plan_item.get("input_files", [])],
                host=resolve_config_host(config),
                host_config=resolve_config_host_config(config),
            )

            # 等待文件落盘（模型可能在 opencode 退出后继续写文件）
            _wait_for_artifacts_settle(artifacts_dir)

            # 收集产出文件
            artifact_files = sorted(
                str(item.relative_to(workspace_dir))
                for item in artifacts_dir.rglob("*")
                if item.is_file()
            )
            write_result_selection(
                workspace_dir,
                task_prompt=config.task_prompt,
                target_skill=config.target_skill,
            )

            success, status, failure_reason = _classify_execution(
                exit_code=exit_code,
                stdout_text=stdout_text,
                stderr_text=stderr_text,
                artifact_files=artifact_files,
            )

            # 纪律 3：invalid 支持重试机制（可配置最大重试次数，耗尽才判定 invalid）
            if status == "invalid" and attempt < max_retries and not dry_run:
                attempt += 1
                retry_attempts.append(
                    {
                        "attempt": attempt,
                        "timestamp": datetime.now().isoformat(),
                        "exit_code": exit_code,
                        "failure_reason": failure_reason,
                    }
                )
                retry_plain = {
                    "fatal_stderr_error": "模型运行环境出错了，正在自动重试",
                    "command_timeout": "这个实验跑超时了，正在重试",
                    "provider_or_network_error": "网络/服务方出问题了，正在重试",
                }.get(str(failure_reason or ""), f"实验遇到环境问题（{failure_reason}），正在重试")
                LOGGER.warning(
                    "%s（第 %d/%d 次）: model=%s, mode=%s",
                    retry_plain,
                    attempt,
                    max_retries,
                    plan_item["model"],
                    "with-skill" if use_skill else "without-skill",
                )
                import time

                time.sleep(1.0)
                continue
            break

        # 短执行时间检测
        wall_clock = execution_metrics.wall_clock_seconds or 0
        if (
            wall_clock < 10
            and not artifact_files
            and success is False
            and status != "invalid"
        ):
            status = "failure"
            failure_reason = "empty_product"

        is_valid = status != "invalid"

        # 执行四类确定性断言判定 (零 LLM 调用)
        analysis_data: dict[str, Any] = {}
        assertions_config = config.evaluation_contract.get("validation_assertions", [])
        if assertions_config and is_valid and success:
            try:
                from .assertions import AssertionEngine

                assertion_eng = AssertionEngine()
                artifacts_map = {}
                for art_rel in artifact_files:
                    art_full = workspace_dir / art_rel
                    try:
                        if art_full.stat().st_size <= 500 * 1024:
                            artifacts_map[art_rel] = art_full.read_text(
                                encoding="utf-8", errors="replace"
                            )
                    except Exception:
                        pass
                suite_res = assertion_eng.evaluate_suite(
                    assertions_config,
                    output_text=stdout_text,
                    artifacts=artifacts_map,
                    context={"stdout": stdout_text, "model": plan_item["model"]},
                )
                # script 契约宿主把模型最终回答放在 text 字段（已追加进 transcript 落盘），
                # 但不写入 stdout.txt；对这类宿主，若 stdout 无完成信号，用 transcript 补充判定视野
                if not suite_res.passed:
                    transcript_disk = ""
                    try:
                        transcript_disk = (
                            transcript_file.read_text(
                                encoding="utf-8", errors="replace"
                            )
                            if transcript_file.exists()
                            else ""
                        )
                    except Exception:
                        transcript_disk = ""
                    if (
                        transcript_disk
                        and transcript_disk.strip() != stdout_text.strip()
                    ):
                        retry_suite = assertion_eng.evaluate_suite(
                            assertions_config,
                            output_text=stdout_text + "\n" + transcript_disk,
                            artifacts=artifacts_map,
                            context={
                                "stdout": stdout_text,
                                "model": plan_item["model"],
                            },
                        )
                        if retry_suite.passed or retry_suite.score > suite_res.score:
                            suite_res = retry_suite
                analysis_data["assertions"] = suite_res.to_dict()
                if not suite_res.passed:
                    success = False
                    status = "failure"
                    failure_reason = "assertion_failure"
            except Exception as e:
                LOGGER.error("断言套件执行异常: %s", e)

        temp_result = ExperimentResult(
            runner_name=resolve_config_host(config)
            or EXECUTION_MODE_DEFAULT_HOST.get(
                str(config.execution_mode or ""), "opencode"
            ),
            model=plan_item["model"],
            use_skill=plan_item["use_skill"],
            exit_code=exit_code,
            success=success,
            status=status,
            failure_reason=failure_reason,
            workspace_dir=str(workspace_dir),
            stdout_file=str(stdout_file),
            stderr_file=str(stderr_file),
            transcript_file=str(transcript_file),
            artifacts=artifact_files,
            analysis=analysis_data,
            repetition=int(plan_item.get("repetition", 1) or 1),
            execution_metrics=execution_metrics,
            is_valid=is_valid,
            skill_loaded=skill_loaded,
            loading_evidence=loading_evidence,
            retry_attempts=retry_attempts,
        )
        write_json(workspace_dir / "result.json", asdict(temp_result))
        return temp_result

    def execute_one_with_progress(
        index: int, plan_item: dict[str, Any]
    ) -> ExperimentResult:
        model = plan_item["model"]
        mode = "with-skill" if plan_item["use_skill"] else "without-skill"
        repetition = int(plan_item.get("repetition", 1) or 1)
        task_id = f"experiment:{index}"
        progress.task_started(
            task_id, f"{model} {mode} rep{repetition}", model=model, mode=mode
        )
        result = execute_one(index, plan_item)
        progress.task_finished(
            task_id,
            bool(result.success),
            model=model,
            mode=mode,
            status=result.status,
            artifacts=len(result.artifacts),
            wall_clock_seconds=result.execution_metrics.wall_clock_seconds,
        )
        return result

    # 检查已有结果，跳过已完成的实验（断点续传）
    pending_plans: list[tuple[int, dict[str, Any]]] = []
    for index, plan_item in enumerate(all_plans):
        workspace_dir = Path(plan_item["workspace_dir"])
        result_file = workspace_dir / "result.json"
        if result_file.exists():
            try:
                existing = json.loads(result_file.read_text(encoding="utf-8"))
                indexed_results[index] = ExperimentResult(
                    runner_name=existing.get("runner_name")
                    or resolve_config_host(config)
                    or EXECUTION_MODE_DEFAULT_HOST.get(
                        str(config.execution_mode or ""), "opencode"
                    ),
                    model=existing["model"],
                    use_skill=existing["use_skill"],
                    exit_code=existing.get("exit_code", -1),
                    success=existing.get("success", False),
                    status=existing.get("status", "unknown"),
                    failure_reason=existing.get("failure_reason"),
                    workspace_dir=existing.get("workspace_dir", str(workspace_dir)),
                    stdout_file=existing.get(
                        "stdout_file", str(workspace_dir / "stdout.txt")
                    ),
                    stderr_file=existing.get(
                        "stderr_file", str(workspace_dir / "stderr.txt")
                    ),
                    transcript_file=existing.get(
                        "transcript_file", str(workspace_dir / "transcript.md")
                    ),
                    artifacts=existing.get("artifacts", []),
                    analysis=existing.get("analysis", {}),
                    repetition=int(
                        existing.get("repetition", plan_item.get("repetition", 1)) or 1
                    ),
                    execution_metrics=ExecutionMetrics(
                        start_time=existing.get("execution_metrics", {}).get(
                            "start_time"
                        ),
                        end_time=existing.get("execution_metrics", {}).get("end_time"),
                        wall_clock_seconds=existing.get("execution_metrics", {}).get(
                            "wall_clock_seconds"
                        ),
                    ),
                    is_valid=existing.get(
                        "is_valid", existing.get("status") != "invalid"
                    ),
                    skill_loaded=existing.get(
                        "skill_loaded", bool(existing["use_skill"])
                    ),
                    loading_evidence=existing.get("loading_evidence", {}),
                    retry_attempts=existing.get("retry_attempts", []),
                )
                model = plan_item["model"]
                mode = "with-skill" if plan_item["use_skill"] else "without-skill"
                print(f"  [跳过] 已有结果: {model} ({mode})", flush=True)
                progress.task_completed(
                    f"experiment:{index}",
                    model=model,
                    mode=mode,
                    success=existing.get("success", False),
                    status=existing.get("status", "unknown"),
                    artifacts=len(existing.get("artifacts", [])),
                    wall_clock_seconds=existing.get("execution_metrics", {}).get(
                        "wall_clock_seconds", 0
                    ),
                )
                continue
            except Exception:
                pass  # result.json 损坏，重新执行
        pending_plans.append((index, plan_item))

    skipped_count = len(all_plans) - len(pending_plans)
    if skipped_count > 0:
        print(
            f"  跳过 {skipped_count} 个已有结果的实验，剩余 {len(pending_plans)} 个待执行",
            flush=True,
        )
    if not pending_plans:
        print(f"  所有实验均已完成，无需重新执行", flush=True)

    if max_workers <= 1:
        for index, plan_item in pending_plans:
            model = plan_item["model"]
            mode = "with-skill" if plan_item["use_skill"] else "without-skill"
            print(
                f"  [{len(indexed_results) + 1}/{len(all_plans)}] 执行: {model} ({mode})...",
                flush=True,
            )
            indexed_results[index] = execute_one_with_progress(index, plan_item)
            status = "✅" if indexed_results[index].success else "❌"
            wall = indexed_results[index].execution_metrics.wall_clock_seconds or 0
            print(
                f"  [{len(indexed_results)}/{len(all_plans)}] {status} 完成: {model} ({mode}), {wall:.0f}s",
                flush=True,
            )
    else:
        total_to_run = len(pending_plans)
        print(
            f"  并发数: {max_workers}, 共 {total_to_run} 个待执行实验（{skipped_count} 个已跳过）",
            flush=True,
        )
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(execute_one_with_progress, index, plan_item): index
                for index, plan_item in pending_plans
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                indexed_results[index] = future.result()
                completed += 1
                result = indexed_results[index]
                model = all_plans[index]["model"]
                mode = (
                    "with-skill" if all_plans[index]["use_skill"] else "without-skill"
                )
                status = "✅" if result.success else "❌"
                wall = result.execution_metrics.wall_clock_seconds or 0
                print(
                    f"  [{completed + skipped_count}/{len(all_plans)}] {status} {model} ({mode}), {wall:.0f}s",
                    flush=True,
                )

    results = [indexed_results[index] for index in sorted(indexed_results)]
    skill_promises = _extract_skill_promises(str(config.target_skill_path))
    write_json(
        plan_dir / "run_manifest.json",
        {
            "evaluation_name": config.evaluation_name,
            "target_skill": config.target_skill,
            "target_skill_path": str(config.target_skill_path),
            "task_prompt": config.task_prompt,
            "skill_promises": skill_promises,
            "repetitions": config.repetitions,
            "host": resolve_config_host(config)
            or EXECUTION_MODE_DEFAULT_HOST.get(
                str(config.execution_mode or ""), "opencode"
            ),
            "results": [asdict(item) for item in results],
        },
    )

    # P1: 同模型配对增益统计、判定快照与人审触发记录落盘（零 LLM）
    try:
        from .paired_gain import rebuild_p1_outputs

        rebuild_p1_outputs(plan_dir)
        LOGGER.info("P1 配对增益统计已落盘: %s", plan_dir / "p1")
    except Exception as e:
        LOGGER.error("P1 配对增益统计失败: %s", e)

    # 判定器版本固定与快照落盘 (assertions_snapshot.json)
    try:
        from .assertions import AssertionEngine, save_assertions_snapshot

        assertion_engine = AssertionEngine()
        assertion_records = []
        for res in results:
            stdout_content = ""
            if Path(res.stdout_file).exists():
                try:
                    stdout_content = Path(res.stdout_file).read_text(
                        encoding="utf-8", errors="replace"
                    )
                except Exception:
                    pass
            assertion_records.append(
                {
                    "model": res.model,
                    "use_skill": res.use_skill,
                    "repetition": res.repetition,
                    "status": res.status,
                    "success": res.success,
                    "is_valid": res.is_valid,
                    "output_text": stdout_content,
                    "artifacts": {a: "" for a in res.artifacts},
                    "results": res.analysis.get("assertions", {}).get("results", []),
                }
            )
        snapshot_data = assertion_engine.create_snapshot(
            rules=config.evaluation_contract.get("validation_assertions", []),
            records=assertion_records,
            metadata={
                "evaluation_name": config.evaluation_name,
                "plan_dir": str(plan_dir),
            },
        )
        save_assertions_snapshot(snapshot_data, plan_dir / "assertions_snapshot.json")
    except Exception as e:
        LOGGER.error("落盘断言快照失败: %s", e)

    if not skip_blind_review:
        _run_blind_review_stage(
            plan_dir,
            config.blind_review_models,
            review_parallelism=review_parallelism,
            progress=progress,
            execution_mode=config.execution_mode,
            host=resolve_config_host(config),
        )
        # 总结阶段：基于评委评价生成结构化洞察
        if config.summary_model:
            progress.start_stage("生成洞察", 1)
            progress.task_started("summary:insight", "生成洞察结论")
            try:
                run_summary_stage(
                    plan_dir,
                    config.summary_model,
                    execution_mode=config.execution_mode,
                    host=resolve_config_host(config),
                )
                progress.task_finished("summary:insight", True)
            except Exception as e:
                LOGGER.error("生成洞察失败: %s", e)
                progress.task_finished("summary:insight", False)
        progress.start_stage("汇总报告", 1)
        progress.task_started("summary:analysis", "汇总分析")
        write_run_summary(plan_dir)
        progress.task_finished("summary:analysis", True)
    progress.finish()
    return plan_dir


def _extract_skill_promises(skill_path: str) -> dict[str, Any]:
    """从 SKILL.md 提取承诺和评测维度定义，用于报告解释与结果追溯"""
    import re
    import yaml
    from pathlib import Path

    skill_md_path = Path(skill_path) / "SKILL.md"
    if not skill_md_path.exists():
        return {
            "skill_name": "",
            "promises": [],
            "output_files": [],
            "phases": [],
            "evaluation_dimensions": {},
        }

    content = skill_md_path.read_text(encoding="utf-8", errors="replace")

    name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
    skill_name = name_match.group(1).strip() if name_match else ""

    eval_dims = _extract_evaluation_dimensions(content)

    phases = []
    for match in _PHASE_PATTERN.finditer(content):
        phase_num = match.group(1)
        phase_name = match.group(2).strip()
        phases.append(f"Phase {phase_num}: {phase_name}")

    constraints = []
    for match in _CONSTRAINT_PATTERN.finditer(content):
        text = match.group(0).strip()
        if len(text) > 5:
            constraints.append(text)

    outputs = []
    for pattern in _OUTPUT_PATTERNS:
        for match in pattern.finditer(content):
            text = match.group(0).strip()
            if len(text) > 3:
                outputs.append(text)

    output_files = []
    for pattern in _FILE_PATTERNS:
        for match in pattern.finditer(content):
            output_files.append(match.group(1).lower())

    entry_points = []
    for pattern in _ENTRY_PATTERNS:
        for match in pattern.finditer(content):
            entry_points.append(match.group(1).strip()[:80])

    promises = {
        "skill_name": skill_name,
        "phases": phases,
        "constraints": constraints[:10],
        "outputs": outputs[:10],
        "output_files": list(dict.fromkeys(output_files)),
        "entry_points": entry_points[:5],
        "raw_summary": _summarize_skill_for_judge(content),
        "evaluation_dimensions": eval_dims,
    }

    return promises


def _extract_evaluation_dimensions(content: str) -> dict[str, Any]:
    """盲评只保留两类主评分维度：需求满足度与结果内容质量。"""
    shared_dims = [
        {
            "name": "用户需求满足度",
            "weight": 50,
            "verification": "验证输出是否真正回答了用户的问题，是否完整覆盖关键要求，是否有明显遗漏或偏题。若关键事实、计算结果、功能行为或交付目标明显错误，也应视为未满足需求。",
        },
        {
            "name": "最终产出内容质量",
            "weight": 50,
            "verification": "直接阅读最终产出文件内容，判断其质量、实用性、正确性、可读性或视觉/实现质量。不要因为使用了某个 skill 就自动加分。",
        },
    ]
    return {
        "with_skill": [dict(item) for item in shared_dims],
        "without_skill": [dict(item) for item in shared_dims],
    }


def _generate_default_evaluation_dimensions(content: str) -> list[dict[str, Any]]:
    """当 SKILL.md 没有定义评测维度时，自动生成默认维度"""
    import re

    has_phases = bool(re.search(r"(?:Phase|阶段)[：:\s]+\d+", content, re.IGNORECASE))
    has_constraints = bool(re.search(r"(?:必须|禁止|不得|不能|确保)", content))

    dims = []

    dims.append(
        {
            "name": "用户需求满足度",
            "weight": 40,
            "verification": "验证输出是否真正回答了用户的问题。",
        }
    )

    if has_phases:
        dims.append(
            {
                "name": "流程合规性",
                "weight": 25,
                "verification": "验证是否按 skill 承诺的流程执行了所有阶段。",
            }
        )

    if has_constraints:
        dims.append(
            {
                "name": "规范遵守度",
                "weight": 15,
                "verification": "验证是否遵守了 skill 定义的约束规则。",
            }
        )

    dims.append(
        {
            "name": "产出内容质量",
            "weight": 20 if len(dims) == 2 else 20 - (15 if has_constraints else 0),
            "verification": "读取产出文件内容，验证质量。",
        }
    )

    total_weight = sum(d["weight"] for d in dims)
    if total_weight != 100:
        scale = 100 / total_weight
        for d in dims:
            d["weight"] = int(d["weight"] * scale)
        dims[-1]["weight"] += 100 - sum(d["weight"] for d in dims)

    return dims


def _summarize_skill_for_judge(content: str) -> str:
    """为评委生成 skill 的简洁摘要"""
    import re

    lines = content.split("\n")
    important_lines = []

    skip_sections = {
        "#",
        "##",
        "---",
        "```",
        "name:",
        "version:",
        "description:",
        "triggers:",
        "tools_required:",
    }
    skip_keywords = ["...", "<!--", "*/", "*/", "---"]

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(s) for s in skip_sections):
            continue
        if any(s in stripped.lower() for s in skip_keywords):
            continue
        if len(stripped) < 10:
            continue
        important_lines.append(stripped[:150])
        if len(important_lines) >= 30:
            break

    return "\n".join(important_lines)


@dataclass
class BlindVotePair:
    model: str
    with_skill_workspace: str
    without_skill_workspace: str
    evaluations: list[dict]
    repetition: int = 1  # P1: 配对所属重复轮次


@dataclass
class BlindEvaluationResult:
    voting_model: str
    pair_model: str
    evaluations: list[dict]


def collect_blind_vote_pairs(run_dir: Path) -> list[BlindVotePair]:
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        return []

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    grouped: dict[tuple[str, int], dict[str, str | None]] = {}
    skipped: list[str] = []
    skipped: list[str] = []

    for item in manifest.get("results", []):
        model = str(item.get("model") or "").strip()
        workspace_dir = str(item.get("workspace_dir") or "").strip()
        if not model or not workspace_dir:
            continue

        if workspace_dir and not Path(workspace_dir).is_absolute():
            workspace_dir = str(run_dir / workspace_dir)
        if workspace_dir and not Path(workspace_dir).exists():
            relocated = run_dir / "experiments" / Path(workspace_dir).name
            if relocated.exists():
                workspace_dir = str(relocated)

        selection = select_result_files(
            workspace_dir,
            task_prompt=str(manifest.get("task_prompt", "") or ""),
            target_skill=str(manifest.get("target_skill", "") or ""),
        )
        result_files = selection.get("validated_result_files", []) or []

        if not result_files:
            skip_reason = f"{model} ({'with-skill' if item.get('use_skill') else 'without-skill'})"
            if skip_reason not in skipped:
                skipped.append(skip_reason)
                LOGGER.info(
                    "盲评跳过无用户验收产物实验: model=%s, use_skill=%s, selection_status=%s",
                    model,
                    item.get("use_skill"),
                    selection.get("status", "unknown"),
                )
            continue

        pair = grouped.setdefault(
            (model, int(item.get("repetition", 1) or 1)),
            {"with": None, "without": None},
        )
        if item.get("use_skill"):
            pair["with"] = workspace_dir
        else:
            pair["without"] = workspace_dir

    if skipped:
        print(f"   ⚠️  盲评自动跳过 {len(skipped)} 个无用户验收产物的实验:")
        for s in skipped:
            print(f"      - {s}")

    pairs: list[BlindVotePair] = []
    for (model, repetition), pair in sorted(grouped.items()):
        with_path = pair.get("with")
        without_path = pair.get("without")
        if with_path or without_path:
            pairs.append(
                BlindVotePair(
                    model=model,
                    with_skill_workspace=with_path or "",
                    without_skill_workspace=without_path or "",
                    evaluations=[],
                    repetition=repetition,
                )
            )
    return pairs


def conduct_blind_vote(
    pairs: list[BlindVotePair],
    voting_models: list[str],
    task_prompt: str = "",
    timeout_seconds: int = 300,
    parallelism: int | None = None,
    progress: RunProgress | None = None,
    execution_mode: str = "cli-attach",
    host: str | None = None,
) -> list[BlindEvaluationResult]:
    """执行批量盲评：评委一次性看所有方案并分发N票（经统一宿主调度层）。"""
    return _conduct_batch_vote(
        pairs,
        voting_models,
        task_prompt,
        timeout_seconds,
        parallelism,
        progress,
        execution_mode,
        host,
    )


def _conduct_batch_vote(
    pairs: list[BlindVotePair],
    voting_models: list[str],
    task_prompt: str = "",
    timeout_seconds: int = 300,
    parallelism: int | None = None,
    progress: RunProgress | None = None,
    execution_mode: str = "cli-attach",
    host: str | None = None,
) -> list[BlindEvaluationResult]:
    """批量盲评：每个评委一次性看所有有用户验收产物的方案，分发N票给最好的。

    规则：
    - 每个评委最多有N票，N=可评审模型组数，且不超过实际候选方案数
    - 同一个评委对同一个方案最多只能投1票
    - 评委一次性拿到所有方案（不知道哪个是with/without，也不知道对应哪个模型）
    - 评委必须评价每个候选方案，但只给认为最好的方案投票
    """
    import random

    # 构建所有候选方案
    all_options = []
    option_labels = []  # 存储打乱的label->workspace映射
    labels = [
        "方案A",
        "方案B",
        "方案C",
        "方案D",
        "方案E",
        "方案F",
        "方案G",
        "方案H",
        "方案I",
        "方案J",
    ]

    for i, pair in enumerate(pairs):
        for mode, path in [
            ("with", pair.with_skill_workspace),
            ("without", pair.without_skill_workspace),
        ]:
            if not path:
                continue
            all_options.append(
                {
                    "original_idx": i,
                    "mode": mode,
                    "workspace": path,
                }
            )

    if not all_options:
        LOGGER.warning("批量盲评没有可评审的用户验收产物方案")
        return []
    votes_per_judge = min(
        len(pairs), len(all_options)
    )  # 每位评委最多N票，且不能超过实际候选方案数。

    jobs: list[tuple[str, list[dict], dict[str, dict]]] = []
    for voting_model in voting_models:
        shuffled = all_options.copy()
        random.shuffle(shuffled)
        label_to_option = {}
        for j, opt in enumerate(shuffled):
            label = labels[j] if j < len(labels) else f"方案{chr(65+j)}"
            label_to_option[label] = {
                "label": label,
                "original_idx": opt["original_idx"],
                "mode": opt["mode"],
                "workspace": opt["workspace"],
            }
        jobs.append((voting_model, shuffled, label_to_option))

    max_workers = min(max(1, int(parallelism or 1)), len(jobs)) if jobs else 1
    if progress:
        progress.start_stage("盲评", len(jobs))
    print(
        f"  批量盲评模式: 每个评委最多 {votes_per_judge} 票, 共 {len(jobs)} 个评委",
        flush=True,
    )

    def run_batch_job(
        voting_model: str, options: list[dict], label_to_option: dict[str, dict]
    ) -> BlindEvaluationResult | None:
        task_id = f"batch_review:{voting_model}"
        label = f"{voting_model} 批量评审"
        if progress:
            progress.task_started(
                task_id, label, voting_model=voting_model, model="batch"
            )
        print(f"  ⏳ 正在批量评审: {voting_model}", flush=True)

        eval_prompt = _build_batch_eval_prompt(
            options, label_to_option, votes_per_judge, task_prompt
        )
        result = _execute_batch_eval_command(
            voting_model,
            eval_prompt,
            label_to_option,
            timeout_seconds,
            execution_mode,
            host,
        )

        if progress:
            progress.task_finished(
                task_id, bool(result), voting_model=voting_model, model="batch"
            )
        return result

    results: list[BlindEvaluationResult] = []
    if max_workers <= 1:
        for job in jobs:
            eval_result = run_batch_job(*job)
            if eval_result:
                results.append(eval_result)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_job = {
                executor.submit(run_batch_job, vm, opts, l2o): (vm, opts, l2o)
                for vm, opts, l2o in jobs
            }
            for future in as_completed(future_to_job):
                eval_result = future.result()
                if eval_result:
                    results.append(eval_result)

    return results


def _build_batch_eval_prompt(
    options: list[dict],
    label_to_option: dict[str, dict],
    votes: int,
    task_prompt: str,
) -> str:
    """构建批量投票prompt：一次性展示所有方案，评委投N票"""
    task_desc = task_prompt if task_prompt else "请根据实际执行内容判断"

    # 构建所有方案的证据
    evidence_sections = []
    for j, opt in enumerate(options):
        label = (
            label_to_option[str(opt.get("workspace", ""))]
            if False
            else (
                list(label_to_option.keys())[j]
                if j < len(label_to_option)
                else f"方案{chr(65+j)}"
            )
        )
        for lbl, info in label_to_option.items():
            if info["workspace"] == opt["workspace"]:
                label = lbl
                break
        evidence = _build_evidence_bundle(opt["workspace"], task_prompt=task_prompt)
        evidence_sections.append(f"""
## {label}
<!-- 评委看不到此信息，仅供系统内部追踪: 模型编号={opt['original_idx']}, 模式={opt['mode']} -->
证据内容:
{evidence if evidence else '[无产出文件]'}
""")

    options_list = "\n".join([f"- {lbl}" for lbl in label_to_option.keys()])

    return f"""# 盲评任务 - 批量投票

## 用户需求
{task_desc}

## 盲评规则
- 你不知道方案背后的模型编号、skill来源、流程约束，不要猜测这些信息。
- 你最多有 **{votes} 票**，投给你认为最好的方案；如果合格方案不足，可以少投。
- 每个方案只能投1票或0票，不能重复投同一个方案。
- 直接投票给最满足用户需求、最终结果文件内容质量最高的方案，不要考虑是否使用了skill。
- 你只能评价下方每个方案的 `validated_result_files` 内容；不要参考、推测或奖励 summary、execution_trace、日志、脚本、中间素材、文件数量、耗时、token 或执行过程。
- 主要判断维度固定为：
  1. **需求满足度**：是否真正完成用户需求，是否覆盖关键要求，是否有明显遗漏、事实错误、计算错误或不可执行结论。
  2. **实际产出结果文件质量**：结果文件是否完整、正确、可读、可复核、可交付；如果是 PPT/Excel/HTML/Markdown/代码等，请按该文件类型的真实内容质量判断。
- 请先逐方案评价，再填写 votes；votes 数组中的方案名不能重复。
- 如果方案没有交付用户要求格式的结果文件，或结果文件内容明显空泛/无法复核，即使执行成功也不应优先投票。
- 下方展示的每个方案都已经有用户要求或 skill 契约匹配的 `validated_result_files`。无论你是否投票，都必须在 `逐方案评价` 中评价每一个方案。缺少任一方案评价会导致本次评审无效。

## 所有方案（已匿名化，按随机顺序展示）

{chr(10).join(evidence_sections)}

## 输出格式
请只输出一个JSON对象，不要输出Markdown。

JSON schema：
```json
{{
  "votes": ["方案A", "方案C", "方案E", ...],  // 你投票的方案列表，最多{votes}个
  "理由": {{
    "方案A": "为什么投给这个方案（简洁一句）",
    "方案C": "为什么投给这个方案（简洁一句）",
    ...
  }},
  "逐方案评价": {{
    "方案A": "无论是否投票，都必须评价该方案。若无结果文件，请写明未交付要求结果文件。",
    "方案B": "无论是否投票，都必须评价该方案。",
    ...
  }},
  "总体评价": "对这些方案的总体评价（1-2句话）"
}}
```"""


def _execute_batch_eval_command(
    voting_model: str,
    prompt: str,
    label_to_option: dict[str, dict],
    timeout_seconds: int,
    execution_mode: str = "cli-attach",
    host: str | None = None,
) -> BlindEvaluationResult | None:
    """执行批量盲评（P2: 评委调用经统一宿主调度层分发，不再绑定 OpenCode）"""

    try:
        eval_dir = Path(tempfile.mkdtemp(prefix="eval_batch_"))
        session = run_host_session(
            host=host,
            model=voting_model,
            prompt=prompt,
            cwd=eval_dir,
            timeout_seconds=timeout_seconds,
            execution_mode=execution_mode,
            format_json=False,
            dangerous_skip_permissions=True,
        )
        raw_text = session.combined_text()

        # 解析投票结果
        parsed_vote = _parse_batch_vote_payload(raw_text, label_to_option)
        missing_evaluations = [
            label
            for label in label_to_option.keys()
            if label not in parsed_vote.get("all_evaluations", {})
        ]
        if missing_evaluations:
            LOGGER.warning(
                "评委模型 %s 未按要求评价所有有产物方案，缺少: %s",
                voting_model,
                ", ".join(missing_evaluations),
            )
            raise ValueError(
                "blind review missing per-scheme evaluations for non-empty artifact options: "
                + ", ".join(missing_evaluations)
            )

        return BlindEvaluationResult(
            voting_model=voting_model,
            pair_model="batch",
            evaluations=[
                {
                    "votes": parsed_vote["votes"],
                    "reasons": parsed_vote["reasons"],
                    "all_evaluations": parsed_vote.get("all_evaluations", {}),
                    "overall": parsed_vote["overall"],
                    "workspace_votes": {
                        v: label_to_option.get(v, {}).get("workspace", "")
                        for v in parsed_vote["votes"]
                    },
                    "workspace_evaluations": {
                        label: label_to_option.get(label, {}).get("workspace", "")
                        for label in label_to_option.keys()
                    },
                    "missing_evaluation_labels": missing_evaluations,
                    "raw_output": raw_text[:80000],
                }
            ],
        )
    except Exception as e:
        LOGGER.error("批量盲评执行失败: %s", e)
        return None


def _parse_batch_vote_payload(
    text: str, label_to_option: dict[str, dict]
) -> dict[str, Any]:
    """解析批量投票结果，保留投票、理由和总体评价。"""
    try:
        obj = _extract_json_object_from_text(text)
        if obj:
            votes = obj.get("votes", [])
            if not isinstance(votes, list):
                votes = []
            valid_votes: list[str] = []
            seen_votes: set[str] = set()
            for raw_vote in votes:
                label = str(raw_vote)
                if label not in label_to_option or label in seen_votes:
                    continue
                seen_votes.add(label)
                valid_votes.append(label)
                if len(valid_votes) >= len(label_to_option):
                    break
            raw_reasons = obj.get("理由", obj.get("reasons", {}))
            reasons = {}
            if isinstance(raw_reasons, dict):
                reasons = {
                    str(label): str(reason)
                    for label, reason in raw_reasons.items()
                    if str(label) in label_to_option and str(reason).strip()
                }
            raw_all = (
                obj.get("逐方案评价")
                or obj.get("all_evaluations")
                or obj.get("evaluations_by_label")
                or obj.get("评价")
                or {}
            )
            all_evaluations = {}
            if isinstance(raw_all, dict):
                all_evaluations = {
                    str(label): str(reason)
                    for label, reason in raw_all.items()
                    if str(label) in label_to_option and str(reason).strip()
                }
            for label in valid_votes:
                if label in reasons and label not in all_evaluations:
                    all_evaluations[label] = reasons[label]
            for label in valid_votes:
                if label not in reasons and label in all_evaluations:
                    reasons[label] = all_evaluations[label]
            overall = obj.get("总体评价", obj.get("overall", ""))
            return {
                "votes": valid_votes,
                "reasons": reasons,
                "all_evaluations": all_evaluations,
                "overall": str(overall or ""),
            }
    except Exception:
        pass
    return {"votes": [], "reasons": {}, "all_evaluations": {}, "overall": ""}


def _parse_batch_votes(text: str, label_to_option: dict[str, dict]) -> list[str]:
    """兼容旧调用：只返回投票标签。"""
    return _parse_batch_vote_payload(text, label_to_option)["votes"]


# ============================================================
# 原有辅助函数和数据结构
# ============================================================

BINARY_EXTENSIONS = {
    ".xlsx",
    ".xls",
    ".csv",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".msg",
    ".eot",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".exe",
    ".dmg",
    ".app",
    ".dll",
    ".so",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".wav",
    ".pyc",
    ".pyo",
    ".class",
    ".jar",
    ".db",
    ".sqlite",
    ".sqlite3",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".pl",
    ".pm",
    ".r",
    ".sql",
    ". graphql",
    ".env",
    ".ini",
    ".cfg",
    ".conf",
    ".log",
    ".diff",
    ".patch",
}


# 盲评证据包整体体积上限（字符数），防止评委 prompt 过大
_EVIDENCE_BUNDLE_MAX_CHARS = 60_000

# 不同类型文件的单文件读取上限
_FILE_CHAR_LIMITS = {
    ".md": 15_000,  # 报告类文件，需要较完整内容
    ".json": 12_000,  # 结构化数据，通常需要完整阅读
    ".txt": 12_000,  # 文本文件
    ".html": 10_000,  # HTML 页面
    ".csv": 8_000,  # CSV 数据
    ".sql": 8_000,  # SQL 脚本
    ".py": 8_000,  # Python 代码
    ".js": 8_000,  # JavaScript 代码
    ".ts": 8_000,  # TypeScript 代码
    ".tsx": 8_000,  # TSX 组件
    ".jsx": 8_000,  # JSX 组件
    ".vue": 8_000,  # Vue 组件
    ".yaml": 6_000,
    ".yml": 6_000,
    ".toml": 6_000,
    ".xml": 6_000,
    ".log": 6_000,
}
_DEFAULT_CHAR_LIMIT = 5_000  # 未匹配类型文件的默认上限


def _file_char_limit(f: Path) -> int:
    return _FILE_CHAR_LIMITS.get(f.suffix.lower(), _DEFAULT_CHAR_LIMIT)


def _try_read_file(f: Path, max_chars: int = 0) -> str:
    """读取文件内容，按文件类型动态限制最大字符数。

    Args:
        f: 文件路径
        max_chars: 手动指定上限（0 表示按文件类型自动推断）
    """
    ext = f.suffix.lower()
    limit = max_chars if max_chars > 0 else _file_char_limit(f)

    if ext in BINARY_EXTENSIONS and ext not in TEXT_EXTENSIONS:
        if ext == ".xlsx":
            try:
                import openpyxl

                wb = openpyxl.load_workbook(f, data_only=True)
                lines = []
                for sheet in wb.sheetnames:
                    ws = wb[sheet]
                    lines.append(f"=== Sheet: {sheet} ===")
                    for row in ws.iter_rows(max_row=50, values_only=True):
                        if any(cell is not None for cell in row):
                            lines.append(
                                " | ".join(str(c) if c is not None else "" for c in row)
                            )
                    lines.append("")
                return "\n".join(lines)[:limit]
            except ImportError:
                return f"[二进制 Excel 文件，大小: {f.stat().st_size} 字节，无法读取（未安装 openpyxl）]"
            except Exception:
                return f"[二进制 Excel 文件，大小: {f.stat().st_size} 字节，解析失败]"

        if ext in (".csv",):
            try:
                import csv

                lines = []
                with open(f, newline="", encoding="utf-8", errors="replace") as fp:
                    reader = csv.reader(fp)
                    for i, row in enumerate(reader):
                        if i >= 100:
                            lines.append(f"...（已截断）")
                            break
                        lines.append(" | ".join(str(c) for c in row))
                return "\n".join(lines)[:limit]
            except Exception:
                return f"[CSV 文件，大小: {f.stat().st_size} 字节，读取失败]"

        if ext == ".pptx":
            try:
                import xml.etree.ElementTree as ET

                lines = []
                with zipfile.ZipFile(f) as zf:
                    slide_names = sorted(
                        [
                            n
                            for n in zf.namelist()
                            if n.startswith("ppt/slides/slide") and n.endswith(".xml")
                        ],
                        key=lambda n: (
                            int(re.search(r"slide(\d+)\.xml", n).group(1))
                            if re.search(r"slide(\d+)\.xml", n)
                            else 0
                        ),
                    )
                    for idx, name in enumerate(slide_names[:30], 1):
                        root = ET.fromstring(zf.read(name))
                        texts = [
                            node.text or ""
                            for node in root.iter()
                            if node.tag.endswith("}t") or node.tag == "t"
                        ]
                        clean = " ".join(t.strip() for t in texts if t and t.strip())
                        lines.append(f"Slide {idx}: {clean or '[未提取到文本]'}")
                return "\n".join(lines)[:limit]
            except Exception:
                return f"[PPTX 文件，大小: {f.stat().st_size} 字节，文本提取失败]"

        return f"[二进制文件，大小: {f.stat().st_size} 字节]"

    try:
        return f.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return f"[文本文件读取失败，大小: {f.stat().st_size} 字节]"


def _build_evidence_bundle(
    workspace_dir: str, task_prompt: str = "", target_skill: str = ""
) -> str:
    if not workspace_dir:
        return ""

    artifacts_dir = Path(workspace_dir) / "artifacts"
    if not artifacts_dir.exists():
        return "[artifacts 目录不存在]"

    parts = []
    selection = select_result_files(
        workspace_dir, task_prompt=task_prompt, target_skill=target_skill
    )
    result_files = selection.get("validated_result_files", []) or []
    missing = selection.get("missing_required_outputs", []) or []

    parts.append("## 评委可评价对象\n")
    parts.append(
        "只允许评价下方 validated_result_files 的实际结果文件内容。summary、execution_trace、日志、脚本、图片素材和中间文件没有提供给你作为评分依据。\n"
    )
    if missing:
        parts.append("### 缺失的必需结果文件\n")
        for item in missing:
            parts.append(
                f"- {item.get('label', '结果文件')} ({item.get('ext', '')}): {item.get('reason', '')}"
            )
        parts.append("")

    if not result_files:
        return (
            "\n".join(parts)
            + "\n[未找到可验证的最终结果文件；该方案应按未交付最终产物处理。]"
        )

    bundle_chars = sum(len(p) for p in parts)

    for item in result_files:
        rel = str(item.get("path") or "")
        f = artifacts_dir / rel
        if not f.exists() or not f.is_file():
            continue
        limit = _file_char_limit(f)
        content = _try_read_file(f)
        header = (
            f"## 结果文件: {rel}\n"
            f"- 角色: {item.get('role', 'primary')}\n"
            f"- 类型: {item.get('ext', f.suffix.lower())}\n"
            f"- 定位原因: {item.get('selection_reason', '')}\n\n"
        )
        file_section_len = len(header) + len(content) + 40
        if bundle_chars + file_section_len > _EVIDENCE_BUNDLE_MAX_CHARS:
            parts.append(f"{header}...[结果文件内容超出证据包上限，已截断]\n")
            continue
        preview = content
        if len(content) >= limit:
            preview += "\n\n...[已截断]"
        parts.append(f"{header}{preview}\n")
        bundle_chars += file_section_len

    if not parts:
        return "[无产出文件]"

    return "\n".join(parts)


def _extract_json_object_from_text(text: str) -> dict[str, Any] | None:
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", text, re.IGNORECASE | re.DOTALL
    )
    candidates = [fenced.group(1)] if fenced else []

    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            char = text[idx]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : idx + 1])
                    break
        start = text.find("{", start + 1)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _aggregate_batch_vote_results(
    pairs: list[BlindVotePair],
    eval_results: list[BlindEvaluationResult],
) -> dict[str, Any]:
    """聚合批量投票结果。

    批量模式下，每个评委一次性看所有方案并投N票。
    需要把投票结果映射回具体的 (model, mode) 组合。
    """
    # 建立 workspace -> (model, repetition, mode) 的映射
    workspace_to_info = {}
    for pair in pairs:
        if pair.with_skill_workspace:
            workspace_to_info[pair.with_skill_workspace] = {
                "model": pair.model,
                "repetition": pair.repetition,
                "mode": "with",
            }
        if pair.without_skill_workspace:
            workspace_to_info[pair.without_skill_workspace] = {
                "model": pair.model,
                "repetition": pair.repetition,
                "mode": "without",
            }

    # 统计每个 (model, repetition, mode) 组合的得票数
    vote_counts: dict[tuple[str, int], dict[str, int]] = (
        {}
    )  # (model, repetition) -> mode -> votes
    judge_reasons: dict[tuple[str, int], dict[str, list]] = (
        {}
    )  # (model, repetition) -> mode -> reasons
    judge_summaries: list[str] = []
    raw_outputs: list[str] = []
    stats: dict[str, Any] = {}

    for er in eval_results:
        for ev in er.evaluations:
            votes = ev.get("votes", [])
            reasons = ev.get("reasons", {}) or {}
            all_evaluations = ev.get("all_evaluations", {}) or {}
            workspace_votes = ev.get("workspace_votes", {})
            workspace_evaluations = ev.get("workspace_evaluations", {}) or {}
            overall = ev.get("overall", "")
            raw_output = ev.get("raw_output", "")
            if overall:
                judge_summaries.append(str(overall))
            if raw_output:
                raw_outputs.append(str(raw_output)[:4000])

            # 映射投票到 (model, mode)
            for vote_label in votes:
                workspace = workspace_votes.get(vote_label, "")
                info = workspace_to_info.get(workspace)
                if not info:
                    continue

                model = info["model"]
                mode = info["mode"]
                key = (model, int(info.get("repetition", 1) or 1))

                if key not in vote_counts:
                    vote_counts[key] = {"with": 0, "without": 0}
                    judge_reasons[key] = {"with": [], "without": []}

                vote_counts[key][mode] += 1

                # 记录理由
                reason = (
                    reasons.get(vote_label, "") if isinstance(reasons, dict) else ""
                )
                if reason:
                    judge_reasons[key][mode].append(reason)

            # 记录所有有产物方案的逐方案评价。未投票但有评价的方案也会进入 reasons，
            # 报告可据此展示“未获票但被评委评价”的说明。
            if isinstance(all_evaluations, dict) and isinstance(
                workspace_evaluations, dict
            ):
                for label, evaluation in all_evaluations.items():
                    evaluation_text = str(evaluation or "").strip()
                    if not evaluation_text:
                        continue
                    workspace = workspace_evaluations.get(label, "")
                    info = workspace_to_info.get(workspace)
                    if not info:
                        continue
                    key = (info["model"], int(info.get("repetition", 1) or 1))
                    mode = info["mode"]
                    if key not in judge_reasons:
                        judge_reasons[key] = {"with": [], "without": []}
                    if evaluation_text not in judge_reasons[key][mode]:
                        judge_reasons[key][mode].append(evaluation_text)

    # 构建统计结果：同模型同重复轮次为一组（P1 配对口径），跨重复汇总到模型层
    pair_stats: dict[str, Any] = {}
    model_stats: dict[str, dict[str, Any]] = {}

    for pair in pairs:
        key = (pair.model, int(pair.repetition or 1))
        counts = vote_counts.get(key, {"with": 0, "without": 0})
        reasons = judge_reasons.get(key, {"with": [], "without": []})

        total_votes = counts["with"] + counts["without"]
        with_pct = counts["with"] / total_votes if total_votes > 0 else 0
        without_pct = counts["without"] / total_votes if total_votes > 0 else 0

        pair_key = f"{pair.model}__rep{pair.repetition}"
        pair_stats[pair_key] = {
            "mode": "batch_vote",
            "model": pair.model,
            "repetition": pair.repetition,
            "with_votes": counts["with"],
            "without_votes": counts["without"],
            "with_wins": counts["with"],
            "without_wins": counts["without"],
            "ties": 0,
            "total_votes": total_votes,
            "with_pct": round(with_pct * 100, 1),
            "without_pct": round(without_pct * 100, 1),
            "with_reasons": reasons["with"][:3] if reasons["with"] else [],
            "without_reasons": reasons["without"][:3] if reasons["without"] else [],
            "final_reasons": judge_summaries[:3],
            "key_differences": _build_batch_key_differences(
                reasons["with"], reasons["without"]
            ),
            "raw_outputs": raw_outputs[:3],
            "num_evaluations": len(eval_results),
            "skill_helped": counts["with"] > counts["without"],
            "vote_source": "batch",
        }

        # 跨重复汇总到模型层（第二层指标）
        agg = model_stats.setdefault(
            pair.model,
            {
                "with": 0,
                "without": 0,
                "repetitions": 0,
                "helped_reps": 0,
                "with_reasons": [],
                "without_reasons": [],
            },
        )
        agg["with"] += counts["with"]
        agg["without"] += counts["without"]
        agg["repetitions"] += 1
        if counts["with"] > counts["without"]:
            agg["helped_reps"] += 1
        agg["with_reasons"].extend(reasons["with"])
        agg["without_reasons"].extend(reasons["without"])

    for model, agg in model_stats.items():
        total_votes = agg["with"] + agg["without"]
        stats[model] = {
            "mode": "batch_vote",
            "repetitions": agg["repetitions"],
            "with_votes": agg["with"],
            "without_votes": agg["without"],
            "with_wins": agg["with"],
            "without_wins": agg["without"],
            "ties": 0,
            "total_votes": total_votes,
            "with_pct": round(agg["with"] / total_votes * 100, 1) if total_votes else 0,
            "without_pct": (
                round(agg["without"] / total_votes * 100, 1) if total_votes else 0
            ),
            "with_reasons": agg["with_reasons"][:3],
            "without_reasons": agg["without_reasons"][:3],
            "final_reasons": judge_summaries[:3],
            "key_differences": _build_batch_key_differences(
                agg["with_reasons"], agg["without_reasons"]
            ),
            "raw_outputs": raw_outputs[:3],
            "num_evaluations": len(eval_results),
            "skill_helped": agg["with"] > agg["without"],
            "helped_repetitions": agg["helped_reps"],
            "vote_source": "batch",
        }

    # 配对粒度统计供 P1 报告使用
    stats["__pairs__"] = pair_stats

    return stats


def _build_batch_key_differences(
    with_reasons: list[str], without_reasons: list[str]
) -> list[str]:
    differences: list[str] = []
    if with_reasons and without_reasons:
        differences.append(
            f"使用 skill 获票原因：{with_reasons[0]}；不使用 skill 获票原因：{without_reasons[0]}"
        )
    elif with_reasons:
        differences.append(f"主要投给使用 skill 的原因：{with_reasons[0]}")
    elif without_reasons:
        differences.append(f"主要投给不使用 skill 的原因：{without_reasons[0]}")
    return differences


def run_blind_vote_phase(
    run_dir: Path,
    voting_models: list[str],
    timeout_seconds: int = 300,
    parallelism: int | None = None,
    progress: RunProgress | None = None,
    execution_mode: str | None = None,
    host: str | None = None,
) -> dict[str, Any]:
    print(f"\n{'='*60}")
    print("🎯 盲评阶段")
    print(f"{'='*60}")
    print("\n📋 评审方式: 统一用户需求 + 匿名方案结果 的必选盲评")

    print("\n📦 收集评测产出对...")
    pairs = collect_blind_vote_pairs(run_dir)

    if not pairs:
        print("⚠️  未找到可对比的产出对（需要同时有 with-skill 和 without-skill 结果）")
        return {"error": "no_pairs_found"}

    print(f"   找到 {len(pairs)} 个可评审产出配对 (模型 × 重复轮次):")
    for pair in pairs:
        modes = []
        if pair.with_skill_workspace:
            modes.append("with-skill")
        if pair.without_skill_workspace:
            modes.append("without-skill")
        print(f"   - {pair.model} rep{pair.repetition}: {', '.join(modes)}")

    if not voting_models:
        print("❌ 未提供评委模型，请在调用时通过 --voting-models 参数指定")
        return {"error": "no_voting_models"}

    task_prompt = ""
    if execution_mode is None:
        config_snapshot_path = run_dir / "config.snapshot.json"
        if config_snapshot_path.exists():
            try:
                config_snapshot = json.loads(
                    config_snapshot_path.read_text(encoding="utf-8")
                )
                execution_mode = str(
                    config_snapshot.get("execution_mode") or "cli-attach"
                )
            except Exception:
                execution_mode = "cli-attach"
        else:
            execution_mode = "cli-attach"
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        task_prompt = manifest.get("task_prompt", manifest.get("target_skill", ""))

    print(
        f"📝 任务: {task_prompt[:80]}..."
        if len(task_prompt) > 80
        else f"📝 任务: {task_prompt}"
    )
    print(f"\n📝 开始盲评（{len(voting_models)} 个评委模型）...")

    eval_results = conduct_blind_vote(
        pairs,
        voting_models,
        task_prompt=task_prompt,
        timeout_seconds=timeout_seconds,
        parallelism=parallelism,
        progress=progress,
        execution_mode=execution_mode,
        host=host,
    )
    if not eval_results:
        return {"error": "blind_review_failed", "message": "盲评未返回任何结果"}

    print("\n📊 盲评结果汇总:")
    stats = _aggregate_batch_vote_results(pairs, eval_results)
    pair_stats: dict[str, Any] = stats.pop("__pairs__", {})

    for model, stat in stats.items():
        if not isinstance(stat, dict):
            continue
        helped_reps = stat.get("helped_repetitions", 0)
        reps = stat.get("repetitions", 0)
        rep_note = f"（{helped_reps}/{reps} 个重复轮次 with 更优）" if reps else ""
        status = "✅ skill有帮助" if stat["skill_helped"] else "❌ skill无明显帮助"
        print(f"\n   {model} {rep_note}:")
        print(
            f"      With-Skill 得票: {stat.get('with_votes', 0)}票 ({stat.get('with_pct', 0)}%)"
        )
        print(
            f"      Without-Skill 得票: {stat.get('without_votes', 0)}票 ({stat.get('without_pct', 0)}%)"
        )
        print(f"      {status}")

    blind_vote_dir = run_dir / "blind_vote"
    ensure_directory(blind_vote_dir)

    write_json(blind_vote_dir / "pairs.json", [asdict(p) for p in pairs])
    write_json(blind_vote_dir / "evaluations.json", [asdict(vr) for vr in eval_results])
    write_json(blind_vote_dir / "stats.json", stats)
    write_json(blind_vote_dir / "pair_stats.json", pair_stats)
    write_json(
        blind_vote_dir / "config.json",
        {
            "blind_review_required": True,
            "voting_models": voting_models,
            "execution_mode": execution_mode,
            "host": host
            or EXECUTION_MODE_DEFAULT_HOST.get(str(execution_mode or ""), "opencode"),
        },
    )

    # P1: 盲评完成后重建配对增益统计（含评委一致率与人审触发记录）
    try:
        from .paired_gain import rebuild_p1_outputs

        rebuild_p1_outputs(run_dir)
    except Exception as e:
        LOGGER.error("P1 配对增益统计重建失败: %s", e)

    print(f"\n💾 盲评结果已保存到: {blind_vote_dir}")

    return {
        "pairs": [asdict(p) for p in pairs],
        "eval_results": [asdict(er) for er in eval_results],
        "stats": stats,
        "output_dir": str(blind_vote_dir),
    }
