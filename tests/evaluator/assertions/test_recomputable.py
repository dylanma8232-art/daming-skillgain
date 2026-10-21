import pytest
from unittest.mock import patch
from evaluator.assertions.recomputable import evaluate_recomputable
from evaluator.assertions.models import AssertionRule

def test_recomputable_csv_parsing_failure():
    """Test that CSV parsing failure in recomputable falls back to empty operands"""
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

    # The operands fallback to empty list.
    # With empty numeric_operands, `sum` gives 0.
    # The claimed value is 100.
    # So it should fail because recomputed (0) != claimed (100)
    assert result.passed is False
    assert result.evidence["recomputed_value"] == 0
    assert result.evidence["claimed_value"] == 100.0
    assert result.evidence["operands_count"] == 0

def test_recomputable_missing_csv_column():
    """Test that missing CSV column results in empty operands and handles gracefully"""
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
    assert result.evidence["recomputed_value"] == 0
    assert result.evidence["operands_count"] == 0

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
