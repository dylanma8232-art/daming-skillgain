"""Python SDK - 简单易用的 Python 接口"""
from __future__ import annotations
from typing import Any

from .workflow import SkillEvaluationWorkflow
from .executors import create_executor
from .model_discovery import ModelDiscovery


class SkillEvaluator:
    """Skill 评测器 Python SDK"""
    
    def __init__(
        self,
        skill_path: str = None,
        executor_type: str = None,
        executor_config: dict = None,
        auto_discover: bool = True,
        **kwargs
    ):
        """初始化评测器
        
        Args:
            skill_path: 被评测 skill 目录路径
            executor_type: 执行器类型 (opencode / api) - 如果不指定则自动检测
            executor_config: 执行器配置
            auto_discover: 是否自动检测本地工具
            **kwargs: 其他配置
        """
        self.skill_path = skill_path
        
        # 自动检测
        if auto_discover and not executor_type and not executor_config:
            discovery = ModelDiscovery()
            result = discovery.discover()
            recommendation = discovery.get_recommendation(result)
            
            if recommendation["executor_type"]:
                executor_type = recommendation["executor_type"]
                executor_config = recommendation["executor_config"]
                self._available_models = recommendation["models"]
            else:
                # 需要手动配置
                raise ValueError(
                    "未检测到本地工具或 API key，请手动配置：\n"
                    "  evaluator = SkillEvaluator(\n"
                    "      skill_path='./my-skill',\n"
                    "      executor_type='api',\n"
                    "      executor_config={'provider': 'openai', 'api_key': 'sk-xxx'}\n"
                    "  )"
                )
        
        # 创建执行器
        if executor_config:
            # 确保 executor_config 中有 type 字段
            if "type" not in executor_config and executor_type:
                executor_config["type"] = executor_type
            self.executor = create_executor(executor_config)
        elif executor_type:
            self.executor = create_executor({"type": executor_type, **kwargs})
        else:
            self.executor = create_executor({"type": "opencode"})
        
        # 创建工作流
        self.workflow = SkillEvaluationWorkflow(executor=self.executor)
    
    def run(
        self,
        task_prompt: str,
        models: list[str],
        voting_models: list[str] = None,
        summary_model: str = None,
        **kwargs
    ) -> dict[str, Any]:
        """执行评测
        
        Args:
            task_prompt: 评测任务描述
            models: 参与评测的模型列表
            voting_models: 评委模型列表（默认使用第一个模型）
            summary_model: 总结模型（默认使用第一个模型）
            **kwargs: 其他参数
            
        Returns:
            dict: 评测结果
        """
        if not self.skill_path:
            raise ValueError("未指定 skill_path")
        
        if not voting_models:
            voting_models = [models[0]]
        
        if not summary_model:
            summary_model = models[0]
        
        config = {
            "skill_path": self.skill_path,
            "task_prompt": task_prompt,
            "models": models,
            "voting_models": voting_models,
            "summary_model": summary_model,
            **kwargs
        }
        
        return self.workflow.run(config)
    
    def analyze_skill(self, skill_path: str = None) -> dict[str, Any]:
        """分析 skill
        
        Args:
            skill_path: skill 目录路径（默认使用初始化时的路径）
            
        Returns:
            dict: skill 分析结果
        """
        from .skill_analyzer import analyze_skill
        
        path = skill_path or self.skill_path
        if not path:
            raise ValueError("未指定 skill_path")
        
        analysis = analyze_skill(path)
        return analysis.to_dict()
    
    def check_connectivity(self, model: str) -> bool:
        """检查模型连通性
        
        Args:
            model: 模型名称
            
        Returns:
            bool: 是否可连通
        """
        return self.executor.check_connectivity(model)
    
    def list_models(self) -> list[str]:
        """列出可用模型
        
        Returns:
            list[str]: 可用模型列表
        """
        return self.executor.list_models()


# 快捷函数
def evaluate_skill(
    skill_path: str,
    task_prompt: str,
    models: list[str],
    voting_models: list[str] = None,
    summary_model: str = None,
    executor_type: str = "opencode",
    **kwargs
) -> dict[str, Any]:
    """快捷评测函数
    
    Args:
        skill_path: 被评测 skill 目录路径
        task_prompt: 评测任务描述
        models: 参与评测的模型列表
        voting_models: 评委模型列表
        summary_model: 总结模型
        executor_type: 执行器类型
        **kwargs: 其他参数
        
    Returns:
        dict: 评测结果
    """
    evaluator = SkillEvaluator(
        skill_path=skill_path,
        executor_type=executor_type,
        **kwargs
    )
    
    return evaluator.run(
        task_prompt=task_prompt,
        models=models,
        voting_models=voting_models,
        summary_model=summary_model,
        **kwargs
    )
