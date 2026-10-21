import pytest
from evaluator.assertions.exact_match import evaluate_exact_match
from evaluator.assertions.models import AssertionRule

def test_json_parse_error():
    rule = AssertionRule(
        name="Test Rule",
        type="exact_match",
        weight=1.0,
        parameters={
            "target_type": "json",
            "expected": '{"key": "value"}'
        }
    )
    result = evaluate_exact_match(rule, "this is malformed json } {")
    assert result.passed is False
    assert result.score == 0.0
    assert "无法解析为有效 JSON 结构" in result.message

def test_json_fallback_regex():
    rule = AssertionRule(
        name="Test Rule",
        type="exact_match",
        weight=1.0,
        parameters={
            "target_type": "json",
            "expected": '{"key": "value"}'
        }
    )
    result = evaluate_exact_match(rule, 'here is the json {"key": "value"}')
    assert result.passed is True
    assert result.score == 1.0

def test_json_fallback_regex_malformed():
    rule = AssertionRule(
        name="Test Rule",
        type="exact_match",
        weight=1.0,
        parameters={
            "target_type": "json",
            "expected": '{"key": "value"}'
        }
    )
    result = evaluate_exact_match(rule, 'here is the json {"key": "value"')
    assert result.passed is False
    assert result.score == 0.0
    assert "无法解析为有效 JSON 结构" in result.message


def test_json_fallback_regex_extract_parse_error():
    rule = AssertionRule(
        name="Test Rule",
        type="exact_match",
        weight=1.0,
        parameters={
            "target_type": "json",
            "expected": '{"key": "value"}'
        }
    )
    result = evaluate_exact_match(rule, 'here is {"key": unquoted_value} inside')
    assert result.passed is False
    assert result.score == 0.0
    assert "无法解析为有效 JSON 结构" in result.message
