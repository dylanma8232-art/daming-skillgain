"""Script 执行器 - 通过用户提供的可执行脚本驱动任意宿主（可 mock）

P2 通用化关键件：当本机无法安装真实 agent CLI（如 opencode / claude / codex）
时，允许以一个约定契约的可执行脚本充当「被驱动的宿主」，用于端到端验证
主链解耦、Subject Adapter 与报告宿主边界的正确性。

脚本契约（stdin/stdout JSON）:
    输入: {"model": str, "prompt": str, "cwd": str, "skill_dir": str|null}
    输出: {"exit_code": int, "stdout": str, "stderr": str,
           "transcript": str, "text": str, "usage": {...}}

脚本自身负责：读取 prompt、在 cwd 内完成任务（mock 场景可简单写入产物）、
把结果 JSON 打到 stdout。失败时 exit_code 非 0。

诚实性标注：script 执行器产出的评测结论仅适用于「script 宿主」，
不得声称已在真实宿主上验证；报告宿主边界声明会如实标注。
"""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .base import BaseExecutor, ExecutionResult, SessionResult


class ScriptExecutor(BaseExecutor):
    """脚本宿主执行器

    capabilities 按脚本能力声明配置（默认全声明，用于端到端贯通验证）:
        config: {
            "script": "/path/to/host_script.py 或 .sh（必须可执行）",
            "capabilities": {"file_ops": true, "skill_injection": "script-skills-dir", "transcript": true},
            "available_models": ["mock-model-a", ...]  # 可选，list_models 返回
        }
    """

    host_name = "script"
    capabilities = {
        "file_ops": True,
        "skill_injection": "script-skills-dir",
        "transcript": True,
    }

    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        self.script = self.config.get("script", "")
        caps = self.config.get("capabilities")
        if isinstance(caps, dict):
            self.capabilities = dict(caps)
        self.timeout = self.config.get("timeout", 1800)

    def _run_script(self, payload: dict[str, Any], cwd: Path, timeout_seconds: int) -> dict[str, Any]:
        script = self.script
        if not script:
            raise ValueError("script 执行器缺少配置: script")
        script_path = Path(script).expanduser()
        if not script_path.exists():
            raise FileNotFoundError(f"宿主脚本不存在: {script_path}")
        proc = subprocess.run(
            [str(script_path)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True,
            timeout=timeout_seconds, cwd=str(cwd),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"宿主脚本退出码 {proc.returncode}: {proc.stderr.strip()[:500]}"
            )
        raw = proc.stdout.strip()
        if not raw:
            raise RuntimeError("宿主脚本未输出任何内容（期望 stdout JSON）")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"宿主脚本输出不是合法 JSON: {raw[:300]}") from e
        if not isinstance(data, dict):
            raise RuntimeError("宿主脚本输出 JSON 必须是对象")
        return data

    def execute(self, model: str, prompt: str, **kwargs) -> ExecutionResult:
        cwd = Path(kwargs.get("cwd") or os.getcwd())
        timeout = int(kwargs.get("timeout") or self.timeout)
        try:
            data = self._run_script(
                {"model": model, "prompt": prompt, "cwd": str(cwd), "skill_dir": None},
                cwd, timeout,
            )
        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e), exit_code=-1)
        return ExecutionResult(
            success=int(data.get("exit_code", 1)) == 0,
            output=str(data.get("text") or data.get("stdout") or ""),
            error=str(data.get("stderr") or ""),
            exit_code=int(data.get("exit_code", 1)),
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
        data = self._run_script(
            {"model": model, "prompt": prompt, "cwd": str(cwd), "skill_dir": None},
            cwd, timeout_seconds,
        )
        exit_code = int(data.get("exit_code", 1))
        stdout_text = str(data.get("stdout") or "")
        stderr_text = str(data.get("stderr") or "")
        transcript_text = str(data.get("transcript") or "")
        text = str(data.get("text") or "")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return SessionResult(
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            transcript=transcript_text,
            usage=usage,
            text=text,
        )

    def check_connectivity(self, model: str) -> bool:
        result = self.execute(model, "回答一个字：好", timeout=60)
        return result.success

    def list_models(self) -> list[str]:
        return list(self.config.get("available_models", []))

    def validate_config(self) -> tuple[bool, str]:
        if not self.script:
            return False, "script 执行器缺少配置: script"
        script_path = Path(self.script).expanduser()
        if not script_path.exists():
            return False, f"宿主脚本不存在: {script_path}"
        if not os.access(str(script_path), os.X_OK):
            return False, f"宿主脚本不可执行（缺少 +x）: {script_path}"
        return True, ""
