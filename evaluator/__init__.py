"""Skill Evaluator - 标准化的 skill 评测工具"""

# 核心模块
from .skill_analyzer import analyze_skill
from .workflow import SkillEvaluationWorkflow
from .sdk import SkillEvaluator, evaluate_skill

# 执行器
from .executors import create_executor, BaseExecutor, ExecutionResult
from .executors.opencode import OpencodeExecutor
from .executors.api import ApiExecutor
from .executors.standalone import StandaloneExecutor
from .executors.agent import AgentExecutor

# 模型发现
from .model_discovery import ModelDiscovery, interactive_setup

# 版本
__version__ = "7.0.1"

# 快捷访问
__all__ = [
    # SDK
    "SkillEvaluator",
    "evaluate_skill",
    
    # 工作流
    "SkillEvaluationWorkflow",
    
    # 执行器
    "create_executor",
    "BaseExecutor",
    "ExecutionResult",
    "OpencodeExecutor",
    "ApiExecutor",
    "StandaloneExecutor",
    "AgentExecutor",
    
    # 模型发现
    "ModelDiscovery",
    "interactive_setup",
    
    # 工具函数
    "analyze_skill",
]
