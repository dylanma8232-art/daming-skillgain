import pytest
from evaluator.assertions.recomputable import evaluate_recomputable
from evaluator.assertions.models import AssertionRule

def test_evaluate_recomputable_calculation_errors():
    # Test ZeroDivisionError for 'ratio'
    rule = AssertionRule(
        name="test_zero_div_ratio",
        type="recomputable",
        parameters={
            "operator": "ratio",
            "operands": [10, 0],
            "claimed_result": "0"
        }
    )
    result = evaluate_recomputable(rule, "0")
    assert result.passed is False
    assert result.error == "除数不能为0"
    assert "可重算确定性计算失败" in result.message

    # Test ZeroDivisionError for 'percentage'
    rule = AssertionRule(
        name="test_zero_div_percentage",
        type="recomputable",
        parameters={
            "operator": "percentage",
            "operands": [10, 0],
            "claimed_result": "0"
        }
    )
    result = evaluate_recomputable(rule, "0")
    assert result.passed is False
    assert result.error == "除数不能为0"
    assert "可重算确定性计算失败" in result.message

    # Test ValueError for empty operands on 'mean'
    rule = AssertionRule(
        name="test_empty_mean",
        type="recomputable",
        parameters={
            "operator": "mean",
            "operands": [],
            "claimed_result": "0"
        }
    )
    result = evaluate_recomputable(rule, "0")
    assert result.passed is False
    assert result.error == "平均值计算操作数为空"
    assert "可重算确定性计算失败" in result.message

    # Test ValueError for not enough operands on 'ratio'
    rule = AssertionRule(
        name="test_not_enough_ratio",
        type="recomputable",
        parameters={
            "operator": "ratio",
            "operands": [10],
            "claimed_result": "0"
        }
    )
    result = evaluate_recomputable(rule, "0")
    assert result.passed is False
    assert result.error == "比例计算至少需要2个操作数"
    assert "可重算确定性计算失败" in result.message

    # Test ValueError for not enough operands on 'difference'
    rule = AssertionRule(
        name="test_not_enough_diff",
        type="recomputable",
        parameters={
            "operator": "difference",
            "operands": [10],
            "claimed_result": "0"
        }
    )
    result = evaluate_recomputable(rule, "0")
    assert result.passed is False
    assert result.error == "差值计算至少需要2个操作数"
    assert "可重算确定性计算失败" in result.message

    # Test ValueError for not enough operands on 'cagr'
    rule = AssertionRule(
        name="test_not_enough_cagr",
        type="recomputable",
        parameters={
            "operator": "cagr",
            "operands": [100, 150],
            "claimed_result": "0"
        }
    )
    result = evaluate_recomputable(rule, "0")
    assert result.passed is False
    assert result.error == "CAGR 需要 [begin, end, periods]"
    assert "可重算确定性计算失败" in result.message

    # Test ValueError for invalid operands on 'cagr' (begin <= 0)
    rule = AssertionRule(
        name="test_invalid_cagr_begin",
        type="recomputable",
        parameters={
            "operator": "cagr",
            "operands": [0, 150, 2],
            "claimed_result": "0"
        }
    )
    result = evaluate_recomputable(rule, "0")
    assert result.passed is False
    assert result.error == "CAGR 参数必须大于0"
    assert "可重算确定性计算失败" in result.message

    # Test ValueError for empty operands on 'min'
    rule = AssertionRule(
        name="test_empty_min",
        type="recomputable",
        parameters={
            "operator": "min",
            "operands": [],
            "claimed_result": "0"
        }
    )
    result = evaluate_recomputable(rule, "0")
    assert result.passed is False
    assert result.error == "操作数为空"
    assert "可重算确定性计算失败" in result.message

    # Test ValueError for empty operands on 'max'
    rule = AssertionRule(
        name="test_empty_max",
        type="recomputable",
        parameters={
            "operator": "max",
            "operands": [],
            "claimed_result": "0"
        }
    )
    result = evaluate_recomputable(rule, "0")
    assert result.passed is False
    assert result.error == "操作数为空"
    assert "可重算确定性计算失败" in result.message

    # Test unknown operator
    rule = AssertionRule(
        name="test_unknown_op",
        type="recomputable",
        parameters={
            "operator": "unknown_op_xyz",
            "operands": [1, 2],
            "claimed_result": "0"
        }
    )
    result = evaluate_recomputable(rule, "0")
    assert result.passed is False
    assert result.error == "unknown_operator"
