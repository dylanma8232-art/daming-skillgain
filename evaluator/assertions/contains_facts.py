"""包含关键事实断言 (Contains Facts) - 确定性零 LLM 实现"""
from __future__ import annotations

import re
from typing import Any

from .models import AssertionResult, AssertionRule, AssertionType


def evaluate_contains_facts(rule: AssertionRule, target_content: Any) -> AssertionResult:
    """确定性验证关键事实、关键字、正则模式与负向禁用词"""
    params = rule.parameters
    text = str(target_content or "")
    case_sensitive = bool(params.get("case_sensitive", False))
    search_text = text if case_sensitive else text.lower()

    facts = [str(f) for f in params.get("facts", []) if str(f).strip()]
    keywords = [str(k) for k in params.get("keywords", []) if str(k).strip()]
    regexes = [str(r) for r in params.get("regexes", []) if str(r).strip()]
    forbidden = [str(fb) for fb in params.get("forbidden", []) if str(fb).strip()]

    # 1. 关键事实核查
    found_facts = []
    missing_facts = []
    for fact in facts:
        target_fact = fact if case_sensitive else fact.lower()
        if target_fact in search_text:
            found_facts.append(fact)
        else:
            missing_facts.append(fact)

    # 2. 关键字核查
    found_keywords = []
    missing_keywords = []
    for kw in keywords:
        target_kw = kw if case_sensitive else kw.lower()
        if target_kw in search_text:
            found_keywords.append(kw)
        else:
            missing_keywords.append(kw)

    # 3. 正则表达式核查
    matched_regexes = []
    unmatched_regexes = []
    regex_flags = 0 if case_sensitive else re.IGNORECASE
    for pat in regexes:
        try:
            m = re.search(pat, text, regex_flags)
            if m:
                matched_regexes.append({"pattern": pat, "matched": m.group(0)[:100]})
            else:
                unmatched_regexes.append(pat)
        except re.error as e:
            unmatched_regexes.append(f"{pat} (正则错误: {e})")

    # 4. 负向禁用词核查 (出现则违背)
    violated_forbidden = []
    for fb in forbidden:
        target_fb = fb if case_sensitive else fb.lower()
        if target_fb in search_text:
            violated_forbidden.append(fb)

    # 计算判定通过条件
    total_checks = len(facts) + len(keywords) + len(regexes)
    passed_checks = len(found_facts) + len(found_keywords) + len(matched_regexes)

    min_facts = int(params.get("min_facts", len(facts)))
    min_keywords = int(params.get("min_keywords", len(keywords)))
    min_regexes = int(params.get("min_regexes", len(regexes)))

    facts_ok = len(found_facts) >= min_facts
    keywords_ok = len(found_keywords) >= min_keywords
    regexes_ok = len(matched_regexes) >= min_regexes
    forbidden_ok = len(violated_forbidden) == 0

    overall_passed = facts_ok and keywords_ok and regexes_ok and forbidden_ok
    if total_checks > 0:
        base_score = passed_checks / total_checks
    else:
        base_score = 1.0 if forbidden_ok else 0.0

    score = base_score if forbidden_ok else 0.0

    evidence = {
        "found_facts": found_facts,
        "missing_facts": missing_facts,
        "found_keywords": found_keywords,
        "missing_keywords": missing_keywords,
        "matched_regexes": matched_regexes,
        "unmatched_regexes": unmatched_regexes,
        "violated_forbidden": violated_forbidden,
        "total_checks": total_checks,
        "passed_checks": passed_checks,
    }

    reasons = []
    if missing_facts:
        reasons.append(f"缺少事实 ({len(missing_facts)}项)")
    if missing_keywords:
        reasons.append(f"缺少关键字 ({len(missing_keywords)}项)")
    if unmatched_regexes:
        reasons.append(f"正则未匹配 ({len(unmatched_regexes)}项)")
    if violated_forbidden:
        reasons.append(f"包含禁用词 ({len(violated_forbidden)}项: {violated_forbidden})")

    if overall_passed:
        message = f"关键事实核验通过 (命中 {passed_checks}/{total_checks})"
    else:
        message = f"关键事实核验未通过: {'; '.join(reasons)}"

    return AssertionResult(
        name=rule.name,
        type=AssertionType.CONTAINS_FACTS.value,
        passed=overall_passed,
        score=score,
        weight=rule.weight,
        evidence=evidence,
        message=message,
    )
