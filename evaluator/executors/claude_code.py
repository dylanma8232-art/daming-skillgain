"""Claude Code 宿主执行器 - 通过 claude CLI 执行

P2 第二宿主 executor，P4 统一到 HostProtocol 三件套：
    ① build_invocation: `claude --print --output-format stream-json [--model M] [--add-dir D]`
       prompt 走 stdin（规避 ARG_MAX，与 codex 对齐）
    ② parse_output:    stream-json 事件流 → assistant text + result usage + session_id
    ③ probe:           零成本 `--version` 探活，深度连通检查按需显式开启

skill 注入约定：Claude Code 使用项目根 .claude/skills/（项目级）目录自动发现 skills。
注入/清理的实现唯一归属 evaluator/subject_adapter；executor 只通过 inject_skill /
cleanup_skill 委派（见 base.py 的边界说明）。

本机未安装 claude CLI 时 validate_config 报告不可用（显式报错，不静默降级）。
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


class ClaudeCodeExecutor(BaseExecutor):
    """Claude Code 宿主执行器（claude CLI 子进程）

    capabilities:
    - file_ops: agent 宿主可在工作目录内读写文件
    - skill_injection: claude-skills-dir（.claude/skills/ 约定）
    - transcript: --output-format stream-json 产出过程记录
    - resume: --resume 支持会话续接
    """

    host_name = "claude-code"
    default_cli_path = "claude"
    capabilities = {
        "file_ops": True,
        "skill_injection": "claude-skills-dir",
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
        cmd = [self.cli_command(), "--print", "--output-format", "stream-json", "--verbose"]
        if model:
            cmd.extend(["--model", model])
        if dangerous_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        # 上下文隔离：显式声明额外可访问目录，未声明目录不可读写
        for extra in self.config.get("add_dirs", []) or []:
            cmd.extend(["--add-dir", str(extra)])
        resume_id = str(self.config.get("resume_session") or self.last_session_id or "")
        if resume_id:
            cmd.extend(["--resume", resume_id])
        return HostInvocation(
            argv=cmd,
            stdin=augment_prompt_with_files(prompt, files),
            cwd=cwd_str,
            env={"CLAUDE_CODE_ENTRYPOINT": "daming-skillgain"},
            prompt_channel="stdin",
        )

    # ---- ② 输出解析器 ------------------------------------------------------
    def parse_output(self, *, stdout: str, stderr: str, exit_code: int) -> SessionResult:
        text_parts: list[str] = []
        usage_info: dict[str, Any] = {}
        session_id = ""
        saw_json = False
        for raw_line in (stdout or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            saw_json = True
            if event.get("session_id"):
                session_id = str(event["session_id"])
            if event.get("type") == "assistant":
                message = event.get("message") or {}
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(str(block.get("text") or ""))
            if event.get("type") == "result":
                usage = event.get("usage") or {}
                if event.get("session_id"):
                    session_id = str(event["session_id"])
                if usage:
                    input_tokens = int(usage.get("input_tokens", 0) or 0) + int(
                        usage.get("cache_read_input_tokens", 0) or 0
                    )
                    output_tokens = int(usage.get("output_tokens", 0) or 0)
                    usage_info = {
                        "prompt_tokens": input_tokens,
                        "completion_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                        "api_call_count": 1,
                    }
        if session_id:
            self.last_session_id = session_id
        if not saw_json:
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
            transcript=stdout,
            usage=usage_info or {"collected": False},
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
                f"{result.message}。请安装 Claude Code "
                f"(https://claude.com/product/claude-code) 后重试，"
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
        """真实模型发现：尝试 `claude --list-models`，失败回退 available_models。"""
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
