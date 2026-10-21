import pytest
from evaluator.model_discovery import ModelDiscovery

class TestModelDiscovery:
    @pytest.fixture
    def discovery(self):
        return ModelDiscovery()

    def test_get_recommendation_has_local_tool(self, discovery):
        discovery_result = {
            "has_local": True,
            "has_api_key": False,
            "tools": [
                {
                    "name": "ollama",
                    "config": {"host": "localhost"},
                    "models": ["llama3", "mistral"]
                },
                {
                    "name": "opencode",
                    "config": {},
                    "models": ["qwen"]
                }
            ],
            "api_keys": {}
        }
        recommendation = discovery.get_recommendation(discovery_result)

        assert recommendation["executor_type"] == "ollama"
        assert recommendation["executor_config"] == {"host": "localhost"}
        assert recommendation["models"] == ["llama3", "mistral"]
        assert "检测到本地工具: ollama" in recommendation["message"]

    def test_get_recommendation_has_openai_api_key(self, discovery):
        discovery_result = {
            "has_local": False,
            "has_api_key": True,
            "tools": [],
            "api_keys": {
                "openai": "sk-xxx"
            }
        }
        recommendation = discovery.get_recommendation(discovery_result)

        assert recommendation["executor_type"] == "api"
        assert recommendation["executor_config"] == {
            "provider": "openai",
            "api_key": "sk-xxx"
        }
        assert recommendation["models"] == ["gpt-4", "gpt-4o", "gpt-3.5-turbo"]
        assert recommendation["message"] == "检测到 OpenAI API key"

    def test_get_recommendation_has_anthropic_api_key(self, discovery):
        discovery_result = {
            "has_local": False,
            "has_api_key": True,
            "tools": [],
            "api_keys": {
                "anthropic": "sk-ant-xxx"
            }
        }
        recommendation = discovery.get_recommendation(discovery_result)

        assert recommendation["executor_type"] == "api"
        assert recommendation["executor_config"] == {
            "provider": "anthropic",
            "api_key": "sk-ant-xxx"
        }
        assert recommendation["models"] == ["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"]
        assert recommendation["message"] == "检测到 Anthropic API key"

    def test_get_recommendation_has_other_api_key(self, discovery):
        discovery_result = {
            "has_local": False,
            "has_api_key": True,
            "tools": [],
            "api_keys": {
                "minimax": "sk-minimax-xxx"
            }
        }
        recommendation = discovery.get_recommendation(discovery_result)

        # In this specific case, it hits the `elif discovery_result["has_api_key"]:` branch
        # but neither "openai" nor "anthropic" is in `api_keys`, so nothing gets set
        # and it returns the default initialized recommendation dict.
        assert recommendation["executor_type"] is None
        assert recommendation["executor_config"] == {}
        assert recommendation["models"] == []
        assert recommendation["message"] == ""

    def test_get_recommendation_needs_manual_config(self, discovery):
        discovery_result = {
            "has_local": False,
            "has_api_key": False,
            "tools": [],
            "api_keys": {}
        }
        recommendation = discovery.get_recommendation(discovery_result)

        assert recommendation["executor_type"] is None
        assert recommendation["executor_config"] == {}
        assert recommendation["models"] == []
        assert recommendation["message"] == "未检测到本地工具或 API key，需要手动配置"
