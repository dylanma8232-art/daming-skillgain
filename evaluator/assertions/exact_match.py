"""精确匹配断言 (Exact Match) - 确定性零 LLM 实现"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

LOGGER = logging.getLogger(__name__)

from .models import AssertionResult, AssertionRule, AssertionType


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _deep_json_equal(a: Any, b: Any, float_tol: float = 1e-4, ignore_extra_keys: bool = False) -> tuple[bool, str]:
    if isinstance(a, dict) and isinstance(b, dict):
        if not ignore_extra_keys and set(a.keys()) != set(b.keys()):
            return False, f"JSON 键不匹配: 期望键 {sorted(b.keys())}, 实际键 {sorted(a.keys())}"
        for k, v_expected in b.items():
            if k not in a:
                return False, f"缺少键: {k}"
            ok, msg = _deep_json_equal(a[k], v_expected, float_tol, ignore_extra_keys)
            if not ok:
                return False, f"键 '{k}' 差异 -> {msg}"
        return True, ""

    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False, f"列表长度不匹配: 期望 {len(b)}, 实际 {len(a)}"
        for idx, (item_a, item_b) in enumerate(zip(a, b)):
            ok, msg = _deep_json_equal(item_a, item_b, float_tol, ignore_extra_keys)
            if not ok:
                return False, f"列表索引 [{idx}] 差异 -> {msg}"
        return True, ""

    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        diff = abs(float(a) - float(b))
        if diff > float_tol:
            return False, f"数值不匹配: 期望 {b}, 实际 {a} (误差 {diff:.6f} > 容差 {float_tol})"
        return True, ""

    if a != b:
        return False, f"值不匹配: 期望 '{b}', 实际 '{a}'"
    return True, ""


def evaluate_exact_match(rule: AssertionRule, target_content: Any) -> AssertionResult:
    """执行精确匹配判定"""
    params = rule.parameters
    target_type = params.get("target_type", "string")

    if target_type == "string":
        expected = str(params.get("expected", ""))
        actual = str(target_content or "")
        case_sensitive = params.get("case_sensitive", True)
        strip = params.get("strip", True)
        normalize_ws = params.get("normalize_whitespace", False)

        test_actual = actual
        test_expected = expected
        if strip:
            test_actual = test_actual.strip()
            test_expected = test_expected.strip()
        if normalize_ws:
            test_actual = _normalize_ws(test_actual)
            test_expected = _normalize_ws(test_expected)
        if not case_sensitive:
            test_actual = test_actual.lower()
            test_expected = test_expected.lower()

        passed = test_actual == test_expected
        evidence = {
            "mode": "string",
            "expected": expected,
            "actual": actual[:1000] if len(actual) > 1000 else actual,
            "case_sensitive": case_sensitive,
            "strip": strip,
        }
        msg = "字符串精确匹配成功" if passed else f"字符串不全等: 期望长度 {len(test_expected)}, 实际长度 {len(test_actual)}"
        return AssertionResult(
            name=rule.name,
            type=AssertionType.EXACT_MATCH.value,
            passed=passed,
            score=1.0 if passed else 0.0,
            weight=rule.weight,
            evidence=evidence,
            message=msg,
        )

    elif target_type in ("number", "numeric"):
        expected_val = float(params.get("expected", 0.0))
        tolerance = float(params.get("tolerance", 1e-4))
        rel_tol = float(params.get("relative_tolerance", 0.0))

        actual_val = None
        extract_regex = params.get("extract_regex")
        if isinstance(target_content, (int, float)):
            actual_val = float(target_content)
        elif isinstance(target_content, str):
            if extract_regex:
                try:
                    match = re.search(extract_regex, target_content)
                    if match:
                        try:
                            actual_val = float(match.group(1))
                        except (ValueError, IndexError) as err:
                            LOGGER.debug("extract_regex match group is not a valid float: %s", err)
                except re.error as err:
                    LOGGER.warning("Invalid extract_regex '%s': %s", extract_regex, err)
            if actual_val is None:
                # 尝试直接转浮点或寻找首个数字
                try:
                    actual_val = float(target_content.strip())
                except ValueError:
                    num_match = re.search(r"[-+]?(?:\d*\.\d+|\d+)", target_content)
                    if num_match:
                        actual_val = float(num_match.group(0))

        if actual_val is None:
            return AssertionResult(
                name=rule.name,
                type=AssertionType.EXACT_MATCH.value,
                passed=False,
                score=0.0,
                weight=rule.weight,
                evidence={"expected": expected_val, "actual_raw": str(target_content)[:200]},
                message="无法从目标内容中解析数值",
            )

        diff = abs(actual_val - expected_val)
        passed = diff <= tolerance
        if not passed and rel_tol > 0 and expected_val != 0:
            passed = (diff / abs(expected_val)) <= rel_tol

        evidence = {
            "mode": "numeric",
            "expected": expected_val,
            "actual": actual_val,
            "diff": round(diff, 6),
            "tolerance": tolerance,
        }
        msg = f"数值匹配成功 (误差 {diff:.6f} <= {tolerance})" if passed else f"数值超出容差: 期望 {expected_val}, 实际 {actual_val}, 差值 {diff:.6f}"
        return AssertionResult(
            name=rule.name,
            type=AssertionType.EXACT_MATCH.value,
            passed=passed,
            score=1.0 if passed else 0.0,
            weight=rule.weight,
            evidence=evidence,
            message=msg,
        )

    elif target_type == "json":
        expected_raw = params.get("expected")
        if isinstance(expected_raw, str):
            try:
                expected_obj = json.loads(expected_raw)
            except Exception as e:
                return AssertionResult(
                    name=rule.name,
                    type=AssertionType.EXACT_MATCH.value,
                    passed=False,
                    score=0.0,
                    weight=rule.weight,
                    evidence={"expected_parse_error": str(e)},
                    message="断言配置中的 expected JSON 无法解析",
                    error=str(e),
                )
        else:
            expected_obj = expected_raw

        actual_raw = target_content
        if isinstance(actual_raw, str):
            try:
                actual_obj = json.loads(actual_raw)
            except Exception:
                # 尝试从文本中提取第一个 JSON 块
                json_match = re.search(r"(\{.*\}|\[.*\])", actual_raw, re.DOTALL)
                if json_match:
                    try:
                        actual_obj = json.loads(json_match.group(0))
                    except Exception as e:
                        actual_obj = None
                else:
                    actual_obj = None
        else:
            actual_obj = actual_raw

        if actual_obj is None:
            return AssertionResult(
                name=rule.name,
                type=AssertionType.EXACT_MATCH.value,
                passed=False,
                score=0.0,
                weight=rule.weight,
                evidence={"actual_raw": str(actual_raw)[:300]},
                message="目标内容无法解析为有效 JSON 结构",
            )

        float_tol = float(params.get("float_tolerance", 1e-4))
        ignore_extra = bool(params.get("ignore_extra_keys", False))
        equal, diff_msg = _deep_json_equal(actual_obj, expected_obj, float_tol, ignore_extra)

        evidence = {
            "mode": "json",
            "diff_detail": diff_msg,
            "ignore_extra_keys": ignore_extra,
        }
        return AssertionResult(
            name=rule.name,
            type=AssertionType.EXACT_MATCH.value,
            passed=equal,
            score=1.0 if equal else 0.0,
            weight=rule.weight,
            evidence=evidence,
            message="JSON 全等匹配成功" if equal else f"JSON 结构不匹配: {diff_msg}",
        )

    elif target_type in ("file", "hash"):
        algo = params.get("hash_algo", "sha256").lower()
        expected_hash = str(params.get("expected_hash") or params.get("expected") or "").lower().strip()
        data_bytes = target_content.encode("utf-8") if isinstance(target_content, str) else target_content
        if not isinstance(data_bytes, bytes):
            data_bytes = b""

        hasher = hashlib.sha256() if algo == "sha256" else hashlib.md5()
        hasher.update(data_bytes)
        actual_hash = hasher.hexdigest().lower()

        passed = actual_hash == expected_hash
        evidence = {"algo": algo, "expected_hash": expected_hash, "actual_hash": actual_hash}
        return AssertionResult(
            name=rule.name,
            type=AssertionType.EXACT_MATCH.value,
            passed=passed,
            score=1.0 if passed else 0.0,
            weight=rule.weight,
            evidence=evidence,
            message="文件哈希全等" if passed else f"文件哈希不一致 (实际 {actual_hash} != 期望 {expected_hash})",
        )

    else:
        return AssertionResult(
            name=rule.name,
            type=AssertionType.EXACT_MATCH.value,
            passed=False,
            score=0.0,
            weight=rule.weight,
            evidence={"unsupported_type": target_type},
            message=f"不支持的 exact_match 目标类型: {target_type}",
            error="unsupported_target_type",
        )
