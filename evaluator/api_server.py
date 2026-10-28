"""REST API Server - 提供 HTTP 接口"""

from __future__ import annotations
import asyncio
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    app = None

if HAS_FASTAPI:
    app = FastAPI(title="Skill Evaluator API", version="1.0.0")

    class EvaluateRequest(BaseModel):
        skill_path: str
        task_prompt: str
        models: list[str]
        voting_models: list[str] = None
        summary_model: str = None
        executor_type: str = "opencode"
        executor_config: dict = None

    class AnalyzeRequest(BaseModel):
        skill_path: str

    @app.post("/api/evaluate")
    async def evaluate(request: EvaluateRequest):
        """执行评测"""
        from .sdk import SkillEvaluator

        try:
            evaluator = SkillEvaluator(
                skill_path=request.skill_path,
                executor_type=request.executor_type,
                executor_config=request.executor_config,
            )

            result = await asyncio.to_thread(
                evaluator.run,
                task_prompt=request.task_prompt,
                models=request.models,
                voting_models=request.voting_models,
                summary_model=request.summary_model,
            )

            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/analyze")
    async def analyze(request: AnalyzeRequest):
        """分析 skill"""
        from .sdk import SkillEvaluator

        try:
            evaluator = SkillEvaluator(skill_path=request.skill_path)
            return await asyncio.to_thread(evaluator.analyze_skill)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/models")
    async def list_models(executor_type: str = "opencode"):
        """列出可用模型"""
        from .sdk import SkillEvaluator

        try:
            evaluator = SkillEvaluator(executor_type=executor_type)
            models = await asyncio.to_thread(evaluator.list_models)
            return {"models": models}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/health")
    async def health():
        """健康检查"""
        return {"status": "ok"}


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """启动 API 服务"""
    if not HAS_FASTAPI:
        raise ImportError("需要安装 fastapi 和 uvicorn: pip install fastapi uvicorn")

    import uvicorn

    uvicorn.run(app, host=host, port=port)
