"""Codex 宿主执行器 - 通过 codex CLI 执行

P2 第二/第三宿主 executor，P4 统一到 HostProtocol 三件套：
    ① build_invocation: `codex exec [--model M] [--cd DIR] [--json] -`（prompt 走 stdin）
    ② parse_output:    解析 `--json` 事件流（text / usage / 会话 id）；非 JSON 输出回退 stdout
    ③ probe:           零成本 `--version` 探活，深度连通检查按需显式开启

skill/指令注入约定为项目根 AGENTS.md（实现归属 subject_adapter，executor 只委派）。

本机未安装 codex CLI 时 validate_config 报告不可用（显式报错，不静默降级）。
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

from .base import (
    BaseExecutor,
    ExecutionResult,
    HostInvocation,
    ProbeResult,
    SessionResult,
    augment_prompt_with_files,
    truncate_stderr,
)


class CodexExecutor(BaseExecutor):
    """Codex 宿主执行器（codex exec 子进程）

    capabilities:
    - file_ops: agent 宿主可在工作目录内读写文件
    - skill_injection: agents-md（AGENTS.md 约定）
    - transcript: codex exec JSON 事件流（--json）
    """

    host_name = "codex"
    default_cli_path = "codex"
    capabilities = {
        "file_ops": True,
        "skill_injection": "agents-md",
        "transcript": True,
        "resume": True,
    }

    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        self.timeout = self.config.get("timeout", 1800)

    # ---- ① 非交互调用形态 --------------------------------------------------
    def build_invocation(
        self,
        *,
        model: str,
        prompt: str,
        cwd: Any = None,
        format_json: bool = False,
        dangerous_skip_permissions: bool = False,
        files: list[str] | None = None,
    ) -> HostInvocation:
        cwd_str = str(cwd or Path.cwd())
        cmd = [self.cli_command(), "exec"]
        resume_id = str(self.config.get("resume_session") or self.last_session_id or "")
        if resume_id:
            # 会话续接：多轮评估复用同一 codex 线程，避免成本×轮数
            cmd.extend(["resume", resume_id])
        if dangerous_skip_permissions:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        if model:
            cmd.extend(["--model", model])
        # 上下文隔离：被测 skill 只可在工作区内读写
        cmd.extend(["--cd", cwd_str])
        if format_json:
            cmd.append("--json")
        cmd.append("-")  # prompt 从 stdin 读取，规避 ARG_MAX
        return HostInvocation(
            argv=cmd,
            stdin=augment_prompt_with_files(prompt, files),
            cwd=cwd_str,
            prompt_channel="stdin",
        )

    # ---- ② 输出解析器 ------------------------------------------------------
    def parse_output(self, *, stdout: str, stderr: str, exit_code: int) -> SessionResult:
        text_parts: list[str] = []
        transcript_parts: list[str] = []
        usage: dict[str, Any] = {}
        session_id = ""
        saw_json = False
        for raw_line in (stdout or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                transcript_parts.append(raw_line)
                continue
            if not isinstance(event, dict):
                transcript_parts.append(raw_line)
                continue
            saw_json = True
            etype = str(event.get("type") or "")
            # codex exec --json: {"type":"item.completed","item":{"type":"agent_message","text":...}}
            item = event.get("item") or {}
            if etype.startswith("item.") and isinstance(item, dict):
                if item.get("type") == "agent_message" and item.get("text"):
                    text_parts.append(str(item["text"]))
            if etype == "thread.started" and event.get("thread_id"):
                session_id = str(event["thread_id"])
            if etype == "turn.completed":
                raw_usage = event.get("usage") or {}
                if isinstance(raw_usage, dict) and raw_usage:
                    input_tokens = int(raw_usage.get("input_tokens", 0) or 0)
                    output_tokens = int(raw_usage.get("output_tokens", 0) or 0)
                    usage = {
                        "prompt_tokens": input_tokens,
                        "completion_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                        "api_call_count": 1,
                    }
            if etype == "error":
                transcript_parts.append(json.dumps(event, ensure_ascii=False))

        if session_id:
            self.last_session_id = session_id

        if not saw_json:
            # 非结构化输出：stdout 原样当 text，usage 显式标「未采集」
            return SessionResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                transcript=stdout,
                usage={"collected": False},
                text=stdout,
            )

        return SessionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            transcript="\n".join(transcript_parts) or stdout,
            usage=usage or {"collected": False},
            text="\n".join(part for part in text_parts if part),
        )

    def execute_session(
        self,
        *,
        model: str,
        prompt: str,
        cwd: Any = None,
        timeout_seconds: int = 600,
        execution_mode: str = "cli",
        format_json: bool = False,
        dangerous_skip_permissions: bool = False,
        files: list[str] | None = None,
        max_tokens: int = 4096,
    ) -> SessionResult:
        return self.run_cli_session(
            model=model,
            prompt=prompt,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            format_json=format_json,
            dangerous_skip_permissions=dangerous_skip_permissions,
            files=files,
        )

    def execute(self, model: str, prompt: str, **kwargs) -> ExecutionResult:
        cwd = kwargs.get("cwd", os.getcwd())
        timeout = kwargs.get("timeout", self.timeout)
        session = self.execute_session(
            model=model, prompt=prompt, cwd=cwd, timeout_seconds=timeout,
        )
        return ExecutionResult(
            success=session.success,
            output=session.text or session.stdout,
            error=truncate_stderr(session.stderr),
            exit_code=session.exit_code,
        )

    # ---- ③ 探活 ------------------------------------------------------------
    def probe(self, timeout: int = 10) -> ProbeResult:
        result = super().probe(timeout=timeout)
        if not result.ok and "找不到" in result.message:
            result.message = (
                f"{result.message}。请安装 OpenAI Codex CLI 后重试，"
                f"或改用 mock/script 执行器完成端到端验证。"
            )
        return result

    def check_connectivity(self, model: str) -> bool:
        """先探活再按需深度检查；默认不烧真实调用（需 config.deep_connectivity=true）。"""
        if not self.probe().ok:
            return False
        if not self.config.get("deep_connectivity"):
            return True
        result = self.execute(model, "回答一个字：好", timeout=60)
        return result.success and bool(result.output.strip())

    def list_models(self) -> list[str]:
        """真实模型发现：优先 `codex --list-models`，失败回退 available_models。"""
        models = self._probe_models()
        if models:
            return models
        return list(self.config.get("available_models", []) or [])

    def _probe_models(self) -> list[str]:
        import subprocess

        if not self.probe().ok:
            return []
        try:
            proc = subprocess.run(
                [self.cli_command(), "--list-models"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            return []
        if proc.returncode != 0:
            return []
        found: list[str] = []
        for line in (proc.stdout or "").splitlines():
            name = line.strip().split()[0] if line.strip() else ""
            if name and not name.startswith("-"):
                found.append(name)
        return found

    def validate_config(self) -> tuple[bool, str]:
        probe = self.probe()
        if probe.ok:
            return True, ""
        return False, probe.message
