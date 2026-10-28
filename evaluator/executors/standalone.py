"""独立执行器 - 内置 HTTP 客户端，不依赖任何 CLI 工具"""
from __future__ import annotations
import os
from typing import Any

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from .base import BaseExecutor, ExecutionResult


class StandaloneExecutor(BaseExecutor):
    """独立执行器 - 内置 HTTP 客户端，直接调用模型 API"""
    
    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        self.provider = self.config.get("provider", "openai")
        self.api_key = self.config.get("api_key") or self._get_api_key()
        self.base_url = self.config.get("base_url", self._get_default_base_url())
        self.timeout = self.config.get("timeout", 120)
    
    def _get_api_key(self) -> str:
        """从环境变量获取 API key"""
        env_keys = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "minimax": "MINIMAX_API_KEY",
            "dashscope": "DASHSCOPE_API_KEY",
        }
        env_key = env_keys.get(self.provider, "OPENAI_API_KEY")
        return os.environ.get(env_key, "")
    
    def _get_default_base_url(self) -> str:
        """获取默认 base URL"""
        urls = {
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
            "minimax": "https://api.minimax.chat/v1",
            "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
        return urls.get(self.provider, "https://api.openai.com/v1")
    
    def execute(self, model: str, prompt: str, **kwargs) -> ExecutionResult:
        """执行模型调用"""
        if not HAS_HTTPX:
            return ExecutionResult(
                success=False,
                output="",
                error="需要安装 httpx: pip install httpx",
                exit_code=-1
            )
        
        if not self.api_key:
            return ExecutionResult(
                success=False,
                output="",
                error=f"缺少 API key，请设置环境变量或在配置中提供",
                exit_code=-1
            )
        
        try:
            if self.provider == "anthropic":
                return self._execute_anthropic(model, prompt, **kwargs)
            else:
                return self._execute_openai_compatible(model, prompt, **kwargs)
        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                exit_code=-1
            )
    
    def _execute_openai_compatible(self, model: str, prompt: str, **kwargs) -> ExecutionResult:
        """调用 OpenAI 兼容 API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": kwargs.get("max_tokens", 4096)
        }
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
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
        except httpx.HTTPStatusError as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"API 错误: {e.response.status_code} - {e.response.text}",
                exit_code=-1
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
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        
        data = {
            "model": model,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "messages": [{"role": "user", "content": prompt}]
        }
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/v1/messages",
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
        except httpx.HTTPStatusError as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"API 错误: {e.response.status_code} - {e.response.text}",
                exit_code=-1
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
        """列出可用模型"""
        # 返回常见模型列表
        common_models = {
            "openai": ["gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
            "anthropic": ["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"],
            "minimax": ["MiniMax-M2.7", "MiniMax-M2.5"],
            "dashscope": ["qwen-max", "qwen-plus", "qwen-turbo"],
        }
        return common_models.get(self.provider, [])
    
    def validate_config(self) -> tuple[bool, str]:
        """验证配置"""
        if not HAS_HTTPX:
            return False, "需要安装 httpx: pip install httpx"
        
        if not self.api_key:
            return False, f"缺少 API key，请设置环境变量"
        
        return True, ""
