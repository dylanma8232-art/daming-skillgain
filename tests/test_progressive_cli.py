"""渐进式批路与 CLI 确认点测试"""
from pathlib import Path
from unittest.mock import patch
import pytest
from evaluator.workflow import SkillEvaluationWorkflow, default_stage_confirm
from evaluator.cli import build_parser


class TestProgressiveWorkflow:
    def test_cli_parser_has_pipeline_and_verify_commands(self):
        parser = build_parser()
        args = parser.parse_args(["pipeline", "--skill-path", "/dummy/path", "--auto"])
        assert args.command == "pipeline"
        assert args.auto is True

        args_v = parser.parse_args(["verify-snapshot", "--snapshot", "/dummy/snapshot.json"])
        assert args_v.command == "verify-snapshot"
        assert args_v.snapshot == "/dummy/snapshot.json"

    def test_default_stage_confirm_auto(self):
        # 传入 auto=True 时不应停盘，直接返回 True
        assert default_stage_confirm(1, "阶段1", "说明", auto=True) is True
        assert default_stage_confirm(4, "阶段4", "说明", auto=True) is True

    def test_default_stage_confirm_interactive_abort(self):
        with patch("builtins.input", return_value="abort"):
            res = default_stage_confirm(1, "计划生成", "计划已就绪", auto=False)
            assert res is False

    def test_default_stage_confirm_interactive_continue(self):
        with patch("builtins.input", return_value=""):
            res = default_stage_confirm(2, "实验执行", "实验完成", auto=False)
            assert res is True

    def test_workflow_stages_interception(self, tmp_path):
        """测试在阶段 1 停盘中断"""
        captured_stages = []

        def mock_confirm(stage_num, stage_name, stage_summary, auto=False):
            captured_stages.append(stage_num)
            if stage_num == 1:
                return False  # 在阶段 1 中止
            return True

        # 创建一个合法的最小 skill 目录
        skill_dir = tmp_path / "my_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: my_skill\ndescription: demo skill\n---\n## What I do\nhello")

        wf = SkillEvaluationWorkflow()
        config = {
            "skill_path": str(skill_dir),
            "skill_name": "my_skill",
            "task_prompt": "demo task",
            "models": ["model-a"],
            "voting_models": ["judge-1"],
            "summary_model": "judge-1",
            "auto": False,
            "confirm_func": mock_confirm,
            "skip_connectivity_test": True,
        }
        res = wf.run(config)
        if res.get("run_dir") and Path(res["run_dir"]).exists():
            import shutil
            shutil.rmtree(res["run_dir"], ignore_errors=True)
        if Path("_generated_test_data").exists():
            import shutil
            shutil.rmtree("_generated_test_data", ignore_errors=True)
        assert res["success"] is False
        assert res["aborted"] is True
        assert res["stage"] == 1
        assert captured_stages == [1]

    def test_cli_verify_snapshot_subcommand(self, tmp_path):
        """测试 CLI verify-snapshot 子命令调用"""
        from evaluator.assertions import AssertionEngine, AssertionRule, save_assertions_snapshot
        from evaluator.cli import main
        import sys

        engine = AssertionEngine()
        rules = [AssertionRule(name="r1", type="exact_match", parameters={"expected": "PASS"})]
        snapshot_file = tmp_path / "assertions_snapshot.json"
        save_assertions_snapshot(engine.create_snapshot(rules, []), snapshot_file)

        with patch.object(sys, "argv", ["evaluator", "verify-snapshot", "--snapshot", str(snapshot_file)]):
            with patch("builtins.print") as mock_print:
                main()
                assert mock_print.called
