"""doctor.py - 一键环境体检（Python / 依赖 / 本地 AI 工具 / API Key）。

让新用户运行 `skillgain doctor` 就能看清当前机器可以直接跑哪些宿主，不再盲猜环境配置。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any

from .model_discovery import discover_hosts

CORE_PACKAGES: tuple[str, ...] = ("yaml", "openpyxl", "requests", "httpx")
OPTIONAL_PACKAGES: tuple[tuple[str, str], ...] = (
    ("fastapi", "REST API 服务 (pip install 'daming-skillgain[api]')"),
    ("uvicorn", "REST API 服务 (pip install 'daming-skillgain[api]')"),
    ("mcp", "MCP Server (pip install 'daming-skillgain[mcp]')"),
    ("anthropic", "Anthropic API 执行器 (pip install 'daming-skillgain[anthropic]')"),
)
API_KEY_ENVS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
)


def _is_module_installed(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def run_doctor() -> dict[str, Any]:
    """执行环境体检并返回结构化报告（绝不泄露任何密钥明文）。"""
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 10)

    core_deps = {pkg: _is_module_installed(pkg) for pkg in CORE_PACKAGES}
    optional_deps = {
        pkg: {"installed": _is_module_installed(pkg), "purpose": purpose}
        for pkg, purpose in OPTIONAL_PACKAGES
    }

    hosts_raw = discover_hosts()
    hosts_summary: dict[str, dict[str, Any]] = {}
    ready_hosts: list[str] = []
    for name, info in hosts_raw.items():
        if "alias_of" in info:
            continue
        installed = info.get("installed")
        hosts_summary[name] = {
            "installed": installed,
            "version": info.get("version", ""),
            "capabilities": info.get("capabilities", {}),
            "probe_error": info.get("probe_error", ""),
        }
        if installed is True:
            ready_hosts.append(name)

    api_keys_configured = [env for env in API_KEY_ENVS if bool(os.environ.get(env))]

    recommendations: list[str] = []
    if not py_ok:
        recommendations.append(
            f"当前 Python 为 {py_ver}，推荐使用 Python 3.10+（或 3.12+）。"
        )
    missing_core = [pkg for pkg, ok in core_deps.items() if not ok]
    if missing_core:
        recommendations.append(
            f"缺少核心依赖 {', '.join(missing_core)}，请运行: pip install -r requirements.txt"
        )
    if ready_hosts:
        recommendations.append(
            f"已检测到可直接运行的原生 AI 工具：{', '.join(ready_hosts)}。可直接运行 `skillgain simple` 开始测评。"
        )
    elif api_keys_configured:
        recommendations.append(
            f"检测到 API Key（{', '.join(api_keys_configured)}），但未检测到本地 Agent CLI；"
            "可使用 `--host script` 包装任意工具，或 `--host api` 进行纯文本任务评测。"
        )
    else:
        recommendations.append(
            "未检测到本地 AI 工具或 API Key。先运行 `skillgain demo` 体验离线报告，再安装 OpenCode / Claude Code / Codex 或编写脚本适配器（见 references/hosts.md）。"
        )

    return {
        "success": all(core_deps.values()),
        "python": {"version": py_ver, "recommended_ok": py_ok},
        "core_dependencies": core_deps,
        "optional_dependencies": optional_deps,
        "ready_hosts": ready_hosts,
        "hosts": hosts_summary,
        "configured_api_key_envs": api_keys_configured,
        "recommendations": recommendations,
    }
