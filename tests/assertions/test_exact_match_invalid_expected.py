import pytest
import json

from evaluator.assertions.models import AssertionRule, AssertionResult
from evaluator.assertions.exact_match import evaluate_exact_match

def test_exact_match_json_parse_error():
    """Test that a malformed expected JSON string returns a parse error Result."""
    rule = AssertionRule(
        name="Test JSON Match",
        type="exact_match",
        parameters={
            "target_type": "json",
            "expected": "{invalid_json: true"  # Missing closing brace and quotes
        }
    )

    target_content = '{"valid": "json"}'

    result = evaluate_exact_match(rule, target_content)

    assert isinstance(result, AssertionResult)
    assert not result.passed
    assert result.score == 0.0
    assert result.error is not None
    assert "expected_parse_error" in result.evidence
    assert result.message == "断言配置中的 expected JSON 无法解析"
