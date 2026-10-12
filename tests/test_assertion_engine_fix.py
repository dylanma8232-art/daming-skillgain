"""结构化数据与执行留痕断言引擎回归测试"""
import json
import pytest
from evaluator.assertions import (
    AssertionEngine,
    AssertionRule,
    AssertionType,
    ASSERTION_ENGINE_VERSION,
)
from evaluator.assertions.script_eval import evaluate_script_eval


class TestStructuredDataIntegrityFix:
    """结构化数据完整性校验功能验证"""

    def test_json_artifact_parsed_as_json_not_csv(self):
        """用例 1: output_text 为 JSON 事件流 + artifacts 含合法 trip.json -> score=1.0"""
        engine = AssertionEngine()
        rule = AssertionRule(
            name="structured_data_integrity",
            type="script_eval",
            target="output",
            parameters={"script_name": "csv_well_formed"},
        )

        event_stream = (
            '{"type":"step_start","timestamp":1791773079753,"sessionID":"ses_123","part":{"id":"p1"}}\n'
            '{"type":"message","timestamp":1791773080000,"part":{"text":"开始生成行程"}}\n'
            '{"type":"step_finish","timestamp":1791773081000}'
        )
        artifacts = {
            "artifacts/trip.json": json.dumps({"city": "Hangzhou", "days": 2, "spots": ["West Lake", "Lingyin"]}),
            "artifacts/summary.md": "# 行程总结\n杭州两日游方案已生成。",
        }

        res = engine.evaluate_rule(rule, target_content=event_stream, artifacts=artifacts)
        assert res.passed is True
        assert res.score == 1.0
        assert "validated_files" in res.evidence
        assert res.evidence["validated_files"][0]["file"] == "artifacts/trip.json"
        assert res.evidence["validated_files"][0]["passed"] is True

    def test_no_data_files_is_not_applicable_no_penalty(self):
        """用例 2: artifacts 中无任何数据文件 -> 判定为 not_applicable，满分 1.0，不扣分"""
        engine = AssertionEngine()
        rule = AssertionRule(
            name="structured_data_integrity",
            type="script_eval",
            target="output",
            parameters={"script_name": "csv_well_formed"},
        )

        event_stream = (
            '{"type":"step_start","timestamp":1791773079753}\n'
            '{"type":"message","content":"纯文档输出任务"}'
        )
        artifacts = {
            "artifacts/summary.md": "# 总结\n无结构化数据需求。",
            "artifacts/notes.txt": "普通文本笔记。",
        }

        res = engine.evaluate_rule(rule, target_content=event_stream, artifacts=artifacts)
        assert res.passed is True
        assert res.score == 1.0
        assert res.evidence.get("status") == "not_applicable"
        assert "不适用" in res.message

    def test_broken_csv_in_artifacts_fails_gracefully(self):
        """用例 3: artifacts 中存在损坏的 CSV -> 判定失败并报告具体不一致行"""
        engine = AssertionEngine()
        rule = AssertionRule(
            name="structured_data_integrity",
            type="script_eval",
            target="output",
            parameters={"script_name": "csv_well_formed"},
        )
        artifacts = {
            "artifacts/data.csv": "col1,col2\n10,20\n30,40,50\n60",  # 列数不齐
        }
        res = engine.evaluate_rule(rule, target_content="", artifacts=artifacts)
        assert res.passed is False
        assert res.score == 0.0 or res.score == 0.5


class TestExecutionTraceFix:
    """执行留痕断言排除事件流干扰验证"""

    def test_event_stream_trace_keywords_not_credited(self):
        """用例 4: 事件流中含 'trace' / 'trace_id' 字段，但产物无留痕 -> 不得得分 (score=0.0)"""
        engine = AssertionEngine()
        rule = AssertionRule(
            name="execution_trace",
            type="contains_facts",
            target="output",
            parameters={"keywords": ["trace", "checkpoint", "步骤", "假设", "assumption"], "min_keywords": 1},
        )

        # 包含日志和协议字段 trace_id / trace，但不属于 agent 的产物
        event_stream = (
            '{"type":"step_start","timestamp":123,"trace_id":"trace_999","trace":"internal_log"}\n'
            '{"type":"step_finish","trace_state":"done"}\n'
            '{"type":"runner_event","message":"system checkpoint passed"}'
        )
        artifacts = {
            "artifacts/itinerary.md": "# 杭州旅行路线\n纯路线说明，第一天游览西湖与断桥，品尝西湖醋鱼；第二天参观灵隐寺与龙井茶园。",
        }

        suite = engine.evaluate_suite([rule], output_text=event_stream, artifacts=artifacts)
        trace_res = suite.results[0]

        assert trace_res.passed is False
        assert trace_res.score == 0.0
        assert len(trace_res.evidence.get("found_keywords", [])) == 0

    def test_trace_in_artifacts_is_properly_credited(self):
        """用例 5: 产物 execution_trace.md 中包含合法执行步骤与假设 -> 正常得分"""
        engine = AssertionEngine()
        rule = AssertionRule(
            name="execution_trace",
            type="contains_facts",
            target="output",
            parameters={"keywords": ["trace", "checkpoint", "步骤", "假设", "assumption"], "min_keywords": 1},
        )

        event_stream = '{"type":"step_start"}\n{"type":"step_finish"}'
        artifacts = {
            "artifacts/execution_trace.md": (
                "# Execution Trace\n"
                "## 步骤记录\n"
                "- 步骤 1: 解析用户偏好\n"
                "- 假设: 假设交通为公共交通\n"
            ),
        }

        suite = engine.evaluate_suite([rule], output_text=event_stream, artifacts=artifacts)
        trace_res = suite.results[0]

        assert trace_res.passed is True
        assert trace_res.score > 0.0
        assert "步骤" in trace_res.evidence["found_keywords"]
        assert "假设" in trace_res.evidence["found_keywords"]


