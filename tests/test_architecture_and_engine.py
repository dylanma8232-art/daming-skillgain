"""调用链、执行器能力边界与引擎集成测试"""
import json
from unittest.mock import patch
import pytest
from pathlib import Path

from evaluator.executors import (
    create_executor,
    BaseExecutor,
    OpencodeExecutor,
    StandaloneExecutor,
    ApiExecutor,
    AgentExecutor,
    ExecutionResult,
)
from evaluator.models import EvaluationConfig, RunnerConfig, ExperimentResult
from evaluator.reporting import generate_run_report, build_report_payload
from evaluator.assertions import AssertionEngine, AssertionRule, save_assertions_snapshot


class TestExecutorBoundaries:
    def test_executor_factory(self):
        opencode_exec = create_executor({"type": "opencode"})
        assert isinstance(opencode_exec, OpencodeExecutor)

        standalone_exec = create_executor({"type": "standalone", "provider": "openai", "api_key": "dummy"})
        assert isinstance(standalone_exec, StandaloneExecutor)

        api_exec = create_executor({"type": "api", "provider": "openai", "api_key": "dummy"})
        assert isinstance(api_exec, ApiExecutor)

        agent_exec = create_executor({"type": "agent", "provider": "openai", "api_key": "dummy"})
        assert isinstance(agent_exec, AgentExecutor)

    def test_standalone_executor_missing_api_key(self):
        standalone = StandaloneExecutor({"provider": "openai", "api_key": ""})
        # 验证如果没有提供 api_key 且有 httpx 时报缺少 API key，无 httpx 时报需要安装 httpx
        with patch("evaluator.executors.standalone.HAS_HTTPX", True):
            res = standalone.execute("gpt-4", "test prompt")
            assert res.success is False
            assert "缺少 API key" in res.error

    def test_agent_executor_custom_tools_registered(self):
        from evaluator.executors.agent import TOOLS
        agent = AgentExecutor({"provider": "openai", "api_key": "dummy"})
        assert len(TOOLS) == 4
        tool_names = [t["function"]["name"] for t in TOOLS]
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "list_files" in tool_names
        assert "execute_command" in tool_names


class TestEngineReportingWithSnapshots:
    def test_generate_report_html_with_stats_and_snapshots(self, tmp_path):
        run_dir = tmp_path / "run_full"
        run_dir.mkdir(parents=True)

        # 准备 workspace
        ws1 = run_dir / "exp_model1_with"
        ws1.mkdir(parents=True)
        (ws1 / "stdout.txt").write_text("Hello from model1 with skill")
        (ws1 / "stderr.txt").write_text("")
        (ws1 / "transcript.md").write_text("# Transcript")
        (ws1 / "artifacts").mkdir()
        (ws1 / "artifacts" / "summary.md").write_text("# Summary\nAll done.")

        ws2 = run_dir / "exp_model1_without"
        ws2.mkdir(parents=True)
        (ws2 / "stdout.txt").write_text("Hello from model1 without skill")
        (ws2 / "stderr.txt").write_text("")
        (ws2 / "transcript.md").write_text("# Transcript")
        (ws2 / "artifacts").mkdir()
        (ws2 / "artifacts" / "summary.md").write_text("# Summary\nWithout skill.")

        # 断言快照
        engine = AssertionEngine()
        rules = [
            AssertionRule(name="summary_check", type="exact_match", parameters={"expected": "All done."}),
            AssertionRule(name="fact_check", type="contains_facts", parameters={"keywords": ["Summary"]}),
        ]
        snapshot_data = engine.create_snapshot(
            rules=rules,
            records=[
                {"model": "model-1", "use_skill": True, "results": []},
            ],
            metadata={"run_dir": str(run_dir)}
        )
        save_assertions_snapshot(snapshot_data, run_dir / "assertions_snapshot.json")

        # 盲评与 manifest
        bv_dir = run_dir / "blind_vote"
        bv_dir.mkdir(parents=True)
        (bv_dir / "stats.json").write_text(json.dumps({
            "model-1": {"with_votes": 3, "without_votes": 0, "skill_helped": True}
        }))

        manifest = {
            "evaluation_name": "Full Calibration Run",
            "task_prompt": "Produce analysis summary",
            "target_skill": "analysis_skill",
            "target_skill_path": str(tmp_path),
            "results": [
                {
                    "model": "model-1",
                    "use_skill": True,
                    "workspace_dir": str(ws1),
                    "is_valid": True,
                    "status": "completed",
                    "success": True,
                    "artifacts": ["artifacts/summary.md"],
                },
                {
                    "model": "model-1",
                    "use_skill": False,
                    "workspace_dir": str(ws2),
                    "is_valid": True,
                    "status": "completed",
                    "success": True,
                    "artifacts": ["artifacts/summary.md"],
                },
            ],
        }
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest))

        # 生成 HTML 报告
        report_path = generate_run_report(run_dir)
        assert report_path.exists()
        html_text = report_path.read_text(encoding="utf-8")

        # 检查报告中是否包含五项纪律和快照相关模块
        assert "统计口径明细" in html_text
        assert "自动检查规则快照" in html_text
        assert "有效样本量 (N)" in html_text
        assert "⚠️ 样本量不足" in html_text  # N=1 < 5 必须包含小样本警告
        assert "零 LLM 调用" in html_text
