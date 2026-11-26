"""plain_language.py - 把统计结论翻译成普通用户能看懂的白话。

规则写进代码（确定性映射，零 LLM）：
- verdict → 白话文案（skill_effective / negative_migration / inconclusive）
- 盲评票数 → 「X 位评委中有 Y 位认为带 skill 更好，理由：…」
- Δ / CI / 方差 / 断言 等术语禁止出现在白话输出中（见 JARGON_TERMS）。
"""
from __future__ import annotations

from typing import Any

# 首屏白话区禁止出现的统计术语（测试与自检共用）
JARGON_TERMS: tuple[str, ...] = (
    "Δ",
    "Δ̄",
    "σ",
    "σ²",
    "CI",
    "置信区间",
    "方差",
    "标准差",
    "有效样本量",
    "inconclusive",
    "断言",
)


def contains_untranslated_jargon(text: str) -> list[str]:
    """返回文本中出现的未翻译统计术语（用于自检/测试）。"""
    if not text:
        return []
    lowered = text.lower()
    return [term for term in JARGON_TERMS if term.lower() in lowered]


def verdict_to_plain_language(
    verdict: str,
    *,
    model: str = "",
    n: int = 0,
    mean_delta: float = 0.0,
    ci_low: float = 0.0,
    ci_high: float = 0.0,
    primary_metric: str = "assertion",
) -> str:
    """把配对增益 verdict 翻译成一句普通用户能看懂的白话。

    规则（写死在代码里，不依赖模型）：
    - skill_effective → 「带这个 skill 的产出明显更好，建议开启」
    - negative_migration → 「带这个 skill 反而更差，建议在这个模型上关闭」
    - inconclusive → 「两者差距不大/波动太大，这次没测出明显区别」+ 下一步建议
    输出中不允许出现 JARGON_TERMS 里的任何术语。
    """
    if verdict in ("skill_effective", "negative_migration"):
        text = ("带这个 skill 的产出明显更好，建议开启。" if verdict == "skill_effective"
                else "带这个 skill 反而更差，建议在这个模型上关闭。")
        if n > 0 and abs(mean_delta) > 0:
            direction = "多" if mean_delta > 0 else "少"
            unit = "票" if primary_metric == "blind_vote" else "分"
            text += f"同一个任务对比了 {n} 轮，开启后平均{direction}得 {abs(mean_delta):.1f} {unit}。"
        return text

    # inconclusive（含未知 verdict，一律按没测出区别处理）
    if n == 0:
        return "这次没有能配成对的结果，还看不出开启 Skill 有没有帮助。"
    if n < 5:
        return f"只对比了 {n} 轮，还看不出哪边更好。可以用同一个任务再跑几轮。"
    return f"同一个任务对比了 {n} 轮，仍没看出哪边稳定占优。可以对照两边的交付物，也可以换个更难的任务再测。"


def votes_to_plain_language(
    with_votes: int,
    without_votes: int,
    ties: int = 0,
    reasons: list[Any] | None = None,
) -> str:
    """把盲评投票结果翻译成白话：评委投票率 + 评委原话引用。"""
    total = max(int(with_votes or 0) + int(without_votes or 0) + int(ties or 0), 0)
    if total <= 0:
        return "评委这次没有留下有效投票。"

    parts: list[str] = [f"{total} 位评委中有 {int(with_votes or 0)} 位认为带 skill 更好"]
    if int(without_votes or 0) > 0:
        parts.append(f"{int(without_votes or 0)} 位认为不带更好")
    if int(ties or 0) > 0:
        parts.append(f"{int(ties or 0)} 位觉得差不多")
    text = "，".join(parts) + "。"

    quote = ""
    for reason in reasons or []:
        cleaned = " ".join(str(reason or "").split())
        if cleaned:
            quote = cleaned[:120] + ("…" if len(cleaned) > 120 else "")
            break
    if quote:
        text += f" 理由（评委原话）：「{quote}」"
    return text


def build_plain_summary(
    pair_gains: list[dict[str, Any]] | None,
    groups: list[dict[str, Any]] | None,
) -> str:
    """首屏白话总结：三句话以内说清这次评测结果（确定性拼接，零 LLM）。"""
    pair_gains = pair_gains or []
    groups = groups or []
    model_count = len(pair_gains) or len(groups)

    if model_count == 0:
        return "这次评测没有产出可对比的结果，请检查评测是否完整跑完。"

    total_with = sum(int(g.get("with_votes", 0) or 0) for g in groups)
    total_without = sum(int(g.get("without_votes", 0) or 0) for g in groups)

    sentence1 = f"这次用同一个任务，在 {model_count} 个模型上比较了开启和关闭 Skill 的结果。"

    effective = sum(1 for pg in pair_gains if pg.get("verdict") == "skill_effective")
    harmful = sum(1 for pg in pair_gains if pg.get("verdict") == "negative_migration")
    unclear = model_count - effective - harmful

    if effective and not harmful:
        sentence2 = f"其中 {effective} 个模型开启后表现更好。"
    elif harmful and not effective:
        sentence2 = f"有 {harmful} 个模型开启后表现更差，建议在这些模型上关闭。"
    elif effective and harmful:
        sentence2 = (
            f"开启后，{effective} 个模型表现更好，{harmful} 个反而变差。"
        )
    else:
        sentence2 = "现有结果还看不出哪个模型能从这个 Skill 中受益。"
    if unclear and (effective or harmful):
        sentence2 += f"还有 {unclear} 个模型看不出明显差别。"

    if total_with + total_without > 0:
        if total_with > total_without:
            sentence3 = f"评委给开启的一侧投了 {total_with} 票，关闭的一侧 {total_without} 票。"
        elif total_without > total_with:
            sentence3 = f"评委投票整体偏向不带 skill（{total_without} 比 {total_with}）。"
        else:
            sentence3 = f"评委投票两边打平（各 {total_with} 票）。"
    else:
        sentence3 = "这次没有有效的评委投票。"

    return " ".join(part for part in (sentence1, sentence2, sentence3) if part)
