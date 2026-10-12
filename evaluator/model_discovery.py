"""模型发现器 - 自动检测本地可用的模型和工具"""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path
from typing import Any


class ModelDiscovery:
    """自动检测本地可用的模型和工具"""
    
    def discover(self) -> dict[str, Any]:
        """发现所有可用的模型和工具
        
        Returns:
            dict: 发现结果
                - tools: 可用工具列表
                - models: 可用模型列表
                - has_local: 是否有本地工具
                - need_config: 是否需要手动配置
        """
        tools = []
        models = []
        
        # 检测 opencode
        opencode_result = self._detect_opencode()
        if opencode_result["available"]:
            tools.append(opencode_result)
            models.extend(opencode_result.get("models", []))
        
        # 检测 ollama
        ollama_result = self._detect_ollama()
        if ollama_result["available"]:
            tools.append(ollama_result)
            models.extend(ollama_result.get("models", []))
        
        # 检测 lmstudio
        lmstudio_result = self._detect_lmstudio()
        if lmstudio_result["available"]:
            tools.append(lmstudio_result)
            models.extend(lmstudio_result.get("models", []))
        
        # 检测 vllm
        vllm_result = self._detect_vllm()
        if vllm_result["available"]:
            tools.append(vllm_result)
            models.extend(vllm_result.get("models", []))
        
        # 检测环境变量中的 API key
        api_keys = self._detect_api_keys()
        
        return {
            "tools": tools,
            "models": models,
            "api_keys": api_keys,
            "has_local": len(tools) > 0,
            "has_api_key": len(api_keys) > 0,
            "need_config": len(tools) == 0 and len(api_keys) == 0
        }
    
    def _detect_opencode(self) -> dict[str, Any]:
        """检测 opencode"""
        result = {
            "name": "opencode",
            "available": False,
            "models": [],
            "config": {}
        }
        
        # 检查 CLI 是否存在
        try:
            proc = subprocess.run(
                ["opencode", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if proc.returncode == 0:
                result["available"] = True
                result["version"] = proc.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        # 检查 GUI 配置
        gui_config = Path.home() / "Library" / "Application Support" / "ai.opencode.desktop" / "opencode.global.dat"
        if gui_config.exists():
            try:
                data = json.loads(gui_config.read_text())
                model_config = json.loads(data.get("model", "{}"))
                user_models = model_config.get("user", [])
                
                for m in user_models:
                    if m.get("visibility") == "show":
                        model_id = m.get("modelID", "")
                        provider = m.get("providerID", "")
                        if model_id and provider:
                            result["models"].append(f"{provider}/{model_id}")
            except Exception:
                pass
        
        # 检查 CLI 模型列表
        if result["available"]:
            try:
                proc = subprocess.run(
                    ["opencode", "models"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if proc.returncode == 0:
                    for line in proc.stdout.splitlines():
                        line = line.strip()
                        if line and "/" in line and not line.startswith("#"):
                            model = line.split()[0]
                            if model not in result["models"]:
                                result["models"].append(model)
            except Exception:
                pass
        
        return result
    
    def _detect_ollama(self) -> dict[str, Any]:
        """检测 ollama"""
        result = {
            "name": "ollama",
            "available": False,
            "models": [],
            "config": {}
        }
        
        # 检查 CLI 是否存在
        try:
            proc = subprocess.run(
                ["ollama", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if proc.returncode == 0:
                result["available"] = True
                result["version"] = proc.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        # 检查 API 是否可用
        try:
            import httpx
            with httpx.Client(timeout=2) as client:
                response = client.get("http://localhost:11434/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    for model in data.get("models", []):
                        result["models"].append(model["name"])
        except Exception:
            pass
        
        return result
    
    def _detect_lmstudio(self) -> dict[str, Any]:
        """检测 lmstudio"""
        result = {
            "name": "lmstudio",
            "available": False,
            "models": [],
            "config": {}
        }
        
        # 检查 API 是否可用
        try:
            import httpx
            with httpx.Client(timeout=2) as client:
                response = client.get("http://localhost:1234/v1/models")
                if response.status_code == 200:
                    data = response.json()
                    for model in data.get("data", []):
                        result["models"].append(model["id"])
                    result["available"] = True
        except Exception:
            pass
        
        return result
    
    def _detect_vllm(self) -> dict[str, Any]:
        """检测 vllm"""
        result = {
            "name": "vllm",
            "available": False,
            "models": [],
            "config": {}
        }
        
        # 检查 API 是否可用
        try:
            import httpx
            with httpx.Client(timeout=2) as client:
                response = client.get("http://localhost:8000/v1/models")
                if response.status_code == 200:
                    data = response.json()
                    for model in data.get("data", []):
                        result["models"].append(model["id"])
                    result["available"] = True
        except Exception:
            pass
        
        return result
    
    def _detect_api_keys(self) -> dict[str, str]:
        """检测环境变量中的 API key"""
        api_keys = {}
        
        # OpenAI
        if os.environ.get("OPENAI_API_KEY"):
            api_keys["openai"] = os.environ["OPENAI_API_KEY"]
        
        # Anthropic
        if os.environ.get("ANTHROPIC_API_KEY"):
            api_keys["anthropic"] = os.environ["ANTHROPIC_API_KEY"]
        
        # MiniMax
        if os.environ.get("MINIMAX_API_KEY"):
            api_keys["minimax"] = os.environ["MINIMAX_API_KEY"]
        
        # 通义千问
        if os.environ.get("DASHSCOPE_API_KEY"):
            api_keys["dashscope"] = os.environ["DASHSCOPE_API_KEY"]
        
        return api_keys
    
    def get_recommendation(self, discovery_result: dict) -> dict[str, Any]:
        """根据发现结果给出推荐配置
        
        Args:
            discovery_result: discover() 的返回结果
            
        Returns:
            dict: 推荐配置
        """
        recommendation = {
            "executor_type": None,
            "executor_config": {},
            "models": [],
            "message": ""
        }
        
        # 优先使用本地工具
        if discovery_result["has_local"]:
            # 选择第一个可用的本地工具
            tool = discovery_result["tools"][0]
            recommendation["executor_type"] = tool["name"]
            recommendation["executor_config"] = tool.get("config", {})
            recommendation["models"] = tool.get("models", [])
            recommendation["message"] = f"检测到本地工具: {tool['name']}，可用模型 {len(tool['models'])} 个"
        
        # 其次使用 API key
        elif discovery_result["has_api_key"]:
            api_keys = discovery_result["api_keys"]
            
            if "openai" in api_keys:
                recommendation["executor_type"] = "api"
                recommendation["executor_config"] = {
                    "provider": "openai",
                    "api_key": api_keys["openai"]
                }
                recommendation["models"] = ["gpt-4", "gpt-4o", "gpt-3.5-turbo"]
                recommendation["message"] = "检测到 OpenAI API key"
            
            elif "anthropic" in api_keys:
                recommendation["executor_type"] = "api"
                recommendation["executor_config"] = {
                    "provider": "anthropic",
                    "api_key": api_keys["anthropic"]
                }
                recommendation["models"] = ["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"]
                recommendation["message"] = "检测到 Anthropic API key"
        
        # 需要手动配置
        else:
            recommendation["message"] = "未检测到本地工具或 API key，需要手动配置"
        
        return recommendation


def interactive_setup() -> dict[str, Any]:
    """交互式配置
    
    Returns:
        dict: 配置结果
    """
    discovery = ModelDiscovery()
    result = discovery.discover()
    
    print("=" * 60)
    print("🔍 模型发现")
    print("=" * 60)
    
    # 显示发现结果
    if result["has_local"]:
        print(f"\n✅ 检测到 {len(result['tools'])} 个本地工具:")
        for tool in result["tools"]:
            print(f"   - {tool['name']}: {len(tool['models'])} 个模型")
    
    if result["has_api_key"]:
        print(f"\n✅ 检测到 {len(result['api_keys'])} 个 API key:")
        for provider in result["api_keys"]:
            print(f"   - {provider}")
    
    if result["need_config"]:
        print("\n⚠️  未检测到本地工具或 API key")
    
    # 获取推荐配置
    recommendation = discovery.get_recommendation(result)
    
    if recommendation["executor_type"]:
        print(f"\n💡 推荐: {recommendation['message']}")
        
        # 让用户确认或选择
        print("\n可用模型:")
        for i, model in enumerate(recommendation["models"][:10], 1):
            print(f"   {i}. {model}")
        
        if len(recommendation["models"]) > 10:
            print(f"   ... 还有 {len(recommendation['models']) - 10} 个")
        
        choice = input("\n选择模型编号（直接回车使用第一个）: ").strip()
        
        if choice and choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(recommendation["models"]):
                selected_model = recommendation["models"][idx]
            else:
                selected_model = recommendation["models"][0]
        else:
            selected_model = recommendation["models"][0] if recommendation["models"] else None
        
        return {
            "success": True,
            "executor_type": recommendation["executor_type"],
            "executor_config": recommendation["executor_config"],
            "selected_model": selected_model,
            "available_models": recommendation["models"]
        }
    
    else:
        # 手动配置
        print("\n📝 手动配置")
        print("-" * 40)
        
        print("\n支持的模型提供商:")
        print("   1. OpenAI (需要 API key)")
        print("   2. Anthropic (需要 API key)")
        print("   3. MiniMax (需要 API key)")
        print("   4. 通义千问 (需要 API key)")
        print("   5. 本地模型 (需要 URL)")
        
        provider_choice = input("\n选择提供商编号: ").strip()
        
        provider_map = {
            "1": "openai",
            "2": "anthropic",
            "3": "minimax",
            "4": "dashscope",
            "5": "local"
        }
        
        provider = provider_map.get(provider_choice, "openai")
        
        if provider == "local":
            url = input("本地模型 URL (如 http://localhost:11434): ").strip()
            model = input("模型名称: ").strip()
            
            return {
                "success": True,
                "executor_type": "api",
                "executor_config": {
                    "provider": "openai",
                    "base_url": url,
                    "api_key": "not-needed"
                },
                "selected_model": model,
                "available_models": [model]
            }
        else:
            api_key = input(f"{provider} API key: ").strip()
            
            # 默认模型列表
            default_models = {
                "openai": ["gpt-4", "gpt-4o", "gpt-3.5-turbo"],
                "anthropic": ["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"],
                "minimax": ["MiniMax-M2.7", "MiniMax-M2.5"],
                "dashscope": ["qwen-max", "qwen-plus", "qwen-turbo"]
            }
            
            return {
                "success": True,
                "executor_type": "api",
                "executor_config": {
                    "provider": provider,
                    "api_key": api_key
                },
                "selected_model": default_models[provider][0],
                "available_models": default_models[provider]
            }
