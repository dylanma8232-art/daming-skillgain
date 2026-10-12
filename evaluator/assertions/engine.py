"""断言执行引擎与快照比对复验 - 确定性零 LLM 实现"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from .contains_facts import evaluate_contains_facts
from .exact_match import evaluate_exact_match
from .models import (
    ASSERTION_ENGINE_VERSION,
    AssertionResult,
    AssertionRule,
    AssertionSuiteResult,
    AssertionType,
)
from .recomputable import evaluate_recomputable
from .script_eval import evaluate_script_eval




def clean_event_stream_lines(text: str) -> str:
    """从 output_text 中过滤排除原始调度事件流（如以 {"type": 开头的行），保留纯文本与模型回复正文"""
    if not text:
        return ""
    clean_lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if (s.startswith('{"type":') or s.startswith('{"sessionID":') or 
            s.startswith('{"part":') or s.startswith('{"id":')):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict) and any(k in obj for k in ("type", "sessionID", "part")):
                    part = obj.get("part", {})
                    if isinstance(part, dict) and "text" in part:
                        clean_lines.append(part["text"])
                    continue
            except Exception:
                pass
        clean_lines.append(line)
    return chr(10).join(clean_lines)


def extract_artifacts_text(artifacts: dict[str, Any]) -> str:
    """提取产物中的所有文本内容 (markdown / json / txt 等)"""
    if not artifacts:
        return ""
    chunks = []
    for fname, content in artifacts.items():
        if not isinstance(content, str) or not content.strip():
            continue
        base = Path(fname).name.lower()
        if base in ("result_manifest.json", "result_selection.json", "config.snapshot.json", "analysis.json", "anonymous_ids.json"):
            continue
        suffix = Path(fname).suffix.lower()
        if suffix in (".md", ".txt", ".json", ".yaml", ".yml", ".html"):
            body = content.strip()
            chunks.append(f"=== File: {fname} ===\n{body}")
    return (chr(10)*2).join(chunks)

class AssertionEngine:
    """确定性断言判定引擎"""

    def __init__(self, version: str = ASSERTION_ENGINE_VERSION):
        self.version = version

    def evaluate_rule(
        self,
        rule: AssertionRule,
        target_content: Any,
        artifacts: dict[str, Any] = None,
        context: dict[str, Any] = None,
    ) -> AssertionResult:
        """评估单条断言"""
        rtype = rule.type
        art = artifacts or {}
        ctx = context or {}

        if rtype == AssertionType.EXACT_MATCH.value:
            return evaluate_exact_match(rule, target_content)
        elif rtype == AssertionType.CONTAINS_FACTS.value:
            # 确保排除原始调度事件流干扰
            clean_content = clean_event_stream_lines(str(target_content or "")) if isinstance(target_content, str) else target_content
            return evaluate_contains_facts(rule, clean_content)
        elif rtype == AssertionType.RECOMPUTABLE.value:
            return evaluate_recomputable(rule, target_content)
        elif rtype == AssertionType.SCRIPT_EVAL.value:
            return evaluate_script_eval(rule, target_content, art, ctx)
        else:
            return AssertionResult(
                name=rule.name,
                type=rtype,
                passed=False,
                score=0.0,
                weight=rule.weight,
                evidence={"unknown_type": rtype},
                message=f"不支持的断言类型: {rtype}",
                error="unsupported_type",
            )

    def evaluate_suite(
        self,
        rules: list[AssertionRule | dict[str, Any]],
        output_text: str = "",
        artifacts: dict[str, Any] = None,
        context: dict[str, Any] = None,
    ) -> AssertionSuiteResult:
        """评估一组断言规则"""
        art = artifacts or {}
        ctx = context or {}
        parsed_rules: list[AssertionRule] = [
            r if isinstance(r, AssertionRule) else AssertionRule.from_dict(r)
            for r in rules
        ]

        results: list[AssertionResult] = []
        total_weight = 0.0
        weighted_score = 0.0
        all_required_passed = True

        for rule in parsed_rules:
            # 确定目标内容
            target_key = rule.target
            if target_key == "artifacts_content":
                target_content = extract_artifacts_text(art)
            elif target_key and target_key != "output":
                if target_key in art:
                    target_content = art[target_key]
                elif target_key == "stdout" and "stdout" in ctx:
                    target_content = ctx["stdout"]
                else:
                    # 尝试模糊匹配产物后缀或文件名
                    target_content = ""
                    for k, v in art.items():
                        if k.endswith(target_key) or target_key.endswith(k):
                            target_content = v
                            break
            else:
                # target == "output":
                # 语义定义为「产物内容与最终回答正文」，优先拼接 artifacts 里的文本内容，
                # 排除原始调度事件流（以 {"type":"step_start 等开头的行）
                clean_output = clean_event_stream_lines(output_text)
                artifacts_text = extract_artifacts_text(art)
                if artifacts_text and clean_output:
                    target_content = artifacts_text + chr(10)*2 + clean_output
                elif artifacts_text:
                    target_content = artifacts_text
                else:
                    target_content = clean_output

            res = self.evaluate_rule(rule, target_content, art, ctx)
            results.append(res)

            weight = max(0.1, rule.weight)
            total_weight += weight
            weighted_score += res.score * weight

            if rule.required and not res.passed:
                all_required_passed = False

        final_score = (weighted_score / total_weight * 100.0) if total_weight > 0 else 100.0
        passed_count = sum(1 for r in results if r.passed)
        failed_count = len(results) - passed_count
        overall_passed = all_required_passed and (failed_count == 0 or final_score >= 60.0)

        return AssertionSuiteResult(
            passed=overall_passed,
            score=final_score,
            total_assertions=len(results),
            passed_count=passed_count,
            failed_count=failed_count,
            results=results,
        )

    def create_snapshot(
        self,
        rules: list[AssertionRule | dict[str, Any]],
        records: list[dict[str, Any]],
        metadata: dict[str, Any] = None,
    ) -> dict[str, Any]:
        """生成规则版本快照和比对记录"""
        rule_dicts = [
            r.to_dict() if isinstance(r, AssertionRule) else AssertionRule.from_dict(r).to_dict()
            for r in rules
        ]
        return {
            "engine_version": self.version,
            "created_at": datetime.datetime.now().isoformat(),
            "metadata": metadata or {},
            "rules_count": len(rule_dicts),
            "rules_snapshot": rule_dicts,
            "records": records,
        }

    def verify_snapshot(self, snapshot_data: dict[str, Any]) -> dict[str, Any]:
        """快照比对复验逻辑：确保同输入同规则可逐条复现"""
        version = snapshot_data.get("engine_version", "unknown")
        rules_data = snapshot_data.get("rules_snapshot", [])
        records = snapshot_data.get("records", [])

        rules = [AssertionRule.from_dict(r) for r in rules_data]
        mismatches: list[dict[str, Any]] = []
        total_checks = 0
        reproduced_checks = 0

        for rec_idx, rec in enumerate(records):
            output_text = rec.get("output_text", "")
            artifacts = rec.get("artifacts", {})
            context = rec.get("context", {})
            recorded_results = rec.get("results", [])

            suite_res = self.evaluate_suite(rules, output_text, artifacts, context)
            total_checks += len(recorded_results)

            # 比对每项断言判定
            current_res_map = {r.name: r for r in suite_res.results}
            for exp_res in recorded_results:
                r_name = exp_res.get("name")
                curr = current_res_map.get(r_name)
                if not curr:
                    mismatches.append({
                        "record_index": rec_idx,
                        "rule_name": r_name,
                        "reason": "当前计算中缺少该规则结果",
                    })
                    continue

                passed_match = curr.passed == exp_res.get("passed")
                score_match = abs(curr.score - exp_res.get("score", 0.0)) < 1e-3

                if passed_match and score_match:
                    reproduced_checks += 1
                else:
                    mismatches.append({
                        "record_index": rec_idx,
                        "rule_name": r_name,
                        "recorded": {"passed": exp_res.get("passed"), "score": exp_res.get("score")},
                        "current": {"passed": curr.passed, "score": curr.score},
                    })

        all_matched = (len(mismatches) == 0) and (total_checks > 0)
        return {
            "verified": all_matched,
            "engine_version": version,
            "current_version": self.version,
            "total_checks": total_checks,
            "reproduced_checks": reproduced_checks,
            "match_rate": (reproduced_checks / total_checks) if total_checks > 0 else 1.0,
            "mismatches": mismatches,
        }


def save_assertions_snapshot(snapshot_data: dict[str, Any], path: Path | str) -> Path:
    """持久化断言快照文件"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snapshot_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_assertions_snapshot(path: Path | str) -> dict[str, Any]:
    """读取断言快照文件"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"断言快照文件不存在: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def verify_assertions_snapshot_file(path: Path | str) -> dict[str, Any]:
    """快捷复验快照文件"""
    engine = AssertionEngine()
    data = load_assertions_snapshot(path)
    return engine.verify_snapshot(data)
