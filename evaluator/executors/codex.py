"""Codex 宿主执行器 - 通过 codex CLI 执行

P2 第二/第三宿主 executor。Codex CLI 的非交互执行形态为
`codex exec [--model M] PROMPT`；skill/指令注入约定为项目根 AGENTS.md。

本机未安装 codex CLI 时 validate_config 报告不可用（显式报错，不静默降级）。
"""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .base import BaseExecutor, ExecutionResult, SessionResult


class CodexExecutor(BaseExecutor):
    """Codex 宿主执行器（codex exec 子进程）

    capabilities:
    - file_ops: agent 宿主可在工作目录内读写文件
    - skill_injection: agents-md（AGENTS.md 约定）
    - transcript: codex exec JSON 输出（--json 可用时）
    """

    host_name = "codex"
    capabilities = {
        "file_ops": True,
        "skill_injection": "agents-md",
        "transcript": True,
    }

    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        self.cli_path = self.config.get("cli_path", "codex")
        self.timeout = self.config.get("timeout", 1800)

    def execute(self, model: str, prompt: str, **kwargs) -> ExecutionResult:
        cwd = kwargs.get("cwd", os.getcwd())
        timeout = kwargs.get("timeout", self.timeout)
        session = self.execute_session(
            model=model, prompt=prompt, cwd=cwd, timeout_seconds=timeout,
        )
        return ExecutionResult(
            success=session.success,
            output=session.text or session.stdout,
            error=session.stderr,
            exit_code=session.exit_code,
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
        cwd = Path(cwd) if cwd else Path.cwd()
        cmd = [self.cli_path, "exec"]
        if dangerous_skip_permissions:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        if model:
            cmd.extend(["--model", model])
        if format_json:
            cmd.append("--json")
        cmd.extend(["-", ])  # prompt 从 stdin 读取，避免超长参数
        env = os.environ.copy()
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=timeout_seconds, cwd=str(cwd), env=env,
        )
        return SessionResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            transcript=proc.stdout,
            usage={},
            text=proc.stdout,
        )

    def check_connectivity(self, model: str) -> bool:
        result = self.execute(model, "回答一个字：好", timeout=60)
        return result.success and bool(result.output.strip())

    def list_models(self) -> list[str]:
        return self.config.get("available_models", [])

    def validate_config(self) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                [self.cli_path, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return False, f"codex CLI 无法执行: {proc.stderr.strip()[:200]}"
            return True, ""
        except FileNotFoundError:
            return False, (
                f"找不到 codex CLI: {self.cli_path}。"
                f"请安装 OpenAI Codex CLI 后重试，或改用 mock/script 执行器完成端到端验证。"
            )
        except subprocess.TimeoutExpired:
            return False, "codex CLI --version 超时"
