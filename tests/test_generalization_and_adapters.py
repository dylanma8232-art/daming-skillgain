"""跨宿主通用化测试：主链解耦、executor 贯通、能力预检、Subject Adapter、宿主边界"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from evaluator.executors import (
    create_executor,
    BaseExecutor,
    ExecutionResult,
    SessionResult,
    UnsupportedCapabilityError,
    ClaudeCodeExecutor,
    CodexExecutor,
    ScriptExecutor,
)
from evaluator.host_session import (
    resolve_host,
    run_host_session,
    run_host_text,
    EXECUTION_MODE_DEFAULT_HOST,
)
from evaluator.models import EvaluationConfig, RunnerConfig
from evaluator.subject_adapter import (
    get_subject_adapter,
    run_subject_contract,
    SubjectAdapterError,
    SUBJECT_HOST_ADAPTERS,
)
from evaluator.engine import (
    EvaluationEngine,
    resolve_config_host,
    run_from_plan,
    test_model_connectivity as _test_model_connectivity,
)


# ---------------------------------------------------------------------------
# 1. 主链解耦审查：主链（engine/workflow/reporting）零 OpenCode 专用传输调用
# ---------------------------------------------------------------------------

class TestMainChainDecoupling:
    """验收标准 1: 主链（含盲评路径）不再直接调用 OpenCode 专用函数（代码审查可验）"""

    # OpenCode 专用传输符号（真实实现只允许出现在 executors/opencode.py）
    # 只匹配「调用点」形态，不匹配 def 定义行与注释（兼容别名不算传输调用）
    OPENCODE_TRANSPORT_SYMBOLS = [
        "OpencodeApiClient(",
        "detect_opencode_api_url()",
        "detect_opencode_api_url(",
        "_detect_opencode_attach_url(",
        "parse_opencode_json_events(raw",
        '["opencode", "run"]',
        "'opencode', 'run'",
    ]

    FORBIDDEN_CALL_PATTERNS = [
        "run_opencode_cli(",
        "run_opencode_api_session(",
    ]

    @staticmethod
    def _is_definition(line: str) -> bool:
        s = line.strip()
        return s.startswith("def ") or s.startswith("class ") or s.startswith("#")

    MAIN_CHAIN_FILES = [
        "evaluator/engine.py",
        "evaluator/workflow.py",
        "evaluator/reporting.py",
        "evaluator/host_session.py",
    ]

    def test_main_chain_has_no_opencode_transport_calls(self):
        repo_root = Path(__file__).resolve().parent.parent
        violations = []
        for rel in self.MAIN_CHAIN_FILES:
            text = (repo_root / rel).read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), 1):
                if self._is_definition(line):
                    continue
                stripped = line.strip()
                for symbol in self.OPENCODE_TRANSPORT_SYMBOLS:
                    if symbol in line:
                        violations.append(f"{rel}:{line_no}: {symbol} -> {stripped[:90]}")
                for pat in self.FORBIDDEN_CALL_PATTERNS:
                    if pat in line:
                        # 兼容别名已全部删除：主链任何 run_opencode_cli / run_opencode_api_session
                        # 调用点（含间接转调）都视为违例
                        violations.append(f"{rel}:{line_no}: {pat} -> {stripped[:90]}")
        assert violations == [], "主链存在 OpenCode 专用传输调用:\n" + "\n".join(violations)

    def test_opencode_transport_lives_only_in_executor(self):
        repo_root = Path(__file__).resolve().parent.parent
        opencode_py = (repo_root / "evaluator/executors/opencode.py").read_text(encoding="utf-8")
        # 宿主专用实现必须存在于 opencode executor 模块
        assert "class OpencodeApiClient" in opencode_py
        assert "def run_opencode_cli" in opencode_py
        assert "def run_opencode_api_session" in opencode_py
        assert '"opencode", "run"' in opencode_py or '"opencode", "run"]' in opencode_py or '[cli, "run"]' in opencode_py

    def test_blind_review_path_goes_through_host_session(self):
        repo_root = Path(__file__).resolve().parent.parent
        engine_text = (repo_root / "evaluator/engine.py").read_text(encoding="utf-8")
        # 盲评评委执行与总结模型必须经统一调度层
        assert "run_host_session(" in engine_text
        assert "def _call_host_model(" in engine_text
        assert "run_host_text(" in engine_text
        # 盲评投票执行函数签名带 host 参数
        assert "def _execute_batch_eval_command(" in engine_text
        sig = engine_text.split("def _execute_batch_eval_command(")[1].split(")")[0]
        assert "host" in sig

    def test_resolve_config_host_mapping(self, tmp_path):
        cfg = EvaluationConfig(
            evaluation_name="t", task_prompt="t", workspace_root="w",
            models=["m"], runners=[RunnerConfig(name="r", command_template="run")],
        )
        assert resolve_config_host(cfg) is None  # 未配置 host -> 按 execution_mode 默认映射
        cfg2 = EvaluationConfig(
            evaluation_name="t", task_prompt="t", workspace_root="w",
            models=["m"], runners=[RunnerConfig(name="r", command_template="run")],
            host="claude-code",
        )
        assert resolve_config_host(cfg2) == "claude-code"

    def test_execution_mode_default_host_mapping(self):
        assert EXECUTION_MODE_DEFAULT_HOST["cli-attach"] == "opencode"
        assert EXECUTION_MODE_DEFAULT_HOST["api"] == "opencode"


# ---------------------------------------------------------------------------
# 2. executor 贯通与第二宿主
# ---------------------------------------------------------------------------

class TestExecutorThrough:
    def test_host_registry_contains_second_host(self):
        from evaluator.executors import HOST_REGISTRY
        for host in ("opencode", "claude-code", "codex", "script"):
            assert host in HOST_REGISTRY

    def test_claude_code_executor_contract(self):
        ex = ClaudeCodeExecutor({})
        assert ex.host_name == "claude-code"
        assert ex.supports("file_ops")
        assert ex.capabilities["skill_injection"] == "claude-skills-dir"
        # 未安装 CLI 时显式报错而非静默成功
        ok, msg = ex.validate_config()
        if not ok:
            assert "claude" in msg.lower()

    def test_codex_executor_contract(self):
        ex = CodexExecutor({})
        assert ex.host_name == "codex"
        assert ex.capabilities["skill_injection"] == "agents-md"
        ok, msg = ex.validate_config()
        if not ok:
            assert "codex" in msg.lower()

    def test_session_result_combined_text(self):
        s = SessionResult(exit_code=0, stdout="out", stderr="err", transcript="trans", text="txt")
        combined = s.combined_text()
        assert "txt" in combined and "trans" in combined and "err" in combined
        assert s.success is True
        s2 = SessionResult(exit_code=1, stdout="", stderr="", transcript="", text="")
        assert s2.success is False


class TestScriptExecutorEndToEnd:
    """验收标准 2: 第二 executor 通过一个真实（mock）任务的端到端评测。

    script 执行器以真实子进程驱动约定契约的宿主脚本——调度链路
    （engine -> host_session -> executor -> 子进程 -> 工作区产物）全部真实，
    仅「宿主本身」是 mock，测试与报告如实标注。
    """

    @staticmethod
    def _write_host_script(path: Path, with_skill_marker: bool = True) -> None:
        script = f'''#!/usr/bin/env python3
import json, sys, pathlib
payload = json.loads(sys.stdin.read())
cwd = pathlib.Path(payload["cwd"])
model = payload["model"]
prompt = payload.get("prompt") or ""
use_skill = "with-skill" in prompt or (cwd / ".skills").exists()
art = cwd / "artifacts"
art.mkdir(parents=True, exist_ok=True)
(art / "summary.md").write_text("# Script Host Summary\\n由 mock 宿主产出，模型: " + model + "\\n", encoding="utf-8")
(art / "result.csv").write_text("id,value\\n1,42\\n2,43\\n", encoding="utf-8")
print(json.dumps({{
    "exit_code": 0,
    "stdout": "script host done\\n",
    "stderr": "",
    "transcript": "# Transcript\\nscript host mock transcript\\n",
    "text": "任务完成，产物已写入 artifacts/summary.md",
    "usage": {{"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "api_call_count": 1}},
}}, ensure_ascii=False))
'''
        path.write_text(script, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _make_skill(self, skill_dir: Path) -> Path:
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: mock-report-skill\ndescription: 生成报告的 mock skill\n---\n"
            "## What I do\n生成 result.csv 与 summary.md",
            encoding="utf-8",
        )
        return skill_dir

    def _make_config(self, tmp_path: Path, script_path: Path) -> EvaluationConfig:
        cfg = EvaluationConfig(
            evaluation_name="script-host-e2e",
            task_prompt="生成一份含 result.csv 的分析报告",
            workspace_root=str(tmp_path / "runs"),
            models=["mock-model-a"],
            runners=[RunnerConfig(name="script", command_template="script run --model {model}")],
            blind_review_models=["mock-judge"],
            target_skill="mock-report-skill",
            target_skill_path=str(self._make_skill(tmp_path / "mock-report-skill")),
            host="script",
            host_config={"script": str(script_path)},
            execution_mode="cli",
            repetitions=1,
            max_parallelism=1,
        )
        return cfg

    def test_script_executor_session_contract(self, tmp_path):
        script = tmp_path / "mock_host.py"
        self._write_host_script(script)
        ex = ScriptExecutor({"script": str(script)})
        assert ex.validate_config() == (True, "")
        session = ex.execute_session(
            model="mock-model-a", prompt="hello", cwd=tmp_path, timeout_seconds=30,
        )
        assert session.success
        assert "任务完成" in session.text
        assert session.usage["total_tokens"] == 15

    def test_second_host_end_to_end_evaluation(self, tmp_path, monkeypatch):
        """script 宿主（mock）跑通 plan -> run_from_plan -> manifest 完整链路"""
        monkeypatch.chdir(tmp_path)
        script = tmp_path / "mock_host.py"
        self._write_host_script(script)
        cfg = self._make_config(tmp_path, script)
        (tmp_path / "eval-config.json").write_text("{}", encoding="utf-8")

        engine = EvaluationEngine(cfg, tmp_path / "eval-config.json")
        run_dir = engine.create_run_directory()
        (run_dir / "config.snapshot.json").write_text(json.dumps(cfg.to_dict()), encoding="utf-8")
        plan_item = {
            "workspace_dir": str(run_dir / "experiments" / "script__mock-model-a__with-skill"),
            "model": "mock-model-a",
            "use_skill": True,
            "repetition": 1,
            "prompt": "with-skill 生成一份含 result.csv 的分析报告",
            "input_files": [],
        }
        (run_dir / "plan.json").write_text(json.dumps({"plans": [plan_item]}), encoding="utf-8")

        result_dir = run_from_plan(run_dir, dry_run=False, skip_blind_review=True)
        manifest = json.loads((result_dir / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["host"] == "script"
        assert len(manifest["results"]) == 1
        result = manifest["results"][0]
        assert result["runner_name"] == "script"
        assert result["success"] is True
        assert result["status"] == "completed"
        assert result["skill_loaded"] is True
        ws = Path(result["workspace_dir"])
        assert (ws / "artifacts" / "summary.md").exists()
        assert (ws / "artifacts" / "result.csv").exists()
        # transcript 由执行链路真实写入
        assert "script host mock transcript" in (ws / "transcript.md").read_text(encoding="utf-8")

    def test_connectivity_via_script_host(self, tmp_path):
        script = tmp_path / "mock_host.py"
        self._write_host_script(script)
        report = _test_model_connectivity(
            ["mock-model-a"], host="script", timeout=30,
            host_config={"script": str(script)},
        )
        assert report["all_ok"] is True
        assert report["passed"] == ["mock-model-a"]

    def test_cli_host_config_flag_parses_and_reaches_executor(self, tmp_path):
        """CLI --host-config 通道——任何 agent 工具的包装脚本可经命令行接入"""
        from evaluator.cli import _parse_host_config
        script = tmp_path / "any_agent_tool_wrapper.py"
        self._write_host_script(script)

        # 1. JSON 解析
        cfg = _parse_host_config('{"script": "' + str(script) + '"}')
        assert cfg == {"script": str(script)}
        assert _parse_host_config(None) is None
        assert _parse_host_config("") is None
        assert _parse_host_config("not-json") is None
        assert _parse_host_config('[1,2]') is None

        # 2. 解析结果能直接驱动 script 执行器（用户包装脚本 → 被测链路）
        from evaluator.executors import create_executor
        ex = create_executor(dict(cfg, type="script"))
        assert ex.validate_config() == (True, "")
        session = ex.execute_session(model="mock-model-a", prompt="hi", cwd=tmp_path, timeout_seconds=30)
        assert session.success

    def test_plan_generate_accepts_host_config(self, tmp_path, monkeypatch):
        """cmd_plan_generate 接受 host_config 并写入配置（script 宿主脚本路径可达）"""
        import evaluator.cli as cli_mod
        import inspect
        sig = inspect.signature(cli_mod.cmd_plan_generate)
        assert "host_config" in sig.parameters


# ---------------------------------------------------------------------------
# 3. Subject Adapter 统一契约（验收标准 3）
# ---------------------------------------------------------------------------

class TestSubjectAdapter:
    HOSTS = ["opencode", "claude-code", "codex", "script"]

    @staticmethod
    def _make_skill(base: Path) -> Path:
        skill = base / "demo-skill"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: demo\n---\nbody", encoding="utf-8"
        )
        return skill

    def test_registry_has_two_demo_hosts(self):
        assert {"opencode", "claude-code", "script"} <= set(SUBJECT_HOST_ADAPTERS.keys())

    def test_unregistered_host_explicit_error(self):
        with pytest.raises(SubjectAdapterError, match="未注册的被测宿主"):
            get_subject_adapter("cursor")

    @pytest.mark.parametrize("host", ["opencode", "claude-code", "script"])
    def test_skill_dir_hosts_inject_collect_verify(self, host, tmp_path):
        adapter = get_subject_adapter(host)
        skill = self._make_skill(tmp_path / "skill-src")
        ws = tmp_path / f"ws-{host}"
        ws.mkdir()
        result = adapter.inject_skill(skill, ws, "demo-skill")
        assert Path(result["skill_dir"]).exists()
        verify = adapter.verify_skill_loaded(ws, "demo-skill")
        assert verify["loaded"] is True, verify["evidence"]
        # 产物收集
        (ws / "artifacts").mkdir()
        (ws / "artifacts" / "out.md").write_text("x", encoding="utf-8")
        assert adapter.collect_artifacts(ws) == ["artifacts/out.md"]
        # without-skill 清理
        cleanup = adapter.cleanup_skill(ws, "demo-skill")
        assert adapter.verify_skill_loaded(ws, "demo-skill")["loaded"] is False
        assert cleanup

    def test_codex_agents_md_injection_and_verify(self, tmp_path):
        adapter = get_subject_adapter("codex")
        skill = self._make_skill(tmp_path / "skill-src")
        ws = tmp_path / "ws-codex"
        ws.mkdir()
        result = adapter.inject_skill(skill, ws, "demo-skill")
        agents_md = Path(result["agents_md"])
        assert agents_md.exists()
        verify = adapter.verify_skill_loaded(ws, "demo-skill")
        assert verify["loaded"] is True
        assert "Injected Skill: demo-skill" in agents_md.read_text(encoding="utf-8")
        # 幂等重注入
        adapter.inject_skill(skill, ws, "demo-skill")
        content = agents_md.read_text(encoding="utf-8")
        assert content.count("Injected Skill: demo-skill") == 1
        # 清理
        adapter.cleanup_skill(ws, "demo-skill")
        assert adapter.verify_skill_loaded(ws, "demo-skill")["loaded"] is False

    def test_load_verification_prevents_fake_control(self, tmp_path):
        """加载验证失败必须显式报错（防假对照纪律）"""
        ws = tmp_path / "ws-fake"
        ws.mkdir()
        adapter = get_subject_adapter("claude-code")
        # 不注入直接验证 -> loaded=False
        assert adapter.verify_skill_loaded(ws, "demo-skill")["loaded"] is False

    def test_run_subject_contract_end_to_end_with_mock_executor(self, tmp_path):
        """四个契约环节在 mock executor 上端到端贯通"""
        class MockExecutor(ScriptExecutor):
            host_name = "script"

        script = tmp_path / "mock_host.py"
        TestScriptExecutorEndToEnd._write_host_script(script)
        executor = MockExecutor({"script": str(script)})
        skill = self._make_skill(tmp_path / "skill-src")
        ws = tmp_path / "ws-contract"

        for use_skill in (True, False):
            # 每次用全新工作区（without-skill 工作区必须无 skill 残留）
            workdir = tmp_path / f"ws-contract-{use_skill}"
            out = run_subject_contract(
                host="script",
                executor=executor,
                skill_path=skill,
                skill_name="demo-skill",
                model="mock-model-a",
                prompt="with-skill demo task" if use_skill else "plain demo task",
                workspace_dir=workdir,
                use_skill=use_skill,
                timeout_seconds=30,
            )
            assert out["host"] == "script"
            assert out["use_skill"] is use_skill
            assert out["session"]["exit_code"] == 0
            assert "artifacts/summary.md" in out["artifacts"]
            if use_skill:
                assert out["loaded_evidence"]["loaded"] is True
            else:
                assert out["loaded_evidence"]["loaded"] is False


# ---------------------------------------------------------------------------
# 4. 能力预检：不支持的能力显式报错（验收标准 4）
# ---------------------------------------------------------------------------

class TestCapabilityPrecheck:
    def test_require_capability_explicit_error(self):
        class LimitedExecutor(BaseExecutor):
            host_name = "limited"
            capabilities = {"file_ops": True}

            def execute(self, model, prompt, **kwargs):
                return ExecutionResult(success=False, output="")

            def check_connectivity(self, model):
                return False

            def list_models(self):
                return []

        ex = LimitedExecutor({})
        ex.require_capability("file_ops")  # 支持的不报错
        with pytest.raises(UnsupportedCapabilityError) as exc:
            ex.require_capability("transcript", purpose="评委过程记录")
        msg = str(exc.value)
        assert "limited" in msg and "transcript" in msg and "评委过程记录" in msg

    def test_execute_session_unsupported_by_default(self):
        class NoSessionExecutor(BaseExecutor):
            host_name = "no-session"
            capabilities = {}

            def execute(self, model, prompt, **kwargs):
                return ExecutionResult(success=False, output="")

            def check_connectivity(self, model):
                return False

            def list_models(self):
                return []

        with pytest.raises(UnsupportedCapabilityError, match="no-session"):
            NoSessionExecutor({}).execute_session(model="m", prompt="p")

    def test_run_from_plan_aborts_when_host_lacks_file_ops(self, tmp_path, monkeypatch):
        """宿主缺 file_ops 能力时 run_from_plan 显式报错，不静默降级"""
        monkeypatch.chdir(tmp_path)
        cfg = EvaluationConfig(
            evaluation_name="cap-test",
            task_prompt="t",
            workspace_root=str(tmp_path / "runs"),
            models=["m"],
            runners=[RunnerConfig(name="limited", command_template="x")],
            blind_review_models=["j"],
            host="limited",
        )
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "config.snapshot.json").write_text(json.dumps(cfg.to_dict()), encoding="utf-8")
        (run_dir / "plan.json").write_text(json.dumps({"plans": []}), encoding="utf-8")

        from evaluator.executors.base import BaseExecutor as _BE

        class _Limited(_BE):
            host_name = "limited"
            capabilities = {}  # 缺 file_ops

            def execute(self, model, prompt, **kwargs):
                return ExecutionResult(success=False, output="")

            def check_connectivity(self, model):
                return False

            def list_models(self):
                return []

        import evaluator.executors as ex_pkg
        with patch.dict(ex_pkg.HOST_REGISTRY, {"limited": _Limited}):
            with pytest.raises(UnsupportedCapabilityError, match="file_ops"):
                run_from_plan(run_dir, dry_run=False, skip_blind_review=True)

    def test_subject_adapter_unsupported_host_error(self):
        adapter = get_subject_adapter("script")
        with pytest.raises(SubjectAdapterError):
            get_subject_adapter("nonexistent-host")


# ---------------------------------------------------------------------------
# 5. 报告宿主适用边界（验收标准 5）
# ---------------------------------------------------------------------------

class TestHostBoundaryReport:
    def test_report_contains_host_boundary(self, tmp_path):
        from evaluator.reporting import generate_run_report

        run_dir = tmp_path / "run_host"
        run_dir.mkdir(parents=True)
        ws1 = run_dir / "exp_with"
        ws1.mkdir()
        (ws1 / "stdout.txt").write_text("done")
        (ws1 / "artifacts").mkdir()
        (ws1 / "artifacts" / "summary.md").write_text("# ok")
        manifest = {
            "evaluation_name": "host-boundary-test",
            "task_prompt": "t",
            "target_skill": "s",
            "host": "script",
            "results": [
                {"model": "m1", "use_skill": True, "workspace_dir": str(ws1),
                 "runner_name": "script", "is_valid": True, "status": "completed",
                 "success": True, "artifacts": ["artifacts/summary.md"]},
            ],
        }
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        report_path = generate_run_report(run_dir)
        html_text = report_path.read_text(encoding="utf-8")
        assert "这次是用什么工具测的" in html_text
        assert "换一个 AI 工具，结果可能会不一样" in html_text
        assert "script" in html_text

    def test_manifest_records_host(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # 准备真实 skill 目录（plan_only 需要）
        skill = tmp_path / "demo-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: demo\n---\nbody", encoding="utf-8"
        )
        cfg = EvaluationConfig(
            evaluation_name="host-manifest",
            task_prompt="t",
            workspace_root=str(tmp_path / "runs"),
            models=["m"],
            runners=[RunnerConfig(name="script", command_template="x")],
            blind_review_models=["j"],
            target_skill="demo-skill",
            target_skill_path=str(skill),
            host="script",
        )
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        engine = EvaluationEngine(cfg, tmp_path / "config.json")
        result = engine.plan_only()
        run_dir = Path(result["run_dir"])
        # config.snapshot.json 必须完整保留 host 字段（run_from_plan / 报告宿主边界的数据源）
        snapshot = json.loads((run_dir / "config.snapshot.json").read_text(encoding="utf-8"))
        assert snapshot["host"] == "script"
        assert snapshot["runners"][0]["name"] == "script"
