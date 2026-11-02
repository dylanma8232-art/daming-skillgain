import pytest
from unittest.mock import patch
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

from unittest.mock import patch

def test_recomputable_csv_parsing_failure():
    """CSV 解析失败不得回退为空操作数，应报告错误（项 23）"""
    rule = AssertionRule(
        name="test_csv_fallback",
        type="recomputable",
        parameters={
            "csv_column": "amount",
            "operator": "sum",
            "claimed_result": "100"
        }
    )

    # Mock csv.DictReader to throw an exception
    with patch('csv.DictReader', side_effect=Exception('Mocked CSV Error')):
        result = evaluate_recomputable(rule, "some,text\n1,2")

    # 解析失败必须判定不通过，错误码为 csv_parse_failed
    assert result.passed is False
    assert result.error == "csv_parse_failed"

def test_recomputable_missing_csv_column():
    """CSV 列未匹配到任何数据不得回退为空操作数，应判定不通过（项 23）"""
    rule = AssertionRule(
        name="test_csv_missing_column",
        type="recomputable",
        parameters={
            "csv_column": "missing_column",
            "operator": "sum",
            "claimed_result": "100"
        }
    )

    # Valid CSV but doesn't have the "missing_column"
    csv_data = "amount,name\n10,apple\n20,banana"

    result = evaluate_recomputable(rule, csv_data)

    assert result.passed is False
    assert result.error == "csv_no_matching_rows"

def test_recomputable_unparseable_number_in_csv():
    """Test that CSV containing non-numeric data in the specified column ignores it"""
    rule = AssertionRule(
        name="test_csv_unparseable_number",
        type="recomputable",
        parameters={
            "csv_column": "amount",
            "operator": "sum",
            "claimed_result": "30"
        }
    )

    # Valid CSV, but has some unparseable numbers
    csv_data = "amount,name\n10,apple\nNotANumber,banana\n20,orange"

    result = evaluate_recomputable(rule, csv_data)

    # sum of [10, 20] should be 30
    assert result.passed is True
    assert result.evidence["recomputed_value"] == 30.0
    assert result.evidence["operands_count"] == 2
