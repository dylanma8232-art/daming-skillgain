"""MCP Server - 让任何支持 MCP 的 agent 都能调用"""
from __future__ import annotations
import json
from typing import Any

try:
    from mcp import Server, Tool
    from mcp.types import TextContent
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

if HAS_MCP:
    server = Server("daming-skillgain")
    
    @server.tool("evaluate_skill")
    async def evaluate_skill(
        skill_path: str,
        task_prompt: str,
        models: list[str],
        voting_models: list[str] = None,
        summary_model: str = None,
        executor_type: str = "opencode"
    ) -> list[TextContent]:
        """评测一个 skill
        
        Args:
            skill_path: 被评测 skill 的目录路径
            task_prompt: 评测任务描述
            models: 参与评测的模型列表
            voting_models: 评委模型列表
            summary_model: 总结模型
            executor_type: 执行器类型 (opencode / api)
        """
        from .sdk import SkillEvaluator
        
        try:
            evaluator = SkillEvaluator(
                skill_path=skill_path,
                executor_type=executor_type
            )
            
            result = evaluator.run(
                task_prompt=task_prompt,
                models=models,
                voting_models=voting_models,
                summary_model=summary_model
            )
            
            return [TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, indent=2)
            )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
            )]
    
    @server.tool("analyze_skill")
    async def analyze_skill(
        skill_path: str
    ) -> list[TextContent]:
        """分析一个 skill
        
        Args:
            skill_path: skill 的目录路径
        """
        from .sdk import SkillEvaluator
        
        try:
            evaluator = SkillEvaluator(skill_path=skill_path)
            result = evaluator.analyze_skill()
            
            return [TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, indent=2)
            )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
            )]
    
    @server.tool("check_connectivity")
    async def check_connectivity(
        model: str,
        executor_type: str = "opencode"
    ) -> list[TextContent]:
        """检查模型连通性
        
        Args:
            model: 模型名称
            executor_type: 执行器类型
        """
        from .sdk import SkillEvaluator
        
        try:
            evaluator = SkillEvaluator(executor_type=executor_type)
            result = evaluator.check_connectivity(model)
            
            return [TextContent(
                type="text",
                text=json.dumps({"model": model, "connected": result}, ensure_ascii=False)
            )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
            )]


def run_mcp_server():
    """启动 MCP Server"""
    if not HAS_MCP:
        raise ImportError("需要安装 mcp: pip install mcp")
    
    import asyncio
    asyncio.run(server.run())
