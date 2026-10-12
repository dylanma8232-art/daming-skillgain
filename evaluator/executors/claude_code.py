"""Claude Code 宿主执行器 - 通过 claude CLI 执行

P2 第二宿主 executor。CLI 可唤起即算接入：当本机 PATH 中存在 claude CLI 时
即为真实宿主；否则 validate_config 报告不可用，端到端测试需使用 mock 宿主。

skill 注入约定：Claude Code 使用 ~/.claude/skills/（用户级）或项目根 .claude/skills/
（项目级）目录自动发现 skills，由 Subject Adapter 负责按该约定注入。
"""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .base import BaseExecutor, ExecutionResult, SessionResult


class ClaudeCodeExecutor(BaseExecutor):
    """Claude Code 宿主执行器（claude CLI 子进程）

    capabilities:
    - file_ops: agent 宿主可在工作目录内读写文件
    - skill_injection: claude-skills-dir（.claude/skills/ 约定）
    - transcript: --output-format stream-json 产出过程记录
    """

    host_name = "claude-code"
    capabilities = {
        "file_ops": True,
        "skill_injection": "claude-skills-dir",
        "transcript": True,
    }

    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        self.cli_path = self.config.get("cli_path", "claude")
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
        cmd = [
            self.cli_path,
            "--print",
            "--output-format", "stream-json",
        ]
        if model:
            cmd.extend(["--model", model])
        if dangerous_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(["--", prompt])

        env = os.environ.copy()
        env.setdefault("CLAUDE_CODE_ENTRYPOINT", "daming-skillgain")
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_seconds, cwd=str(cwd), env=env,
        )
        stdout_text, stderr_text = proc.stdout, proc.stderr
        text_parts: list[str] = []
        usage_info: dict[str, Any] = {}
        for line in stdout_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "assistant":
                message = event.get("message") or {}
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(str(block.get("text") or ""))
            if isinstance(event, dict) and event.get("type") == "result":
                usage = event.get("usage") or {}
                if usage:
                    usage_info = {
                        "prompt_tokens": usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
                        "completion_tokens": usage.get("output_tokens", 0),
                        "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                        "api_call_count": 1,
                    }
        return SessionResult(
            exit_code=proc.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
            transcript=stdout_text,
            usage=usage_info,
            text="\n".join(text_parts),
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
                return False, f"claude CLI 无法执行: {proc.stderr.strip()[:200]}"
            return True, ""
        except FileNotFoundError:
            return False, (
                f"找不到 claude CLI: {self.cli_path}。"
                f"请安装 Claude Code (https://claude.com/product/claude-code) 后重试，"
                f"或改用 mock/script 执行器完成端到端验证。"
            )
        except subprocess.TimeoutExpired:
            return False, "claude CLI --version 超时"
