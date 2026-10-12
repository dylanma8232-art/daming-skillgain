"""四类确定性断言单元测试 - 零 LLM 调用"""
import pytest
from evaluator.assertions import (
    AssertionEngine,
    AssertionRule,
    AssertionType,
    ASSERTION_ENGINE_VERSION,
)
from evaluator.assertions.exact_match import evaluate_exact_match
from evaluator.assertions.contains_facts import evaluate_contains_facts
from evaluator.assertions.recomputable import evaluate_recomputable
from evaluator.assertions.script_eval import evaluate_script_eval


class TestExactMatchAssertion:
    def test_string_exact_match(self):
        rule = AssertionRule(
            name="test_str",
            type=AssertionType.EXACT_MATCH.value,
            parameters={"target_type": "string", "expected": "Hello World", "strip": True, "case_sensitive": True}
        )
        res = evaluate_exact_match(rule, "  Hello World  ")
        assert res.passed is True
        assert res.score == 1.0

        res_fail = evaluate_exact_match(rule, "hello world")
        assert res_fail.passed is False
        assert res_fail.score == 0.0

    def test_numeric_exact_match_with_tolerance(self):
        rule = AssertionRule(
            name="test_num",
            type=AssertionType.EXACT_MATCH.value,
            parameters={"target_type": "number", "expected": 123.456, "tolerance": 0.01}
        )
        res = evaluate_exact_match(rule, 123.46)
        assert res.passed is True

        res_extract = evaluate_exact_match(rule, "最终结果为: 123.458 元")
        assert res_extract.passed is True

        res_fail = evaluate_exact_match(rule, 124.0)
        assert res_fail.passed is False

    def test_json_exact_match(self):
        rule = AssertionRule(
            name="test_json",
            type=AssertionType.EXACT_MATCH.value,
            parameters={
                "target_type": "json",
                "expected": {"status": "ok", "count": 10, "ratio": 0.5},
                "float_tolerance": 1e-4
            }
        )
        target = '{"status": "ok", "count": 10, "ratio": 0.50001}'
        res = evaluate_exact_match(rule, target)
        assert res.passed is True

        target_bad = '{"status": "fail", "count": 10, "ratio": 0.5}'
        res_bad = evaluate_exact_match(rule, target_bad)
        assert res_bad.passed is False


class TestContainsFactsAssertion:
    def test_facts_and_keywords(self):
        rule = AssertionRule(
            name="test_facts",
            type=AssertionType.CONTAINS_FACTS.value,
            parameters={
                "facts": ["营业收入增长15%", "净利润突破2亿"],
                "keywords": ["财报", "现金流"],
                "min_facts": 2,
                "min_keywords": 2,
            }
        )
        text = "2025年财报显示，公司营业收入增长15%，净利润突破2亿，经营现金流健康。"
        res = evaluate_contains_facts(rule, text)
        assert res.passed is True
        assert res.score == 1.0

    def test_forbidden_words_violation(self):
        rule = AssertionRule(
            name="test_forbidden",
            type=AssertionType.CONTAINS_FACTS.value,
            parameters={
                "keywords": ["分析完成"],
                "forbidden": ["数据缺失", "无法处理"],
            }
        )
        text = "分析完成，但是由于数据缺失，部分内容无法处理。"
        res = evaluate_contains_facts(rule, text)
        assert res.passed is False
        assert "violated_forbidden" in res.evidence
        assert len(res.evidence["violated_forbidden"]) == 2


class TestRecomputableAssertion:
    def test_sum_and_mean(self):
        rule_sum = AssertionRule(
            name="test_sum",
            type=AssertionType.RECOMPUTABLE.value,
            parameters={
                "operator": "sum",
                "operands": [10.5, 20.5, 30.0],
                "claimed_result": 61.0,
                "tolerance": 0.01,
            }
        )
        res = evaluate_recomputable(rule_sum, "")
        assert res.passed is True
        assert res.evidence["recomputed_value"] == 61.0

        rule_mean = AssertionRule(
            name="test_mean",
            type=AssertionType.RECOMPUTABLE.value,
            parameters={
                "operator": "mean",
                "operands": [10, 20, 30],
                "claimed_result": 20.0,
            }
        )
        assert evaluate_recomputable(rule_mean, "").passed is True

    def test_cagr_and_custom_expr(self):
        rule_cagr = AssertionRule(
            name="test_cagr",
            type=AssertionType.RECOMPUTABLE.value,
            parameters={
                "operator": "cagr",
                "operands": [100.0, 144.0, 2.0],  # sqrt(1.44) - 1 = 0.2 (20%)
                "claimed_result": 0.2,
                "tolerance": 1e-4,
            }
        )
        assert evaluate_recomputable(rule_cagr, "").passed is True

        rule_expr = AssertionRule(
            name="test_expr",
            type=AssertionType.RECOMPUTABLE.value,
            parameters={
                "operator": "custom_expr",
                "expr": "(x1 + x2) * x3",
                "operands": [2.0, 3.0, 4.0],
                "claimed_result": 20.0,
            }
        )
        assert evaluate_recomputable(rule_expr, "").passed is True


class TestScriptEvalAssertion:
    def test_builtin_scripts(self):
        rule_art = AssertionRule(
            name="test_art",
            type=AssertionType.SCRIPT_EVAL.value,
            parameters={"script_name": "artifact_path_exists", "path": "summary.md"}
        )
        artifacts = {"summary.md": "# Summary", "data.csv": "a,b\n1,2"}
        assert evaluate_script_eval(rule_art, "", artifacts=artifacts).passed is True

        rule_csv = AssertionRule(
            name="test_csv",
            type=AssertionType.SCRIPT_EVAL.value,
            parameters={"script_name": "csv_well_formed"}
        )
        good_csv = "col1,col2\n10,20\n30,40"
        assert evaluate_script_eval(rule_csv, good_csv).passed is True

        bad_csv = "col1,col2\n10,20,30\n40"
        assert evaluate_script_eval(rule_csv, bad_csv).passed is False

    def test_balance_sheet_script(self):
        rule_bs = AssertionRule(
            name="test_bs",
            type=AssertionType.SCRIPT_EVAL.value,
            parameters={"script_name": "balance_sheet", "assets": 500, "liabilities": 300, "equity": 200}
        )
        assert evaluate_script_eval(rule_bs, "").passed is True


class TestAssertionEngineSuite:
    def test_suite_execution_zero_llm(self):
        engine = AssertionEngine()
        rules = [
            AssertionRule(name="r1", type="exact_match", parameters={"target_type": "string", "expected": "OK 任务完成"}),
            AssertionRule(name="r2", type="contains_facts", parameters={"keywords": ["完成"]}),
            AssertionRule(name="r3", type="recomputable", parameters={"operator": "sum", "operands": [1, 2], "claimed_result": 3}),
            AssertionRule(name="r4", type="script_eval", parameters={"script_name": "completion_signal"}),
        ]
        suite_res = engine.evaluate_suite(
            rules,
            output_text="OK 任务完成",
            artifacts={},
            context={"stdout": "OK 任务完成"}
        )
        assert suite_res.passed is True
        assert suite_res.total_assertions == 4
        assert suite_res.passed_count == 4
        assert suite_res.failed_count == 0
        assert suite_res.score == 100.0
