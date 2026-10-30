from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import evaluator.cli as cli
import evaluator.workflow as workflow
from evaluator.engine import classify_failure, generate_existing_run_report


def _simple_args(**overrides):
    base = dict(
        skill_path="",
        task_prompt="",
        models="",
        voting_models="",
        dry_run=False,
        auto=False,
        non_interactive=True,
        profile=None,
        host=None,
        host_config=None,
        repetitions=None,
        parallelism=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_simple_non_interactive_never_calls_input(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("非交互模式不应调用 input()")

    monkeypatch.setattr("builtins.input", boom)
    result = cli.cmd_simple(_simple_args())
    assert result["error"] == "needs_user_input"
    assert result["non_interactive"] is True
    assert "skill_path" in result["missing_fields"]
    assert result["questions"]


def test_simple_profile_fills_answers(monkeypatch, tmp_path: Path):
    profile = {
        "skill_path": "./skill",
        "task_prompt": "做个报告",
        "models": ["m1", "m2"],
        "voting_models": ["j1"],
        "summary_model": "j1",
        "dry_run": True,
    }
    profile_path = tmp_path / "eval-config.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    captured = {}

    class FakeWorkflow:
        def run(self, config):
            captured.update(config)
            return {"success": True, "report_path": "/tmp/report.html"}

    monkeypatch.setattr(workflow, "SkillEvaluationWorkflow", FakeWorkflow)
    result = cli.cmd_simple(_simple_args(profile=str(profile_path)))
    assert result["success"] is True
    assert captured["skill_path"] == "./skill"
    assert captured["models"] == ["m1", "m2"]
    assert captured["non_interactive"] is True
    assert captured["auto"] is True
    assert captured["dry_run"] is True


def test_simple_bad_profile_returns_error():
    result = cli.cmd_simple(_simple_args(profile="/definitely/missing.json"))
    assert result["error"] == "profile_invalid"


def test_workflow_confirm_auto_passes_without_tty(monkeypatch):
    class FakeStdin:
        def isatty(self):
            return False

    monkeypatch.setattr(workflow.sys, "stdin", FakeStdin())
    assert workflow.default_stage_confirm(1, "阶段", "说明", auto=False) is True


def test_classify_failure():
    assert classify_failure("command_timeout") == "retryable"
    assert classify_failure("provider_or_network_error") == "retryable"
    assert classify_failure("non_zero_exit_code_124") == "retryable"
    assert classify_failure("fatal_stderr_error") == "fatal"
    assert classify_failure(None) == "fatal"


def test_report_resume_skips_existing(tmp_path: Path):
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "report.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "anonymous_report.html").write_text("<html></html>", encoding="utf-8")
    result = generate_existing_run_report(tmp_path)
    assert result["skipped"] is True
    assert result["report_path"].endswith("report.html")


def test_main_exit_code_mapping():
    assert cli._main_exit_code({"success": True}) == 0
    assert cli._main_exit_code({"aborted": True, "success": False}) == 0
    assert cli._main_exit_code({"error": "needs_user_input"}) == 2
    assert cli._main_exit_code({"error": "select_voting_models"}) == 2
    assert cli._main_exit_code({"error": "boom"}) == 1
    assert cli._main_exit_code([1, 2, 3]) == 0
