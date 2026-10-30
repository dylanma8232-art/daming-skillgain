from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from evaluator.executors import HOST_REGISTRY, create_executor
from evaluator.executors.base import (
    ERROR_FATAL,
    ERROR_RETRYABLE,
    STDERR_TRUNCATE,
    BaseExecutor,
    augment_prompt_with_files,
    truncate_stderr,
)
from evaluator.subject_adapter import (
    SubjectAdapterError,
    get_subject_adapter,
)


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


# ---- 注册表 / capabilities -------------------------------------------------

def test_cursor_registered_with_capabilities():
    assert HOST_REGISTRY["cursor-agent"] is HOST_REGISTRY["cursor"]
    executor = create_executor({"type": "cursor-agent"})
    assert executor.host_name == "cursor-agent"
    assert executor.capabilities["file_ops"] is True
    assert executor.capabilities["skill_injection"] == "agents-md"


def test_unknown_host_raises():
    with pytest.raises(ValueError):
        create_executor({"type": "nope"})


# ---- ① build_invocation ----------------------------------------------------

def test_codex_invocation_prompt_via_stdin_and_cd(tmp_path: Path):
    executor = create_executor({"type": "codex"})
    invocation = executor.build_invocation(
        model="gpt-5", prompt="hi", cwd=tmp_path, format_json=True,
        dangerous_skip_permissions=True, files=["a.txt"],
    )
    assert invocation.prompt_channel == "stdin"
    assert invocation.stdin is not None and "hi" in invocation.stdin
    assert "a.txt" in invocation.stdin
    assert invocation.argv[0] == "codex"
    assert "exec" in invocation.argv
    assert "--json" in invocation.argv
    assert "--cd" in invocation.argv and str(tmp_path) in invocation.argv
    assert "--dangerously-bypass-approvals-and-sandbox" in invocation.argv
    assert invocation.argv[-1] == "-"


def test_claude_invocation_uses_stdin_not_argv(tmp_path: Path):
    executor = create_executor({"type": "claude-code"})
    invocation = executor.build_invocation(model="sonnet", prompt="长" * 10000, cwd=tmp_path)
    assert invocation.prompt_channel == "stdin"
    assert "长" * 10000 in invocation.stdin
    assert not any("长" in str(arg) for arg in invocation.argv)
    assert "--output-format" in invocation.argv and "stream-json" in invocation.argv


def test_cursor_invocation_workspace_and_force(tmp_path: Path):
    executor = create_executor({"type": "cursor-agent"})
    invocation = executor.build_invocation(
        model="gpt-5", prompt="hi", cwd=tmp_path, dangerous_skip_permissions=True
    )
    assert invocation.argv[0] == "cursor-agent"
    assert "--print" in invocation.argv
    assert "--workspace" in invocation.argv and str(tmp_path) in invocation.argv
    assert "--force" in invocation.argv
    assert invocation.prompt_channel == "argv"


# ---- ② parse_output --------------------------------------------------------

def test_codex_parses_json_events():
    executor = create_executor({"type": "codex"})
    stdout = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t-1"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "好"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}),
    ])
    result = executor.parse_output(stdout=stdout, stderr="", exit_code=0)
    assert result.text == "好"
    assert result.usage["total_tokens"] == 12
    assert executor.last_session_id == "t-1"


def test_codex_non_json_falls_back_to_stdout():
    executor = create_executor({"type": "codex"})
    result = executor.parse_output(stdout="plain answer", stderr="", exit_code=0)
    assert result.text == "plain answer"
    assert result.usage == {"collected": False}


def test_claude_parses_stream_json():
    executor = create_executor({"type": "claude-code"})
    stdout = "\n".join([
        json.dumps({"type": "assistant", "session_id": "s-1",
                    "message": {"content": [{"type": "text", "text": "第一段"}]}}),
        json.dumps({"type": "result", "session_id": "s-1",
                    "usage": {"input_tokens": 5, "output_tokens": 3}}),
    ])
    result = executor.parse_output(stdout=stdout, stderr="", exit_code=0)
    assert result.text == "第一段"
    assert result.usage["total_tokens"] == 8
    assert executor.last_session_id == "s-1"


def test_cursor_parses_tolerant_stream_json():
    executor = create_executor({"type": "cursor-agent"})
    stdout = "\n".join([
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "a"}]}}),
        json.dumps({"type": "result", "result": "b", "chat_id": "c-1",
                    "usage": {"input_tokens": 1, "output_tokens": 2}}),
        "plain line",
    ])
    result = executor.parse_output(stdout=stdout, stderr="", exit_code=0)
    assert "a" in result.text and "b" in result.text
    assert result.usage["total_tokens"] == 3
    assert executor.last_session_id == "c-1"


# ---- ③ probe / validate_config --------------------------------------------

def test_probe_reports_missing_cli(monkeypatch):
    executor = create_executor({"type": "codex", "cli_path": "/definitely/not/here"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    probe = executor.probe()
    assert probe.ok is False
    assert "找不到" in probe.message
    ok, message = executor.validate_config()
    assert ok is False
    assert "codex" in message


def test_probe_ok(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(0, "1.2.3\n", ""))
    executor = create_executor({"type": "cursor-agent"})
    probe = executor.probe()
    assert probe.ok is True
    assert probe.version == "1.2.3"


def test_check_connectivity_does_not_burn_real_call_by_default(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(0, "v1\n", ""))
    executor = create_executor({"type": "codex"})
    called = {"execute": False}
    monkeypatch.setattr(executor, "execute", lambda *a, **k: called.__setitem__("execute", True))
    assert executor.check_connectivity("m") is True
    assert called["execute"] is False


# ---- run_cli_session 流水线 -------------------------------------------------

def test_run_cli_session_pipes_stdin(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return _completed(0, json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    executor = create_executor({"type": "codex"})
    result = executor.execute_session(model="m", prompt="hello", cwd=tmp_path)
    assert captured["input"].startswith("hello")
    assert result.text == "ok"


# ---- 错误语义 --------------------------------------------------------------

def test_truncate_stderr_limit():
    text = "x" * (STDERR_TRUNCATE + 100)
    assert len(truncate_stderr(text)) == STDERR_TRUNCATE + 1


def test_classify_error():
    assert BaseExecutor.classify_error(1, "connection reset by peer") == ERROR_RETRYABLE
    assert BaseExecutor.classify_error(1, "invalid config") == ERROR_FATAL
    assert BaseExecutor.classify_error(1, "", timed_out=True) == ERROR_RETRYABLE


def test_augment_prompt_with_files():
    assert augment_prompt_with_files("p", None) == "p"
    assert "a.txt" in augment_prompt_with_files("p", ["a.txt"])


# ---- skill 注入边界 --------------------------------------------------------

def test_cursor_subject_adapter_injects_agents_md(tmp_path: Path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n# demo\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    adapter = get_subject_adapter("cursor-agent")
    adapter.inject_skill(skill_dir, workspace, "demo")
    evidence = adapter.verify_skill_loaded(workspace, "demo")
    assert evidence["loaded"] is True
    adapter.cleanup_skill(workspace, "demo")
    assert adapter.verify_skill_loaded(workspace, "demo")["loaded"] is False


def test_none_adapter_rejects_injection(tmp_path: Path):
    adapter = get_subject_adapter("api")
    with pytest.raises(SubjectAdapterError):
        adapter.inject_skill(tmp_path, tmp_path, "demo")
