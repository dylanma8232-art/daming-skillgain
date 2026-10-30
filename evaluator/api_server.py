"""REST API Server - 提供 HTTP 接口"""

from __future__ import annotations
import asyncio
import os
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Header, Security, Depends
    from fastapi.security import APIKeyHeader
    from pydantic import BaseModel

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    app = None

if HAS_FASTAPI:
    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def verify_api_key(
        api_key: str = Security(api_key_header),
        authorization: str = Header(None),
    ):
        expected_key = os.environ.get("EVALUATOR_API_KEY") or os.environ.get("API_KEY")
        if not expected_key:
            raise HTTPException(status_code=401, detail="API key not configured on server")

        token = api_key
        if not token and authorization:
            if authorization.startswith("Bearer "):
                token = authorization[7:]
            else:
                token = authorization

        if not token or token != expected_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
        return token

    app = FastAPI(
        title="Skill Evaluator API",
        version="1.0.0",
        dependencies=[Depends(verify_api_key)],
    )

    class EvaluateRequest(BaseModel):
        task_prompt: str
        models: list[str]
        voting_models: list[str] = None
        summary_model: str = None

    class AnalyzeRequest(BaseModel):
        pass

    @app.post("/api/evaluate")
    async def evaluate(request: EvaluateRequest):
        """执行评测"""
        from .sdk import SkillEvaluator

        try:
            skill_path = os.environ.get("EVALUATOR_SKILL_PATH") or os.environ.get("SKILL_PATH") or "."
            executor_type = os.environ.get("EVALUATOR_EXECUTOR_TYPE")
            evaluator = SkillEvaluator(
                skill_path=skill_path,
                executor_type=executor_type,
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
    async def analyze(request: AnalyzeRequest = None):
        """分析 skill"""
        from .sdk import SkillEvaluator

        try:
            skill_path = os.environ.get("EVALUATOR_SKILL_PATH") or os.environ.get("SKILL_PATH") or "."
            evaluator = SkillEvaluator(skill_path=skill_path)
            return await asyncio.to_thread(evaluator.analyze_skill)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/models")
    async def list_models():
        """列出可用模型"""
        from .sdk import SkillEvaluator

        try:
            executor_type = os.environ.get("EVALUATOR_EXECUTOR_TYPE")
            evaluator = SkillEvaluator(executor_type=executor_type)
            models = await asyncio.to_thread(evaluator.list_models)
            return {"models": models}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/health")
    async def health():
        """健康检查"""
        return {"status": "ok"}


def run_server(host: str = "127.0.0.1", port: int = 8000):
    """启动 API 服务"""
    if not HAS_FASTAPI:
        raise ImportError("需要安装 fastapi 和 uvicorn: pip install fastapi uvicorn")

    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Skill Evaluator REST API Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
