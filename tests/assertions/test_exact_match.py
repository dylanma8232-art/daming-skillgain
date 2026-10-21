import pytest
import json
import hashlib
from evaluator.assertions.models import AssertionRule, AssertionResult, AssertionType
from evaluator.assertions.exact_match import evaluate_exact_match

def create_rule(target_type: str, params: dict) -> AssertionRule:
    params["target_type"] = target_type
    return AssertionRule(
        name="test_rule",
        type=AssertionType.EXACT_MATCH.value,
        parameters=params
    )

class TestExactMatchString:
    def test_string_exact_match(self):
        rule = create_rule("string", {"expected": "hello"})
        res = evaluate_exact_match(rule, "hello")
        assert res.passed is True
        assert res.score == 1.0

    def test_string_mismatch(self):
        rule = create_rule("string", {"expected": "hello"})
        res = evaluate_exact_match(rule, "world")
        assert res.passed is False
        assert res.score == 0.0

    def test_string_strip(self):
        rule = create_rule("string", {"expected": "hello", "strip": True})
        res = evaluate_exact_match(rule, "  hello  ")
        assert res.passed is True

        rule_no_strip = create_rule("string", {"expected": "hello", "strip": False})
        res_no_strip = evaluate_exact_match(rule_no_strip, "  hello  ")
        assert res_no_strip.passed is False

    def test_string_case_sensitive(self):
        rule_case_insensitive = create_rule("string", {"expected": "HELLO", "case_sensitive": False})
        res = evaluate_exact_match(rule_case_insensitive, "hello")
        assert res.passed is True

        rule_case_sensitive = create_rule("string", {"expected": "HELLO", "case_sensitive": True})
        res_sensitive = evaluate_exact_match(rule_case_sensitive, "hello")
        assert res_sensitive.passed is False

    def test_string_normalize_whitespace(self):
        rule = create_rule("string", {"expected": "hello world", "normalize_whitespace": True})
        res = evaluate_exact_match(rule, "hello \n \t world")
        assert res.passed is True

class TestExactMatchNumeric:
    def test_numeric_exact_match(self):
        rule = create_rule("number", {"expected": 42.0})
        res = evaluate_exact_match(rule, 42)
        assert res.passed is True

    def test_numeric_tolerance(self):
        rule = create_rule("number", {"expected": 42.0, "tolerance": 0.5})
        res = evaluate_exact_match(rule, 42.4)
        assert res.passed is True

        res2 = evaluate_exact_match(rule, 42.6)
        assert res2.passed is False

    def test_numeric_relative_tolerance(self):
        rule = create_rule("number", {"expected": 100.0, "relative_tolerance": 0.05, "tolerance": 0.0})
        res = evaluate_exact_match(rule, 104.0)
        assert res.passed is True

        res2 = evaluate_exact_match(rule, 106.0)
        assert res2.passed is False

    def test_numeric_extract_regex(self):
        rule = create_rule("numeric", {"expected": 42.0, "extract_regex": r"answer is (\d+)"})
        res = evaluate_exact_match(rule, "the answer is 42!")
        assert res.passed is True

        res2 = evaluate_exact_match(rule, "no answer here")
        assert res2.passed is False

    def test_numeric_fallback_extraction(self):
        rule = create_rule("numeric", {"expected": 3.14})
        res = evaluate_exact_match(rule, "pi is about 3.14159")
        # tolerance is 1e-4 by default
        assert res.passed is False # 3.14159 - 3.14 = 0.00159 > 1e-4

        rule2 = create_rule("numeric", {"expected": -42})
        res2 = evaluate_exact_match(rule2, "  -42  ")
        assert res2.passed is True

    def test_numeric_invalid_parse(self):
        rule = create_rule("numeric", {"expected": 42.0})
        res = evaluate_exact_match(rule, "hello world")
        assert res.passed is False
        assert "无法从目标内容中解析" in res.message


