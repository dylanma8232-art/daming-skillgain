"""Agent 执行器 - 支持工具调用，让模型自己读取文件"""
from __future__ import annotations
import json
import os
import subprocess
import shlex
from pathlib import Path
from typing import Any

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from .base import BaseExecutor, ExecutionResult


# 定义工具
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径"
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出目录下的文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "执行 shell 命令",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的命令"
                    }
                },
                "required": ["command"]
            }
        }
    }
]


class AgentExecutor(BaseExecutor):
    host_name = "agent"
    """Agent 执行器 - 支持工具调用"""
    
    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        self.provider = self.config.get("provider", "openai")
        self.api_key = self.config.get("api_key") or self._get_api_key()
        self.base_url = self.config.get("base_url", self._get_default_base_url())
        self.timeout = self.config.get("timeout", 120)
        self.max_iterations = self.config.get("max_iterations", 10)
        self.working_dir = self.config.get("working_dir", os.getcwd())
    
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
        """执行 Agent 任务"""
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
                error="缺少 API key",
                exit_code=-1
            )
        
        # Agent 循环
        messages = [{"role": "user", "content": prompt}]
        iteration = 0
        
        while iteration < self.max_iterations:
            iteration += 1
            
            # 调用模型
            result = self._call_model(model, messages)
            
            if not result["success"]:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=result["error"],
                    exit_code=-1
                )
            
            # 检查是否有工具调用
            response = result["response"]
            message = response["choices"][0]["message"]
            
            if not message.get("tool_calls"):
                # 没有工具调用，返回最终结果
                return ExecutionResult(
                    success=True,
                    output=message["content"],
                    metadata={"iterations": iteration}
                )
            
            # 处理工具调用
            messages.append(message)
            
            for tool_call in message["tool_calls"]:
                function_name = tool_call["function"]["name"]
                function_args = json.loads(tool_call["function"]["arguments"])
                
                # 执行工具
                tool_result = self._execute_tool(function_name, function_args)
                
                # 添加工具结果到消息
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })
        
        return ExecutionResult(
            success=False,
            output="",
            error=f"超过最大迭代次数 ({self.max_iterations})",
            exit_code=-1
        )
    
    def _call_model(self, model: str, messages: list) -> dict[str, Any]:
        """调用模型 API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "messages": messages,
            "tools": TOOLS,
            "max_tokens": 4096
        }
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=data
                )
                response.raise_for_status()
                return {"success": True, "response": response.json()}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _execute_tool(self, function_name: str, function_args: dict) -> Any:
        """执行工具"""
        try:
            if function_name == "read_file":
                return self._read_file(function_args["path"])
            elif function_name == "write_file":
                return self._write_file(function_args["path"], function_args["content"])
            elif function_name == "list_files":
                return self._list_files(function_args["path"])
            elif function_name == "execute_command":
                return self._execute_command(function_args["command"])
            else:
                return {"error": f"未知工具: {function_name}"}
        except Exception as e:
            return {"error": str(e)}
    
    def _read_file(self, path: str) -> dict:
        """读取文件"""
        try:
            # 处理相对路径
            if not os.path.isabs(path):
                path = os.path.join(self.working_dir, path)
            
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            
            return {
                "success": True,
                "path": path,
                "content": content[:10000],  # 限制长度
                "truncated": len(content) > 10000
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _write_file(self, path: str, content: str) -> dict:
        """写入文件"""
        try:
            # 处理相对路径
            if not os.path.isabs(path):
                path = os.path.join(self.working_dir, path)
            
            # 创建目录
            os.makedirs(os.path.dirname(path), exist_ok=True)
            
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            
            return {"success": True, "path": path}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _list_files(self, path: str) -> dict:
        """列出文件"""
        try:
            # 处理相对路径
            if not os.path.isabs(path):
                path = os.path.join(self.working_dir, path)
            
            files = []
            for item in os.listdir(path):
                full_path = os.path.join(path, item)
                files.append({
                    "name": item,
                    "path": full_path,
                    "is_dir": os.path.isdir(full_path)
                })
            
            return {"success": True, "files": files}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _execute_command(self, command: str) -> dict:
        """执行命令"""
        try:
            parsed_command = shlex.split(command)
            result = subprocess.run(
                parsed_command,
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self.working_dir
            )
            
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[:5000],  # 限制长度
                "stderr": result.stderr[:5000],
                "returncode": result.returncode
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def check_connectivity(self, model: str) -> bool:
        """检查连通性"""
        result = self.execute(model, "回答一个字：好")
        return result.success
    
    def list_models(self) -> list[str]:
        """列出模型"""
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
            return False, "缺少 API key"
        
        return True, ""
