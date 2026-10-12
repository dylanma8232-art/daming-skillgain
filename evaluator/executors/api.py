"""API 执行器 - 直接调用模型 API"""
from __future__ import annotations
import json
import os
from typing import Any

from .base import BaseExecutor, ExecutionResult


class ApiExecutor(BaseExecutor):
    """直接调用模型 API"""
    
    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        self.provider = self.config.get("provider", "openai")
        self.api_key = self.config.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = self.config.get("base_url", "")
        self.timeout = self.config.get("timeout", 120)
    
    def execute(self, model: str, prompt: str, **kwargs) -> ExecutionResult:
        """通过 API 执行"""
        try:
            if self.provider == "openai":
                return self._execute_openai(model, prompt, **kwargs)
            elif self.provider == "anthropic":
                return self._execute_anthropic(model, prompt, **kwargs)
            else:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"不支持的 provider: {self.provider}",
                    exit_code=-1
                )
        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                exit_code=-1
            )
    
    def _execute_openai(self, model: str, prompt: str, **kwargs) -> ExecutionResult:
        """调用 OpenAI API"""
        try:
            import httpx
        except ImportError:
            return ExecutionResult(
                success=False,
                output="",
                error="需要安装 httpx: pip install httpx",
                exit_code=-1
            )
        
        base_url = self.base_url or "https://api.openai.com/v1"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096
        }
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=data
                )
                response.raise_for_status()
                result = response.json()
                
                output = result["choices"][0]["message"]["content"]
                return ExecutionResult(
                    success=True,
                    output=output,
                    metadata={"usage": result.get("usage", {})}
                )
        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                exit_code=-1
            )
    
    def _execute_anthropic(self, model: str, prompt: str, **kwargs) -> ExecutionResult:
        """调用 Anthropic API"""
        try:
            import httpx
        except ImportError:
            return ExecutionResult(
                success=False,
                output="",
                error="需要安装 httpx: pip install httpx",
                exit_code=-1
            )
        
        api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        
        data = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}]
        }
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=data
                )
                response.raise_for_status()
                result = response.json()
                
                output = result["content"][0]["text"]
                return ExecutionResult(
                    success=True,
                    output=output,
                    metadata={"usage": result.get("usage", {})}
                )
        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                exit_code=-1
            )
    
    def check_connectivity(self, model: str) -> bool:
        """检查模型连通性"""
        result = self.execute(model, "回答一个字：好")
        return result.success
    
    def list_models(self) -> list[str]:
        """列出可用模型（需要配置）"""
        return self.config.get("available_models", [])
    
    def validate_config(self) -> tuple[bool, str]:
        """验证配置"""
        if not self.api_key:
            return False, "缺少 API key"
        return True, ""