class TestExactMatchJson:
    def test_json_exact_dict(self):
        rule = create_rule("json", {"expected": {"a": 1, "b": "str"}})
        res = evaluate_exact_match(rule, {"b": "str", "a": 1})
        assert res.passed is True

    def test_json_list(self):
        rule = create_rule("json", {"expected": [1, 2, 3]})
        res = evaluate_exact_match(rule, [1, 2, 3])
        assert res.passed is True

        res2 = evaluate_exact_match(rule, [1, 2])
        assert res2.passed is False

    def test_json_float_tolerance(self):
        rule = create_rule("json", {"expected": {"val": 3.1415}, "float_tolerance": 0.001})
        res = evaluate_exact_match(rule, {"val": 3.1419})
        assert res.passed is True

        res2 = evaluate_exact_match(rule, {"val": 3.15})
        assert res2.passed is False

    def test_json_ignore_extra_keys(self):
        rule = create_rule("json", {"expected": {"a": 1}, "ignore_extra_keys": True})
        res = evaluate_exact_match(rule, {"a": 1, "b": 2})
        assert res.passed is True

        rule2 = create_rule("json", {"expected": {"a": 1}, "ignore_extra_keys": False})
        res2 = evaluate_exact_match(rule2, {"a": 1, "b": 2})
        assert res2.passed is False

    def test_json_parse_strings(self):
        rule = create_rule("json", {"expected": '{"a": 1}'})
        res = evaluate_exact_match(rule, '{"a": 1}')
        assert res.passed is True

    def test_json_extract_from_text(self):
        rule = create_rule("json", {"expected": {"a": 1}})
        res = evaluate_exact_match(rule, 'Here is the json: {"a": 1} inside text.')
        assert res.passed is True

    def test_json_invalid_expected(self):
        rule = create_rule("json", {"expected": '{invalid_json'})
        res = evaluate_exact_match(rule, '{"a": 1}')
        assert res.passed is False
        assert res.error is not None

    def test_json_invalid_actual(self):
        rule = create_rule("json", {"expected": {"a": 1}})
        res = evaluate_exact_match(rule, 'not a json at all')
        assert res.passed is False

    def test_json_deep_differences(self):
        rule = create_rule("json", {"expected": {"a": {"b": 1}}})
        res = evaluate_exact_match(rule, {"a": {"b": 2}})
        assert res.passed is False

        rule2 = create_rule("json", {"expected": {"a": [1, 2]}})
        res2 = evaluate_exact_match(rule2, {"a": [1, 3]})
        assert res2.passed is False

        rule3 = create_rule("json", {"expected": {"a": 1}})
        res3 = evaluate_exact_match(rule3, {"b": 1})
        assert res3.passed is False # missing key 'a'

class TestExactMatchHash:
    def test_hash_sha256(self):
        data = b"hello world"
        expected_hash = hashlib.sha256(data).hexdigest()
        rule = create_rule("hash", {"hash_algo": "sha256", "expected_hash": expected_hash})
        res = evaluate_exact_match(rule, data)
        assert res.passed is True

        res2 = evaluate_exact_match(rule, b"wrong data")
        assert res2.passed is False

    def test_hash_md5(self):
        data = "hello world"
        expected_hash = hashlib.md5(data.encode()).hexdigest()
        rule = create_rule("file", {"hash_algo": "md5", "expected": expected_hash})
        res = evaluate_exact_match(rule, data)
        assert res.passed is True

    def test_hash_non_bytes(self):
        rule = create_rule("hash", {"hash_algo": "sha256", "expected_hash": hashlib.sha256(b"").hexdigest()})
        res = evaluate_exact_match(rule, 12345) # Not bytes, not str
        assert res.passed is True # fallback to b""

class TestExactMatchUnsupported:
    def test_unsupported_type(self):
        rule = create_rule("magic", {"expected": "wow"})
        res = evaluate_exact_match(rule, "wow")
        assert res.passed is False
        assert res.error == "unsupported_target_type"

class TestExactMatchCoverage:
    def test_json_missing_key_ignore_extra_keys(self):
        rule = create_rule("json", {"expected": {"a": 1, "b": 2}, "ignore_extra_keys": True})
        res = evaluate_exact_match(rule, {"a": 1})
        assert res.passed is False

    def test_json_mismatch_value(self):
        rule = create_rule("json", {"expected": {"a": "value1"}})
        res = evaluate_exact_match(rule, {"a": "value2"})
        assert res.passed is False

    def test_numeric_extract_regex_invalid_float(self):
        rule = create_rule("number", {"expected": 42.0, "extract_regex": r"answer is (.*)"})
        # The regex will extract 'notanumber', float() will raise ValueError
        res = evaluate_exact_match(rule, "the answer is notanumber")
        assert res.passed is False

    def test_json_extract_from_text_invalid_json(self):
        rule = create_rule("json", {"expected": {"a": 1}})
        # We need something that looks like json for the regex `r"(\{.*\}|\[.*\])"` but is invalid
        res = evaluate_exact_match(rule, 'some text { invalid json } more text')
        assert res.passed is False
