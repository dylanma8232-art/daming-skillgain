"""执行器工厂 - 根据配置创建执行器"""
from __future__ import annotations
from typing import Any

from .base import BaseExecutor, ExecutionResult, SessionResult, UnsupportedCapabilityError
from .opencode import OpencodeExecutor
from .api import ApiExecutor
from .standalone import StandaloneExecutor
from .agent import AgentExecutor
from .claude_code import ClaudeCodeExecutor
from .codex import CodexExecutor
from .script import ScriptExecutor

# P2 宿主注册表：主链按 host 名经此分发，不直接 import 任何宿主专用模块
HOST_REGISTRY: dict[str, type[BaseExecutor]] = {
    "opencode": OpencodeExecutor,
    "claude-code": ClaudeCodeExecutor,
    "claude_code": ClaudeCodeExecutor,
    "codex": CodexExecutor,
    "script": ScriptExecutor,
    "api": ApiExecutor,
    "standalone": StandaloneExecutor,
    "agent": AgentExecutor,
}


def create_executor(config: dict[str, Any]) -> BaseExecutor:
    """根据配置创建执行器

    Args:
        config: 执行器配置
            - type: 执行器/宿主类型 (opencode / claude-code / codex / script /
              api / standalone / agent)
            - 其他参数取决于执行器类型

    Returns:
        BaseExecutor: 执行器实例
    """
    executor_type = config.get("type", "opencode")

    executor_cls = HOST_REGISTRY.get(executor_type)
    if executor_cls is None:
        raise ValueError(
            f"不支持的执行器类型: {executor_type}。可用类型: {sorted(HOST_REGISTRY.keys())}"
        )
    return executor_cls(config)


# 导出
from .base import BaseExecutor, ExecutionResult, SessionResult, UnsupportedCapabilityError
from .opencode import OpencodeExecutor
from .api import ApiExecutor
from .standalone import StandaloneExecutor
from .agent import AgentExecutor
from .claude_code import ClaudeCodeExecutor
from .codex import CodexExecutor
from .script import ScriptExecutor
