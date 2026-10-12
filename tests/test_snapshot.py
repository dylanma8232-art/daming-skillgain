"""断言快照持久化与复验测试 (零 LLM)"""
import pytest
from evaluator.assertions import (
    AssertionEngine,
    AssertionRule,
    save_assertions_snapshot,
    load_assertions_snapshot,
    verify_assertions_snapshot_file,
    ASSERTION_ENGINE_VERSION,
)


class TestSnapshotVerification:
    def test_snapshot_lifecycle_and_exact_reproduction(self, tmp_path):
        engine = AssertionEngine()
        rules = [
            AssertionRule(name="exact_title", type="exact_match", parameters={"expected": "Report Title"}),
            AssertionRule(name="contains_summary", type="contains_facts", parameters={"keywords": ["总结", "结论"]}),
            AssertionRule(name="calc_sum", type="recomputable", parameters={"operator": "sum", "operands": [10, 20], "claimed_result": 30}),
        ]

        # 模拟模型输出与断言执行记录
        record1_out = "Report Title\n这是最终总结与结论。"
        suite1 = engine.evaluate_suite(rules, record1_out)
        record1 = {
            "model": "model-1",
            "output_text": record1_out,
            "artifacts": {},
            "context": {},
            "results": [r.to_dict() for r in suite1.results],
        }

        # 生成快照数据
        snapshot_data = engine.create_snapshot(rules, [record1])
        assert snapshot_data["engine_version"] == ASSERTION_ENGINE_VERSION
        assert snapshot_data["rules_count"] == 3

        # 保存快照到磁盘
        snapshot_path = tmp_path / "assertions_snapshot.json"
        save_assertions_snapshot(snapshot_data, snapshot_path)
        assert snapshot_path.exists()

        # 读取并比对复验
        loaded_data = load_assertions_snapshot(snapshot_path)
        verification = engine.verify_snapshot(loaded_data)

        assert verification["verified"] is True
        assert verification["match_rate"] == 1.0
        assert len(verification["mismatches"]) == 0

        # 通过便捷文件接口复验
        file_ver = verify_assertions_snapshot_file(snapshot_path)
        assert file_ver["verified"] is True

    def test_snapshot_mismatch_detection(self, tmp_path):
        engine = AssertionEngine()
        rules = [
            AssertionRule(name="exact_code", type="exact_match", parameters={"expected": "200"}),
        ]
        suite = engine.evaluate_suite(rules, "200")
        record = {
            "model": "model-1",
            "output_text": "500",  # 篡改输出
            "artifacts": {},
            "context": {},
            "results": [r.to_dict() for r in suite.results],  # 记录的是 200 (passed=True)
        }

        snapshot_data = engine.create_snapshot(rules, [record])
        ver = engine.verify_snapshot(snapshot_data)

        assert ver["verified"] is False
        assert len(ver["mismatches"]) > 0
        assert ver["mismatches"][0]["rule_name"] == "exact_code"
