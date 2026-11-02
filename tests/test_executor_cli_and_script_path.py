import stat
from pathlib import Path
from unittest.mock import MagicMock

from evaluator.executors.opencode import OpencodeExecutor
from evaluator.executors.script import ScriptExecutor


def test_opencode_execute_session_passes_cli_path(monkeypatch):
    custom_cli = "/opt/custom/bin/opencode"
    executor = OpencodeExecutor({"cli_path": custom_cli})

    mock_run_cli = MagicMock(return_value=(0, '{"type":"text","part":{"type":"text","text":"hello"}}', ""))
    monkeypatch.setattr("evaluator.executors.opencode.run_opencode_cli", mock_run_cli)

    res = executor.execute_session(
        model="test-model",
        prompt="say hello",
        cwd="/tmp",
    )

    assert mock_run_cli.called
    _, kwargs = mock_run_cli.call_args
    assert kwargs.get("cli_path") == custom_cli
    assert res.exit_code == 0
    assert res.text == "hello"


def test_script_executor_relative_path_resolution(tmp_path, monkeypatch):
    # 创建一个测试包装脚本
    script_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    mock_script = script_dir / "wrapper.sh"
    mock_script.write_text(
        '#!/bin/sh\n'
        'echo \'{"exit_code": 0, "stdout": "ok", "stderr": "", "text": "result from wrapper"}\'\n'
    )
    # 赋可执行权限
    mock_script.chmod(mock_script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # 切换当前工作目录到 script_dir 的父级
    monkeypatch.chdir(tmp_path)
    rel_path = "./bin/wrapper.sh"

    executor = ScriptExecutor({"script": rel_path})

    # 断言初始化时已经解析为绝对路径
    assert Path(executor.script).is_absolute()
    assert Path(executor.script) == mock_script.resolve()

    # 切换到另一个独立的目录（模拟实验 workspace）
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    monkeypatch.chdir(workspace_dir)

    # 验证配置验证成功
    ok, msg = executor.validate_config()
    assert ok is True
    assert msg == ""

    # 验证在工作空间下执行成功，不会因为 cwd 切换导致找不到脚本
    session_res = executor.execute_session(
        model="mock-model",
        prompt="test prompt",
        cwd=workspace_dir,
    )
    assert session_res.exit_code == 0
    assert session_res.text == "result from wrapper"

    # 测试常规 execute 方法
    exec_res = executor.execute(
        model="mock-model",
        prompt="test prompt",
        cwd=workspace_dir,
    )
    assert exec_res.success is True
    assert exec_res.output == "result from wrapper"
