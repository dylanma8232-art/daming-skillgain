import pytest
from evaluator.assertions.models import AssertionRule
from evaluator.assertions.recomputable import evaluate_recomputable

def create_rule(operator, operands, claimed_result=None, **kwargs):
    params = {"operator": operator, "operands": operands}
    if claimed_result is not None:
        params["claimed_result"] = claimed_result
    params.update(kwargs)
    return AssertionRule(name=f"test_{operator}", type="recomputable", parameters=params)

def test_sum():
    # Happy path
    rule = create_rule("sum", [1.5, 2.5], 4.0)
    res = evaluate_recomputable(rule, "")
    assert res.passed
    assert res.score == 1.0

    # Text parse fallback with commas
    rule2 = create_rule("add", ["1,000.5", "2,000"], 3000.5)
    res2 = evaluate_recomputable(rule2, "")
    assert res2.passed

def test_mean():
    rule = create_rule("mean", [10, 20, 30], 20.0)
    res = evaluate_recomputable(rule, "")
    assert res.passed

    rule_avg = create_rule("average", [5, 15], 10.0)
    res_avg = evaluate_recomputable(rule_avg, "")
    assert res_avg.passed

    # Error: empty operands
    rule_err = create_rule("avg", [], 0)
    res_err = evaluate_recomputable(rule_err, "")
    assert not res_err.passed
    assert "平均值计算操作数为空" in res_err.message

def test_ratio():
    rule = create_rule("ratio", [10, 2], 5.0)
    res = evaluate_recomputable(rule, "")
    assert res.passed

    # Division by zero
    rule2 = create_rule("div", [10, 0], 0)
    res2 = evaluate_recomputable(rule2, "")
    assert not res2.passed
    assert "除数不能为0" in str(res2.evidence.get("calc_error", ""))

    # Not enough operands
    rule3 = create_rule("ratio", [10], 5.0)
    res3 = evaluate_recomputable(rule3, "")
    assert not res3.passed

def test_percentage():
    rule = create_rule("percentage", [1, 4], 25.0)
    res = evaluate_recomputable(rule, "")
    assert res.passed

    rule2 = create_rule("percent", [3, 4], 75.0)
    assert evaluate_recomputable(rule2, "").passed

    # Division by zero
    rule_err = create_rule("percentage", [1, 0], 0)
    assert not evaluate_recomputable(rule_err, "").passed

def test_difference():
    rule = create_rule("difference", [10, 3], 7.0)
    assert evaluate_recomputable(rule, "").passed

    rule_diff = create_rule("diff", [5.5, 2.1], 3.4)
    assert evaluate_recomputable(rule_diff, "").passed

    rule_delta = create_rule("delta", [100, 90], 10.0)
    assert evaluate_recomputable(rule_delta, "").passed

    # Not enough operands
    rule_err = create_rule("diff", [10], 0)
    assert not evaluate_recomputable(rule_err, "").passed

def test_growth_rate():
    # cagr: (end / begin) ** (1/n) - 1
    # begin=100, end=121, periods=2 => (121/100)^(1/2) - 1 = 1.1 - 1 = 0.1
    rule = create_rule("cagr", [100, 121, 2], 0.1)
    res = evaluate_recomputable(rule, "")
    assert res.passed

    rule_growth = create_rule("growth_rate", [100, 133.1, 3], 0.1, tolerance=0.01)
    assert evaluate_recomputable(rule_growth, "").passed

    # Invalid params (begin=0)
    rule_err = create_rule("growth_rate", [0, 121, 2], 0)
    res_err = evaluate_recomputable(rule_err, "")
    assert not res_err.passed

    # Not enough operands
    rule_err2 = create_rule("cagr", [100, 121], 0.1)
    assert not evaluate_recomputable(rule_err2, "").passed

def test_min_max_count():
    r_min = create_rule("min", [5, 2, 9], 2.0)
    assert evaluate_recomputable(r_min, "").passed

    r_max = create_rule("max", [5, 2, 9], 9.0)
    assert evaluate_recomputable(r_max, "").passed

    r_count = create_rule("count", [5, 2, 9], 3.0)
    assert evaluate_recomputable(r_count, "").passed

    # Empty operands
    r_min_err = create_rule("min", [], 0)
    assert not evaluate_recomputable(r_min_err, "").passed

def test_custom_expr():
    # variables x1, x2, etc.
    rule = create_rule("expr", [10, 2], 12.0, expr="x1 + x2")
    assert evaluate_recomputable(rule, "").passed

    # custom variables
    rule2 = create_rule(
        "custom_expr", [], 50.0,
        expr="a * b",
        variables={"a": 5, "b": 10}
    )
    assert evaluate_recomputable(rule2, "").passed

    # Unsafe expr (should fail)
    rule3 = create_rule("expr", [10], 0, expr="import os")
    res3 = evaluate_recomputable(rule3, "")
    assert not res3.passed

def test_csv_column():
    csv_text = "id,sales\n1,100\n2,250\n3,150\n4,invalid"
    rule = AssertionRule(
        name="test_csv",
        type="recomputable",
        parameters={
            "operator": "sum",
            "csv_column": "sales",
            "claimed_result": 500.0
        }
    )
    res = evaluate_recomputable(rule, csv_text)
    assert res.passed

def test_csv_column_empty():
    csv_text = "id,sales\n1,100"
    rule = AssertionRule(
        name="test_csv",
        type="recomputable",
        parameters={
            "operator": "sum",
            "csv_column": "unknown_col",
            "claimed_result": 0.0
        }
    )
    res = evaluate_recomputable(rule, csv_text)
    # Operands will be empty, sum([]) is 0, so it will pass
    assert res.passed

def test_claimed_regex():
    text = "The total sales were 1,234.56 dollars."
    rule = AssertionRule(
        name="test_regex",
        type="recomputable",
        parameters={
            "operator": "sum",
            "operands": [1000, 234.56],
            "claimed_regex": r"total sales were ([\d,.]+)"
        }
    )
    res = evaluate_recomputable(rule, text)
    assert res.passed

def test_claimed_fallback():
    # no claimed_result or claimed_regex, it should try to find a number in the text
    text = "Result: 42"
    rule = create_rule("sum", [20, 22]) # claimed_result is None
    res = evaluate_recomputable(rule, text)
    assert res.passed

def test_missing_claimed_result():
    text = "Result: unknown"
    rule = create_rule("sum", [20, 22])
    res = evaluate_recomputable(rule, text)
    assert not res.passed
    assert res.error == "missing_claimed_result"

def test_unknown_operator():
    rule = create_rule("magic", [1, 2], 3)
    res = evaluate_recomputable(rule, "")
    assert not res.passed
    assert res.error == "unknown_operator"

def test_tolerance():
    # diff is 0.05, tolerance default is 1e-3, should fail
    rule = create_rule("sum", [1, 2], 3.05)
    res = evaluate_recomputable(rule, "")
    assert not res.passed

    # specify tolerance
    rule2 = create_rule("sum", [1, 2], 3.05, tolerance=0.1)
    res2 = evaluate_recomputable(rule2, "")
    assert res2.passed
