"""Python SDK - 简单易用的 Python 接口

A10：支持 `SkillEvaluator(profile=...)`；`run()` 透传 host / host_config / repetitions；
自动检测改为「按宿主分发模型发现」，不再硬依赖 ModelDiscovery（发现失败不阻断 profile 路径）。
"""
from __future__ import annotations
from typing import Any

from .profile import EvalProfile, load_profile
from .workflow import SkillEvaluationWorkflow
from .executors import create_executor


class SkillEvaluator:
    """Skill 评测器 Python SDK"""

    def __init__(
        self,
        skill_path: str = None,
        executor_type: str = None,
        executor_config: dict = None,
        auto_discover: bool = True,
        profile: EvalProfile | str | None = None,
        **kwargs
    ):
        """初始化评测器

        Args:
            skill_path: 被评测 skill 目录路径
            executor_type: 执行器类型 (opencode / claude-code / codex / cursor-agent / api ...)
            executor_config: 执行器配置
            auto_discover: 是否自动检测本地工具（profile 路径下默认不做硬依赖检测）
            profile: EvalProfile 实例或 profile JSON 路径（一次性配置，可复用）
            **kwargs: 其他配置
        """
        if isinstance(profile, str):
            profile = load_profile(profile)
        self.profile: EvalProfile | None = profile
        self.skill_path = skill_path or (profile.skill_path if profile else None)

        host = executor_type or (profile.host if profile and profile.host else None)
        host_config: dict[str, Any] = dict(executor_config or {})
        if profile and profile.host_config:
            host_config = {**profile.host_config, **host_config}
        if host:
            host_config.setdefault("type", host)

        self.host = host
        self.host_config = host_config

        if host or host_config:
            self.executor = create_executor(host_config or {"type": host})
        elif auto_discover:
            # 按宿主分发模型发现；失败不阻断（返回空即可，后续显式选择宿主）。
            from .model_discovery import discover_models_for_host

            self._available_models = discover_models_for_host(None)
            self.executor = create_executor({"type": "opencode"})
        else:
            self.executor = create_executor({"type": "opencode"})

        self.workflow = SkillEvaluationWorkflow(executor=self.executor)

    def run(
        self,
        task_prompt: str = None,
        models: list[str] = None,
        voting_models: list[str] = None,
        summary_model: str = None,
        **kwargs
    ) -> dict[str, Any]:
        """执行评测

        Args:
            task_prompt: 评测任务描述（缺省取 profile.task_prompt）
            models: 参与评测的模型列表（缺省取 profile.models）
            voting_models: 评委模型列表（缺省取 profile.voting_models 或第一个模型）
            summary_model: 总结模型（缺省取 profile.summary_model 或第一个评委）
            **kwargs: 其他参数（host / host_config / repetitions 等透传）
        """
        profile = self.profile
        if profile is not None:
            config = profile.to_workflow_config(auto=True, non_interactive=True)
        else:
            config = {
                "skill_path": self.skill_path,
                "skill_name": "",
                "task_prompt": "",
                "allow_generated_task_prompt": False,
                "input_files": [],
                "models": [],
                "voting_models": [],
                "summary_model": "",
                "resources_json": None,
                "execution_mode": "cli-attach",
                "host": self.host,
                "host_config": dict(self.host_config) if self.host_config else None,
                "auto": True,
                "non_interactive": True,
            }

        if not self.skill_path:
            raise ValueError("未指定 skill_path")
        config["skill_path"] = self.skill_path

        models = list(models or config.get("models") or [])
        if profile is None and not models:
            from .model_discovery import discover_models_for_host

            models = discover_models_for_host(config.get("host"))[:2]
        if not models:
            raise ValueError("未指定 models，且本机未发现可用模型")

        voting_models = list(voting_models or config.get("voting_models") or [])
        if not voting_models:
            voting_models = [models[0]]
        summary_model = summary_model or config.get("summary_model") or voting_models[0]

        config.update(
            {
                "task_prompt": task_prompt if task_prompt is not None else config.get("task_prompt", ""),
                "models": models,
                "voting_models": voting_models,
                "summary_model": summary_model,
            }
        )
        config.update(kwargs)
        config.setdefault("auto", True)
        config.setdefault("non_interactive", True)
        if "host" not in kwargs and self.host is not None:
            config["host"] = self.host
        elif config.get("host") is None and profile is not None:
            config["host"] = profile.host or None

        if "host_config" not in kwargs and self.host_config:
            config["host_config"] = dict(self.host_config)
        elif not config.get("host_config") and profile is not None:
            config["host_config"] = dict(profile.host_config) or None

        if config.get("repetitions") is None and profile is not None:
            config["repetitions"] = profile.repetitions

        return self.workflow.run(config)

    def analyze_skill(self, skill_path: str = None) -> dict[str, Any]:
        """分析 skill"""
        from .skill_analyzer import analyze_skill

        path = skill_path or self.skill_path
        if not path:
            raise ValueError("未指定 skill_path")

        analysis = analyze_skill(path)
        return analysis.to_dict()

    def check_connectivity(self, model: str) -> bool:
        """检查模型连通性"""
        return self.executor.check_connectivity(model)

    def list_models(self) -> list[str]:
        """列出可用模型"""
        return self.executor.list_models()


# 快捷函数
def evaluate_skill(
    skill_path: str,
    task_prompt: str,
    models: list[str],
    voting_models: list[str] = None,
    summary_model: str = None,
    executor_type: str = "opencode",
    profile: EvalProfile | str | None = None,
    **kwargs
) -> dict[str, Any]:
    """快捷评测函数"""
    evaluator = SkillEvaluator(
        skill_path=skill_path,
        executor_type=None if profile is not None else executor_type,
        profile=profile,
        **kwargs
    )

    return evaluator.run(
        task_prompt=task_prompt,
        models=models,
        voting_models=voting_models,
        summary_model=summary_model,
        **kwargs
    )
