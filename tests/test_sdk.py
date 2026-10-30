from unittest.mock import MagicMock, patch
import pytest
from evaluator.sdk import SkillEvaluator


def test_skill_evaluator_without_profile_retains_executor_config():
    with patch("evaluator.sdk.create_executor", return_value=MagicMock()), \
         patch("evaluator.model_discovery.discover_models_for_host", return_value=["model-1", "model-2"]):

        evaluator = SkillEvaluator(
            skill_path="dummy_skill_path",
            executor_type="codex",
            executor_config={"type": "codex", "custom_setting": "test_val"},
        )

        assert evaluator.host == "codex"
        assert evaluator.host_config == {"type": "codex", "custom_setting": "test_val"}

        with patch.object(evaluator.workflow, "run", return_value={"success": True}) as mock_workflow_run:
            evaluator.run(task_prompt="Evaluate this skill")

            mock_workflow_run.assert_called_once()
            called_config = mock_workflow_run.call_args[0][0]

            assert called_config["host"] == "codex"
            assert called_config["host_config"] == {"type": "codex", "custom_setting": "test_val"}


def test_skill_evaluator_run_kwargs_override_host():
    with patch("evaluator.sdk.create_executor", return_value=MagicMock()), \
         patch("evaluator.model_discovery.discover_models_for_host", return_value=["model-1"]):

        evaluator = SkillEvaluator(
            skill_path="dummy_skill_path",
            executor_type="codex",
        )

        with patch.object(evaluator.workflow, "run", return_value={"success": True}) as mock_workflow_run:
            evaluator.run(
                task_prompt="Evaluate this skill",
                host="claude-code",
                host_config={"type": "claude-code"},
            )

            mock_workflow_run.assert_called_once()
            called_config = mock_workflow_run.call_args[0][0]

            assert called_config["host"] == "claude-code"
            assert called_config["host_config"] == {"type": "claude-code"}
