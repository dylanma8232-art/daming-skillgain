"""断言模型定义 - 零 LLM、确定性任务结果断言"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

ASSERTION_ENGINE_VERSION = "1.1.0"


class AssertionType(str, Enum):
    EXACT_MATCH = "exact_match"
    CONTAINS_FACTS = "contains_facts"
    RECOMPUTABLE = "recomputable"
    SCRIPT_EVAL = "script_eval"


@dataclass
class AssertionRule:
    """断言规则配置"""
    name: str
    type: str  # exact_match | contains_facts | recomputable | script_eval
    description: str = ""
    weight: float = 1.0
    required: bool = True
    target: str = "output"  # 目标: output | stdout | 相对文件路径 如 summary.md
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "weight": self.weight,
            "required": self.required,
            "target": self.target,
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AssertionRule":
        rule_type = data.get("type") or data.get("kind") or AssertionType.EXACT_MATCH.value
        # 兼容已有 kind 命名
        if rule_type in ("artifact_path_exists", "artifact_extensions", "completion_signal"):
            rule_type = AssertionType.SCRIPT_EVAL.value
        elif rule_type in ("content_keywords", "keywords"):
            rule_type = AssertionType.CONTAINS_FACTS.value

        params = dict(data.get("parameters") or {})
        for k in ("expected", "target_type", "tolerance", "facts", "keywords", "regexes",
                  "forbidden", "operator", "operands", "claimed_result", "script_name",
                  "script_path", "function_name", "extensions", "path"):
            if k in data and k not in params:
                params[k] = data[k]

        return cls(
            name=str(data.get("name") or "unnamed_assertion"),
            type=str(rule_type),
            description=str(data.get("description") or ""),
            weight=float(data.get("weight", 1.0)),
            required=bool(data.get("required", True)),
            target=str(data.get("target") or data.get("path") or "output"),
            parameters=params,
        )


@dataclass
class AssertionResult:
    """单条断言执行结果"""
    name: str
    type: str
    passed: bool
    score: float  # 0.0 ~ 1.0
    weight: float
    evidence: dict[str, Any]
    message: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "passed": self.passed,
            "score": round(self.score, 4),
            "weight": self.weight,
            "evidence": self.evidence,
            "message": self.message,
            "error": self.error,
        }


@dataclass
class AssertionSuiteResult:
    """整套断言套件执行结果"""
    passed: bool
    score: float  # 0.0 ~ 100.0
    total_assertions: int
    passed_count: int
    failed_count: int
    results: list[AssertionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 2),
            "total_assertions": self.total_assertions,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "results": [r.to_dict() for r in self.results],
        }
