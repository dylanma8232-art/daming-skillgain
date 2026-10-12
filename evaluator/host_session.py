"""宿主会话调度层（P2 主链解耦核心）

主链（实验执行 / 盲评评委 / 总结模型 / 连通性测试）唯一允许的宿主调用入口。
任何宿主专用传输代码（如 opencode CLI / HTTP session）必须封装在
evaluator/executors/ 下的对应 executor 内，通过本模块分发；主链不得直接 import
任何宿主专用函数。

用法:
    from .host_session import run_host_session, run_host_text, resolve_host

    result = run_host_session(host="opencode", model="m", prompt="p",
                              cwd=workspace, timeout_seconds=600,
                              execution_mode="cli-attach")
    text = run_host_text(host="opencode", model="judge", prompt=eval_prompt)
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any

from .executors import (
    BaseExecutor,
    SessionResult,
    UnsupportedCapabilityError,
    create_executor,
)

LOGGER = logging.getLogger("skill_evaluator.host_session")

# 执行模式 -> 默认宿主（兼容 P2 之前的 execution_mode 配置语义）
EXECUTION_MODE_DEFAULT_HOST = {
    "cli-attach": "opencode",
    "cli": "opencode",
    "api": "opencode",
}


def resolve_host(
    host: str | None = None,
    execution_mode: str | None = None,
    host_config: dict[str, Any] | None = None,
) -> BaseExecutor:
    """解析宿主执行器。

    优先级: 显式 host 参数 > execution_mode 映射 > 默认 opencode。
    host 可为已注册宿主名（opencode / claude-code / codex / script / api /
    standalone / agent），也可为 {"type": ..., ...} 形式的配置字典。
    """
    if isinstance(host, dict):
        return create_executor(dict(host, **(host_config or {})))
    if not host:
        host = EXECUTION_MODE_DEFAULT_HOST.get(str(execution_mode or ""), "opencode")
    config = dict(host_config or {})
    config.setdefault("type", host)
    if host == "opencode" and execution_mode:
        config.setdefault("execution_mode", execution_mode)
    return create_executor(config)


def host_capabilities(
    host: str | None = None,
    execution_mode: str | None = None,
    host_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """查询宿主能力声明（不实例化重资源）"""
    return dict(resolve_host(host, execution_mode, host_config).capabilities)


def require_host_capability(
    capability: str,
    host: str | None = None,
    execution_mode: str | None = None,
    host_config: dict[str, Any] | None = None,
    purpose: str = "",
) -> BaseExecutor:
    """能力预检：宿主不支持该能力时显式报错（禁止静默降级）"""
    executor = resolve_host(host, execution_mode, host_config)
    executor.require_capability(capability, purpose=purpose)
    return executor


def run_host_session(
    *,
    host: str | None = None,
    model: str,
    prompt: str,
    cwd: Path | str | None = None,
    timeout_seconds: int = 600,
    execution_mode: str = "cli-attach",
    format_json: bool = True,
    dangerous_skip_permissions: bool = False,
    files: list[str] | None = None,
    max_tokens: int = 4096,
    host_config: dict[str, Any] | None = None,
) -> SessionResult:
    """统一宿主会话执行：主链所有宿主调用都经此分发。"""
    executor = resolve_host(host, execution_mode, host_config)
    LOGGER.debug(
        "宿主会话调度: host=%s, model=%s, mode=%s, cwd=%s",
        executor.host_name, model, execution_mode, cwd,
    )
    return executor.execute_session(
        model=model,
        prompt=prompt,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        execution_mode=execution_mode if executor.host_name == "opencode" else "cli",
        format_json=format_json,
        dangerous_skip_permissions=dangerous_skip_permissions,
        files=files,
        max_tokens=max_tokens,
    )


def run_host_text(
    *,
    host: str | None = None,
    model: str,
    prompt: str,
    timeout_seconds: int = 300,
    execution_mode: str = "cli-attach",
    host_config: dict[str, Any] | None = None,
    workspace_dir: Path | str | None = None,
) -> str:
    """以纯文本形态调用宿主模型（盲评评委 / 总结模型路径）。

    失败时抛 RuntimeError（与历史行为对齐）。
    """
    result = run_host_session(
        host=host,
        model=model,
        prompt=prompt,
        cwd=workspace_dir,
        timeout_seconds=timeout_seconds,
        execution_mode=execution_mode,
        format_json=False,
        dangerous_skip_permissions=True,
        host_config=host_config,
    )
    if result.exit_code != 0:
        raise RuntimeError(
            f"宿主 '{result and host or ''}' 模型调用失败 (exit={result.exit_code}): "
            f"{(result.stderr or result.stdout).strip()[:500]}"
        )
    text = result.text or result.stdout
    if not text.strip():
        raise RuntimeError("宿主模型调用未返回任何文本输出")
    return text

__all__ = [
    "resolve_host",
    "host_capabilities",
    "require_host_capability",
    "run_host_session",
    "run_host_text",
    "UnsupportedCapabilityError",
]
