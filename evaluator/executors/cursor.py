"""cursor-agent 宿主执行器（第六宿主，HostProtocol 首个真机落地验证）

CLI 形态（实测 `cursor-agent --version` → 2026.10.22-27dcf5f，rc=0）：
    cursor-agent -p --output-format stream-json [--model M] [--workspace DIR] [--force] PROMPT

三件套：
    ① build_invocation: 非交互 `-p`（print 模式），prompt 走 argv（上游 CLI 仅支持位置参数，
       超长 prompt 需调用方控制长度；协议 §2.1 的 stdin 通道在本宿主不可用，已登记）
    ② parse_output:    容忍式解析 stream-json 事件（assistant/result/text/delta）
    ③ probe:           `--version` 零成本探活；`--list-models` 需登录，未登录时回退 available_models

skill 注入约定：AGENTS.md（Cursor Agent 读取工作区 AGENTS.md），实现归属 subject_adapter。
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


class CursorExecutor(BaseExecutor):
    """cursor-agent 宿主执行器

    capabilities:
    - file_ops: agent 宿主可在工作区读写文件
    - skill_injection: agents-md（AGENTS.md 约定）
    - transcript: --output-format stream-json
    - resume: --resume <chatId>
    """

    host_name = "cursor-agent"
    default_cli_path = "cursor-agent"
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
        cmd = [self.cli_command(), "--print", "--output-format", "stream-json"]
        if model:
            cmd.extend(["--model", model])
        # 上下文隔离：以工作区为根，被测 skill 只可在其中读写
        cmd.extend(["--workspace", cwd_str])
        for extra in self.config.get("add_dirs", []) or []:
            cmd.extend(["--add-dir", str(extra)])
        resume_id = str(self.config.get("resume_session") or self.last_session_id or "")
        if resume_id:
            cmd.extend(["--resume", resume_id])
        if dangerous_skip_permissions:
            cmd.append("--force")
        cmd.append("--")
        cmd.append(augment_prompt_with_files(prompt, files))
        return HostInvocation(
            argv=cmd,
            stdin=None,
            cwd=cwd_str,
            prompt_channel="argv",
        )

    # ---- ② 输出解析器 ------------------------------------------------------
    def parse_output(self, *, stdout: str, stderr: str, exit_code: int) -> SessionResult:
        text_parts: list[str] = []
        usage: dict[str, Any] = {}
        session_id = ""
        saw_json = False

        def _collect_text(payload: Any) -> None:
            if isinstance(payload, str) and payload.strip():
                text_parts.append(payload)
            elif isinstance(payload, list):
                for item in payload:
                    _collect_text(item)
            elif isinstance(payload, dict):
                block_type = payload.get("type")
                if block_type in (None, "text", "output_text") and payload.get("text"):
                    text_parts.append(str(payload["text"]))
                for key in ("content", "message", "delta", "result"):
                    if key in payload:
                        _collect_text(payload[key])

        for raw_line in (stdout or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                text_parts.append(raw_line)
                continue
            if not isinstance(event, dict):
                text_parts.append(raw_line)
                continue
            saw_json = True
            etype = str(event.get("type") or "")
            for id_key in ("chat_id", "session_id", "thread_id"):
                if event.get(id_key):
                    session_id = str(event[id_key])
            if etype in ("assistant", "result", "text", "message"):
                _collect_text(event.get("message") or event.get("result") or event.get("text"))
            elif etype.endswith("delta"):
                _collect_text(event.get("delta") or event.get("text"))
            if event.get("usage"):
                raw_usage = event["usage"]
                if isinstance(raw_usage, dict):
                    input_tokens = int(raw_usage.get("input_tokens", 0) or 0)
                    output_tokens = int(raw_usage.get("output_tokens", 0) or 0)
                    if input_tokens or output_tokens:
                        usage = {
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
            usage=usage or {"collected": False},
            text="\n".join(dict.fromkeys(part for part in text_parts if part)),
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
                f"{result.message}。请安装 Cursor Agent CLI 后重试，"
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
                capture_output=True, text=True, timeout=20,
            )
        except Exception:
            return []
        output = (proc.stdout or "") + (proc.stderr or "")
        lowered = output.lower()
        if proc.returncode != 0 or "authentication required" in lowered or "error:" in lowered:
            return []
        found: list[str] = []
        for line in (proc.stdout or "").splitlines():
            name = line.strip().split()[0] if line.strip() else ""
            if name and not name.startswith("-") and " " not in name:
                found.append(name)
        return found

    def validate_config(self) -> tuple[bool, str]:
        probe = self.probe()
        if probe.ok:
            return True, ""
        return False, probe.message
