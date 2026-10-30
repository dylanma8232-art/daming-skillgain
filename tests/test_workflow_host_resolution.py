from __future__ import annotations

from typing import Any
import pytest

from evaluator.workflow import SkillEvaluationWorkflow
from evaluator.executors import BaseExecutor, CodexExecutor, OpencodeExecutor, ClaudeCodeExecutor


def test_workflow_resolves_configured_host(monkeypatch, tmp_path):
    """验证 SkillEvaluationWorkflow().run(config) 当 host='codex' 时解析并校验 CodexExecutor 而非 OpencodeExecutor"""
    (tmp_path / "SKILL.md").write_text("name: test-skill\n", encoding="utf-8")

    # opencode 缺失, codex 正常
    monkeypatch.setattr(OpencodeExecutor, "validate_config", lambda self: (False, "opencode 未安装"))
    monkeypatch.setattr(CodexExecutor, "validate_config", lambda self: (True, ""))

    wf = SkillEvaluationWorkflow()
    assert isinstance(wf.executor, OpencodeExecutor)

    config = {
        "skill_path": str(tmp_path),
        "task_prompt": "do something",
        "models": ["gpt-5"],
        "voting_models": ["gpt-5"],
        "summary_model": "gpt-5",
        "host": "codex",
        "dry_run": True,  # dry_run 不跑真实模型但进行 config 校验
        "skip_connectivity_test": True,
    }

    # 因为 host 是 codex 且 CodexExecutor.validate_config 为 True，_resolve_executor + _validate_config 应成功
    wf._resolve_executor(config)
    wf._validate_config(config)
    assert isinstance(wf.executor, CodexExecutor)


def test_workflow_fails_validation_for_configured_host(monkeypatch, tmp_path):
    """当配置的 host='claude-code' 缺失时，校验应提示 claude-code 无效而非 opencode"""
    (tmp_path / "SKILL.md").write_text("name: test-skill\n", encoding="utf-8")

    monkeypatch.setattr(OpencodeExecutor, "validate_config", lambda self: (True, ""))
    monkeypatch.setattr(ClaudeCodeExecutor, "validate_config", lambda self: (False, "claude-code 命令未找到"))

    wf = SkillEvaluationWorkflow()

    config = {
        "skill_path": str(tmp_path),
        "task_prompt": "do something",
        "models": ["m1"],
        "voting_models": ["m1"],
        "summary_model": "m1",
        "host": "claude-code",
    }

    # 执行 _resolve_executor 后 executor 被替换为 ClaudeCodeExecutor
    wf._resolve_executor(config)
    assert isinstance(wf.executor, ClaudeCodeExecutor)

    with pytest.raises(ValueError) as exc_info:
        wf._validate_config(config)

    assert "claude-code 命令未找到" in str(exc_info.value)


def test_workflow_preserves_explicit_executor(monkeypatch, tmp_path):
    """显式传入 executor 实例时，run(config) 不应重置 executor"""
    (tmp_path / "SKILL.md").write_text("name: test-skill\n", encoding="utf-8")

    custom_executor = OpencodeExecutor()
    wf = SkillEvaluationWorkflow(executor=custom_executor)

    config = {
        "skill_path": str(tmp_path),
        "task_prompt": "do something",
        "models": ["m1"],
        "voting_models": ["m1"],
        "summary_model": "m1",
        "host": "codex",
    }

    wf._resolve_executor(config)
    assert wf.executor is custom_executor
