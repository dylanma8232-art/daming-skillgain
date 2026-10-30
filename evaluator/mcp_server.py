"""MCP Server - 让任何支持 MCP 的 agent 都能调用

A9：从「三个大工具」拆为按阶段可组合的工具，统一透出 host / host_config / profile，
任何 MCP agent 都走同一条 HostProtocol 契约：
    hosts        → 宿主能力 + 注入约定 + 是否已安装
    analyze_skill → skill 静态分析
    plan         → 生成评测计划（缺参返回 needs_user_input，不阻塞）
    run          → 执行已有计划（支持 force/resume）
    evaluate     → 盲评打分（缺投票模型时默认第一个被测模型，不再死锁）
    report       → 生成 HTML 报告
    pipeline     → 一键闭环 plan→run→evaluate→report
    check_connectivity → 连通性检查（先探活）
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from typing import Any

try:
    from mcp import Server, Tool
    from mcp.types import TextContent
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def _json_text(result: Any) -> list:
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


def _error(exc: Exception) -> list:
    return _json_text({"success": False, "error": str(exc)})


def _load_profile_payload(profile: str | None, profile_config: dict | None):
    """A9/A10：MCP 工具统一支持 profile 字符串路径或内联对象。"""
    from .profile import EvalProfile, load_profile

    if profile_config:
        return EvalProfile.from_dict(profile_config)
    if profile:
        return load_profile(profile)
    return None


def _host_kwargs(profile, host, host_config):
    """profile + 显式参数合并成宿主参数。"""
    resolved_host = host or (profile.host if profile else None)
    resolved_config = host_config or (dict(profile.host_config) if profile else None)
    return resolved_host, resolved_config


def _do_hosts() -> dict:
    from .model_discovery import discover_hosts

    return {"hosts": discover_hosts()}


def _do_analyze(skill_path: str) -> dict:
    from .skill_analyzer import analyze_skill

    return analyze_skill(skill_path).to_dict()


def _do_plan(
    *,
    profile=None,
    skill_path: str = "",
    skill_name: str = "",
    task_prompt: str = "",
    input_files: list[str] | None = None,
    models: list[str] | None = None,
    voting_models: list[str] | None = None,
    summary_model: str = "",
    resources: list[dict] | None = None,
    host: str | None = None,
    host_config: dict | None = None,
    repetitions: int | None = None,
    execution_mode: str = "cli-attach",
    skip_connectivity_test: bool = False,
    allow_generated_task_prompt: bool = False,
) -> dict:
    from .cli import PlanGenerateConfig, cmd_plan_generate

    host, host_config = _host_kwargs(profile, host, host_config)
    config = PlanGenerateConfig(
        skill_name=skill_name or (profile.skill_name if profile else ""),
        skill_path=skill_path or (profile.skill_path if profile else ""),
        task_prompt=task_prompt or (profile.task_prompt if profile else ""),
        input_files=input_files or (list(profile.input_files) if profile else []),
        models=models or (list(profile.models) if profile else []),
        voting_models=voting_models or (list(profile.voting_models) if profile else []),
        summary_model=summary_model or (profile.summary_model if profile else None),
        resources_json=(
            json.dumps(resources, ensure_ascii=False)
            if resources
            else (profile.resources_json() if profile else None)
        ),
        repetitions=repetitions if repetitions is not None else (profile.repetitions if profile else None),
        execution_mode=execution_mode or (profile.execution_mode if profile else "cli-attach"),
        skip_connectivity_test=skip_connectivity_test,
        allow_generated_task_prompt=allow_generated_task_prompt
        or (profile.allow_generated_task_prompt if profile else False),
        host=host,
        host_config=host_config,
        non_interactive=True,
    )
    return cmd_plan_generate(config)


def _do_run(plan_dir: str, force: bool = False, resume: bool = True) -> dict:
    from .engine import run_from_plan

    return {
        "run_dir": str(
            run_from_plan(Path(plan_dir), force=force, resume=resume)
        )
    }


def _do_evaluate(
    run_dir: str,
    voting_models: list[str] | None = None,
    host: str | None = None,
    force: bool = False,
) -> dict:
    from .engine import run_blind_vote_phase
    from .model_discovery import discover_models_for_host

    manifest_models: list[str] = []
    manifest_path = Path(run_dir) / "run_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest.get("results", []):
            model = item.get("model", "")
            if model and model not in manifest_models:
                manifest_models.append(model)
    judges = list(voting_models or [])
    if not judges:
        judges = manifest_models[:1] or discover_models_for_host(host)[:1]
    if not judges:
        return {
            "error": "needs_user_input",
            "action": "ask_user",
            "missing_fields": ["voting_models"],
            "message": "没有可用评委模型，请显式提供 voting_models。",
        }
    return run_blind_vote_phase(
        Path(run_dir), voting_models=judges, host=host, force=force
    )


def _do_report(run_dir: str, force: bool = False) -> dict:
    from .engine import generate_existing_run_report

    return generate_existing_run_report(Path(run_dir), force=force)


def _do_pipeline(profile=None, **kwargs) -> dict:
    from .workflow import SkillEvaluationWorkflow

    if profile is not None:
        config = profile.to_workflow_config(auto=True, non_interactive=True)
    else:
        config = {
            "skill_path": kwargs.get("skill_path", ""),
            "skill_name": kwargs.get("skill_name", ""),
            "task_prompt": kwargs.get("task_prompt", ""),
            "input_files": kwargs.get("input_files") or [],
            "models": kwargs.get("models") or [],
            "voting_models": kwargs.get("voting_models") or [],
            "summary_model": kwargs.get("summary_model") or "",
            "execution_mode": kwargs.get("execution_mode", "cli-attach"),
            "host": kwargs.get("host"),
            "host_config": kwargs.get("host_config"),
            "auto": True,
            "non_interactive": True,
        }
        if config["models"] and not config["voting_models"]:
            config["voting_models"] = [config["models"][0]]
        if not config["summary_model"] and config["voting_models"]:
            config["summary_model"] = config["voting_models"][0]
    config.update({k: v for k, v in kwargs.items() if v is not None})
    return SkillEvaluationWorkflow().run(config)


if HAS_MCP:
    server = Server("daming-skillgain")

    @server.tool("hosts")
    async def hosts() -> list[TextContent]:
        """列出所有 Agent 工具宿主的 capabilities、注入约定与是否已安装。"""
        try:
            return _json_text(await asyncio.to_thread(_do_hosts))
        except Exception as e:
            return _error(e)

    @server.tool("analyze_skill")
    async def analyze_skill(skill_path: str) -> list[TextContent]:
        """分析一个 skill（静态分析，不调用模型）。"""
        try:
            return _json_text(await asyncio.to_thread(_do_analyze, skill_path))
        except Exception as e:
            return _error(e)

    @server.tool("plan")
    async def plan(
        skill_path: str = "",
        task_prompt: str = "",
        models: list[str] = None,
        voting_models: list[str] = None,
        summary_model: str = "",
        skill_name: str = "",
        host: str = None,
        host_config: dict = None,
        profile: str = None,
        profile_config: dict = None,
        repetitions: int = None,
    ) -> list[TextContent]:
        """生成评测计划；缺参返回结构化 needs_user_input（不会阻塞等输入）。"""
        try:
            loaded = await asyncio.to_thread(_load_profile_payload, profile, profile_config)
            result = await asyncio.to_thread(
                _do_plan,
                profile=loaded,
                skill_path=skill_path,
                skill_name=skill_name,
                task_prompt=task_prompt,
                models=models,
                voting_models=voting_models,
                summary_model=summary_model,
                host=host,
                host_config=host_config,
                repetitions=repetitions,
            )
            return _json_text(result)
        except Exception as e:
            return _error(e)

    @server.tool("run")
    async def run(plan_dir: str, force: bool = False, resume: bool = True) -> list[TextContent]:
        """执行已有评测计划（默认续跑，force 强制重跑）。"""
        try:
            return _json_text(await asyncio.to_thread(_do_run, plan_dir, force, resume))
        except Exception as e:
            return _error(e)

    @server.tool("evaluate")
    async def evaluate(
        run_dir: str,
        voting_models: list[str] = None,
        host: str = None,
        force: bool = False,
    ) -> list[TextContent]:
        """对已有结果盲评打分（缺评委默认第一个被测模型）。"""
        try:
            return _json_text(
                await asyncio.to_thread(_do_evaluate, run_dir, voting_models, host, force)
            )
        except Exception as e:
            return _error(e)

    @server.tool("report")
    async def report(run_dir: str, force: bool = False) -> list[TextContent]:
        """为已有 run 目录生成自包含 HTML 报告（已存在则续跑跳过）。"""
        try:
            return _json_text(await asyncio.to_thread(_do_report, run_dir, force))
        except Exception as e:
            return _error(e)

    @server.tool("pipeline")
    async def pipeline(
        skill_path: str = "",
        task_prompt: str = "",
        models: list[str] = None,
        voting_models: list[str] = None,
        summary_model: str = "",
        host: str = None,
        host_config: dict = None,
        profile: str = None,
        profile_config: dict = None,
    ) -> list[TextContent]:
        """一键闭环：plan → run → evaluate → report（非交互，不停盘）。"""
        try:
            loaded = await asyncio.to_thread(_load_profile_payload, profile, profile_config)
            result = await asyncio.to_thread(
                _do_pipeline,
                profile=loaded,
                skill_path=skill_path or None,
                task_prompt=task_prompt or None,
                models=models,
                voting_models=voting_models,
                summary_model=summary_model or None,
                host=host,
                host_config=host_config,
            )
            return _json_text(result)
        except Exception as e:
            return _error(e)

    @server.tool("check_connectivity")
    async def check_connectivity(
        model: str,
        host: str = None,
        host_config: dict = None,
    ) -> list[TextContent]:
        """检查模型连通性（先 --version 探活再按需深度检查）。"""
        try:
            from .host_session import run_host_session

            def _check() -> dict:
                session = run_host_session(
                    host=host,
                    model=model,
                    prompt="回答一个字：好",
                    timeout_seconds=60,
                    format_json=True,
                    host_config=host_config,
                )
                return {
                    "model": model,
                    "host": host or "default",
                    "connected": session.exit_code == 0
                    and bool((session.text or session.stdout).strip()),
                    "exit_code": session.exit_code,
                }

            return _json_text(await asyncio.to_thread(_check))
        except Exception as e:
            return _error(e)

    @server.tool("evaluate_skill")
    async def evaluate_skill(
        skill_path: str,
        task_prompt: str,
        models: list[str],
        voting_models: list[str] = None,
        summary_model: str = None,
        executor_type: str = "opencode",
        host: str = None,
        host_config: dict = None,
        profile: str = None,
        profile_config: dict = None,
    ) -> list[TextContent]:
        """兼容旧接口：一键评测一个 skill（等价 pipeline）。"""
        try:
            loaded = await asyncio.to_thread(
                _load_profile_payload,
                profile,
                profile_config
                or {
                    "skill_path": skill_path,
                    "task_prompt": task_prompt,
                    "models": models,
                    "voting_models": voting_models or [models[0]],
                    "summary_model": summary_model or (voting_models or models)[0],
                    "host": host or executor_type,
                    "host_config": host_config or {},
                },
            )
            from .sdk import SkillEvaluator

            evaluator = SkillEvaluator(profile=loaded)
            result = await asyncio.to_thread(
                evaluator.run,
                task_prompt=task_prompt,
                models=models,
                voting_models=voting_models,
                summary_model=summary_model,
            )
            return _json_text(result)
        except Exception as e:
            return _error(e)


def run_mcp_server():
    """启动 MCP Server"""
    if not HAS_MCP:
        raise ImportError("需要安装 mcp: pip install mcp")

    asyncio.run(server.run())
