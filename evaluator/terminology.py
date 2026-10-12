"""terminology.py - 全产品统一术语映射（原词 → 白话）。

reporting 与 cli 共用本模块，保证两处话术一致。
`to_plain_language()` 用于把用户可见文案里的术语替换成白话；
`JARGON_TERMS`（沿用 plain_language）用于首屏自检。
"""
from __future__ import annotations

import re

# 原词 → 白话。键按长度降序匹配（先长词后短词，避免子串误替换）。
TERMINOLOGY_MAP: dict[str, str] = {
    "盲评": "评委打分",
    "双盲评审": "评委打分",
    "断言得分": "自动检查得分",
    "断言": "自动检查",
    "preflight": "跑前体检",
    "预检": "跑前体检",
    "synthetic": "生成的测试数据",
    "mock": "生成的测试数据",
    "合成测试数据": "生成的测试数据",
    "宿主适用边界": "适用范围（Agent 工具）",
    "已测宿主": "已测的 Agent 工具",
    "跨宿主外推": "推广到其他 Agent 工具",
    "宿主": "Agent 工具",
    "加载证据": "确认 skill 已装上",
    "置信区间": "结果波动范围",
    "标准差": "结果波动大小",
    "方差": "结果波动大小",
    "inconclusive": "没法下结论",
    "负迁移": "带 skill 反而更差",
}

# 按键长度降序排列，替换时先长后短
_SORTED_TERMS: list[tuple[str, str]] = sorted(
    TERMINOLOGY_MAP.items(), key=lambda kv: len(kv[0]), reverse=True
)


def to_plain_language(text: str) -> str:
    """把一段用户可见文案里的术语替换成白话。

    大小写不敏感（preflight / Synthetic 等），保留原文其余部分。
    """
    if not text:
        return text
    result = text
    for term, plain in _SORTED_TERMS:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        # 避免重复替换到已生成的白话（如「Agent 工具」不再被再次命中）
        result = pattern.sub(plain, result)
    return result


def untranslated_terms(text: str) -> list[str]:
    """返回文本中仍然残留的术语（用于测试与自检）。"""
    if not text:
        return []
    lowered = text.lower()
    return [term for term in TERMINOLOGY_MAP if term.lower() in lowered]