class TestAssertionVersionBump:
    """判定器版本 bump 验证"""

    def test_version_is_bumped_to_1_1_0(self):
        assert ASSERTION_ENGINE_VERSION == "1.1.0"
        engine = AssertionEngine()
        assert engine.version == "1.1.0"


class TestReplayRealData:
    """真实数据/动态数据断言重放测试"""

    def test_replay_evaluation_data(self, tmp_path):
        from scripts.replay_assertions import replay_evaluation
        from pathlib import Path
        import os

        run_dir_env = os.environ.get("RUN_DIR")
        if run_dir_env and Path(run_dir_env).exists():
            real_run = Path(run_dir_env)
            summary = replay_evaluation(real_run)
            assert summary["engine_version"] == "1.1.0"
            records = summary["replayed_records"]
            assert len(records) > 0
            if len(records) == 18:
                repaired_sdi = [r for r in records if r["new_sdi"] == 1.0]
                assert len(repaired_sdi) >= 12, f"预期至少12条记录SDI恢复为1.0, 实际: {len(repaired_sdi)}"
                if "alibaba-cn/qwen-flash" in summary["delta_summary"]:
                    qwen_gain = summary["delta_summary"]["alibaba-cn/qwen-flash"]["mean_delta"]
                    assert qwen_gain > 0.0, f"qwen-flash 预期平均增益大于0, 实际: {qwen_gain}"
        else:
            # 动态生成最小有效评测运行目录进行重放验证（免去外部硬编码数据依赖）
            mock_run = tmp_path / "mock_replay_run"
            mock_run.mkdir(parents=True)
            mock_exp_with = mock_run / "experiments" / "alibaba-cn_qwen-flash_with-skill_rep1"
            (mock_exp_with / "artifacts").mkdir(parents=True)
            (mock_exp_with / "artifacts" / "trip.json").write_text(json.dumps({"city": "Hangzhou"}), encoding="utf-8")
            mock_exp_without = mock_run / "experiments" / "alibaba-cn_qwen-flash_without-skill_rep1"
            (mock_exp_without / "artifacts").mkdir(parents=True)

            snap_data = {
                "engine_version": "1.0.0",
                "rules_snapshot": [
                    {
                        "name": "structured_data_integrity",
                        "type": "script_eval",
                        "target": "output",
                        "weight": 1.0,
                        "parameters": {"script_name": "csv_well_formed"},
                    }
                ],
                "records": [
                    {
                        "model": "alibaba-cn/qwen-flash",
                        "use_skill": True,
                        "repetition": 1,
                        "output_text": "```json\n{\"city\": \"Hangzhou\"}\n```",
                        "results": [{"name": "structured_data_integrity", "score": 0.5}],
                    },
                    {
                        "model": "alibaba-cn/qwen-flash",
                        "use_skill": False,
                        "repetition": 1,
                        "output_text": "Plain text without json",
                        "results": [{"name": "structured_data_integrity", "score": 0.0}],
                    }
                ]
            }
            (mock_run / "assertions_snapshot.json").write_text(json.dumps(snap_data), encoding="utf-8")
            summary = replay_evaluation(mock_run)
            assert summary["engine_version"] == "1.1.0"
            assert len(summary["replayed_records"]) == 2
            # 验证两单均获得 1.0（带 skill 解析成功 1.0，无数据文件 not_applicable 1.0）
            assert all(r["new_sdi"] == 1.0 for r in summary["replayed_records"])
            assert summary["replayed_records"][0]["old_sdi"] == 0.5
            assert summary["replayed_records"][0]["new_sdi"] == 1.0
            delta = summary["delta_summary"]["alibaba-cn/qwen-flash"]["mean_delta"]
            assert delta == 0.0
