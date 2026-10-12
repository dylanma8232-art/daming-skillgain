"""五项纪律规范单元测试"""
import math
import pytest
from pathlib import Path

from evaluator.engine import _classify_execution, EvaluationEngine
from evaluator.models import EvaluationConfig, RunnerConfig, ExperimentPlan
from evaluator.reporting import build_report_payload, build_group_payload


class TestFiveDisciplines:
    def test_discipline_1_empty_product_is_failure(self):
        """纪律 1：空产物必须计为 failure，绝不可标记为 completed 或 success"""
        # exit_code = 0, stdout normal, but no artifacts
        success, status, failure_reason = _classify_execution(
            exit_code=0,
            stdout_text="Process finished normally",
            stderr_text="",
            artifact_files=[],
        )
        assert success is False
        assert status == "failure"
        assert failure_reason == "empty_product"

    def test_discipline_2_invalid_vs_failure_distinction(self):
        """纪律 2：基础设施异常/超时标 invalid，业务/产物缺失标 failure"""
        # 1. 超时标 invalid
        success, status, reason = _classify_execution(
            exit_code=124,
            stdout_text="",
            stderr_text="Command timed out after 600s",
            artifact_files=[],
        )
        assert success is False
        assert status == "invalid"
        assert reason == "command_timeout"

        # 2. 网络错误标 invalid
        success, status, reason = _classify_execution(
            exit_code=1,
            stdout_text="",
            stderr_text="Fatal: unable to connect to upstream server (econnrefused)",
            artifact_files=[],
        )
        assert success is False
        assert status == "invalid"
        assert reason == "provider_or_network_error"

        # 3. 正常执行但产物为空标 failure (计入分母)
        success, status, reason = _classify_execution(
            exit_code=0,
            stdout_text="Done",
            stderr_text="",
            artifact_files=[],
        )
        assert success is False
        assert status == "failure"
        assert reason == "empty_product"

    def test_discipline_3_invalid_retry_mechanism(self):
        """纪律 3：invalid 触发重试机制，记录 retry_attempts"""
        from evaluator.models import ExperimentResult

        # 模拟执行经历 2 次 retry 后成功
        retry_history = [
            {"attempt": 1, "exit_code": 124, "failure_reason": "command_timeout"},
            {"attempt": 2, "exit_code": 1, "failure_reason": "provider_or_network_error"},
        ]
        res = ExperimentResult(
            runner_name="opencode",
            model="test-model",
            use_skill=True,
            exit_code=0,
            success=True,
            status="completed",
            failure_reason=None,
            workspace_dir="/tmp/test",
            stdout_file="/tmp/stdout.txt",
            stderr_file="/tmp/stderr.txt",
            transcript_file="/tmp/transcript.md",
            artifacts=["artifacts/summary.md"],
            analysis={},
            is_valid=True,
            retry_attempts=retry_history,
        )
        assert res.is_valid is True
        assert res.success is True
        assert len(res.retry_attempts) == 2
        assert res.retry_attempts[0]["failure_reason"] == "command_timeout"

    def test_discipline_4_without_skill_isolation_and_prompt_symmetry(self, tmp_path):
        """纪律 4：without-skill 严格隔离，无 skill 泄漏，提示词严格对称"""
        cfg = EvaluationConfig(
            evaluation_name="test_iso",
            task_prompt="请分析财务报表并产出 summary.md",
            workspace_root="workspaces",
            models=["test-model"],
            runners=[RunnerConfig(name="opencode", command_template="opencode run")],
            blind_review_models=["judge-model"],
            target_skill="financial-analysis-skill",
            target_skill_path="dummy-path",
        )
        engine = EvaluationEngine(cfg, tmp_path / "config.json")

        prompt_with = engine.build_prompt("test-model", "opencode", use_skill=True)
        prompt_without = engine.build_prompt("test-model", "opencode", use_skill=False)

        # with-skill 包含目标 skill
        assert "financial-analysis-skill" in prompt_with
        assert "with-skill" in prompt_with

        # without-skill 严格禁止泄露 target_skill 名称
        assert "financial-analysis-skill" not in prompt_without
        assert "你不得加载待评测 skill" not in prompt_without  # 不得有泄露否定句
        assert "standard-baseline" in prompt_without

    def test_discipline_5_statistics_and_small_n_warning(self, tmp_path):
        """纪律 5：样本量 N、配对差值 Δ、方差/标准差/分布，N < 5 给出显式警告"""
        run_dir = tmp_path / "run_test"
        run_dir.mkdir(parents=True)

        # 构造 3 个模型的配对结果 (N=3 < 5)
        results = []
        for i, model in enumerate(["model_a", "model_b", "model_c"]):
            # with-skill
            ws_with = run_dir / f"exp_{model}_with"
            ws_with.mkdir(parents=True)
            (ws_with / "stdout.txt").write_text("done")
            (ws_with / "artifacts").mkdir()
            (ws_with / "artifacts" / "report.md").write_text("# Report")
            results.append({
                "model": model,
                "use_skill": True,
                "workspace_dir": str(ws_with),
                "is_valid": True,
                "status": "completed",
                "success": True,
            })
            # without-skill
            ws_without = run_dir / f"exp_{model}_without"
            ws_without.mkdir(parents=True)
            (ws_without / "stdout.txt").write_text("done")
            (ws_without / "artifacts").mkdir()
            (ws_without / "artifacts" / "report.md").write_text("# Report")
            results.append({
                "model": model,
                "use_skill": False,
                "workspace_dir": str(ws_without),
                "is_valid": True,
                "status": "completed",
                "success": True,
            })

        # blind_vote 统计
        bv_dir = run_dir / "blind_vote"
        bv_dir.mkdir(parents=True)
        stats_json = {
            "model_a": {"with_votes": 3, "without_votes": 1, "skill_helped": True},
            "model_b": {"with_votes": 2, "without_votes": 2, "skill_helped": False},
            "model_c": {"with_votes": 4, "without_votes": 0, "skill_helped": True},
        }
        import json
        (bv_dir / "stats.json").write_text(json.dumps(stats_json))

        manifest = {
            "evaluation_name": "stats_test",
            "task_prompt": "test task",
            "target_skill": "my_skill",
            "results": results,
        }
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest))

        payload = build_report_payload(run_dir, manifest)
        stats = payload["statistics"]

        assert stats["sample_size_valid"] == 3
        # paired deltas: model_a: 3-1=2, model_b: 2-2=0, model_c: 4-0=4
        # mean_delta = (2 + 0 + 4) / 3 = 2.0
        assert math.isclose(stats["mean_delta"], 2.0, rel_tol=1e-3)
        # variance: ((2-2)^2 + (0-2)^2 + (4-2)^2) / (3-1) = (0 + 4 + 4) / 2 = 4.0
        assert math.isclose(stats["variance"], 4.0, rel_tol=1e-3)
        # std_dev = sqrt(4.0) = 2.0
        assert math.isclose(stats["std_dev"], 2.0, rel_tol=1e-3)

        # 样本量 < 5 触发告警
        assert "⚠️ 样本量不足" in stats["small_sample_warning"]
        assert "N=3" in stats["small_sample_warning"]
