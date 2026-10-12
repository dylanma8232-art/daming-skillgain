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
    "σ",
    "CI",
    "置信区间",
    "方差",
    "标准差",
    "inconclusive",
    "断言",
)


def contains_untranslated_jargon(text: str) -> list[str]:
    """返回文本中出现的未翻译统计术语（用于自检/测试）。"""
    if not text:
        return []
    lowered = text.lower()
    return [term for term in JARGON_TERMS if term.lower() in lowered]


def _metric_word(primary_metric: str) -> str:
    return "评委票数" if primary_metric == "blind_vote" else "任务得分"


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
    metric_word = _metric_word(primary_metric)

    if verdict == "skill_effective":
        text = "带这个 skill 的产出明显更好，建议开启。"
        if n > 0 and abs(mean_delta) > 0:
            text += f" {n} 轮同样的任务里，带 skill 平均{_metric_verb(primary_metric)}高 {abs(mean_delta):.1f}。"
        return text

    if verdict == "negative_migration":
        text = "带这个 skill 反而更差，建议在这个模型上关闭。"
        if n > 0 and abs(mean_delta) > 0:
            text += f" {n} 轮同样的任务里，带 skill 平均{_metric_verb(primary_metric)}低 {abs(mean_delta):.1f}。"
        return text

    # inconclusive（含未知 verdict，一律按没测出区别处理）
    text = "两者差距不大、或结果波动太大，这次没测出明显区别。"
    if n > 0 and n < 5:
        text += f"而且只对比了 {n} 轮，轮数偏少。"
    text += (
        "想进一步确认，可以：增加重复次数多跑几轮、换一个更难的任务再测，"
        "或者看看下面评委对两边产出的具体评价。"
    )
    return text


def _metric_verb(primary_metric: str) -> str:
    return "多拿" if primary_metric == "blind_vote" else "多得"


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

    sentence1 = (
        f"这次评测让 {model_count} 个模型分别「带 skill」和「不带 skill」各自完成同一个任务，"
        "再由评委打分。"
    )

    effective = sum(1 for pg in pair_gains if pg.get("verdict") == "skill_effective")
    harmful = sum(1 for pg in pair_gains if pg.get("verdict") == "negative_migration")
    unclear = model_count - effective - harmful

    if effective and not harmful:
        sentence2 = (
            f"结论：{effective} 个模型带 skill 的产出明显更好，可以放心开启；"
            + (f"另外 {unclear} 个模型没测出明显区别。" if unclear else "")
        ).rstrip()
    elif harmful and not effective:
        sentence2 = (
            f"结论：{harmful} 个模型带 skill 反而更差，建议在这些模型上关闭；"
            + (f"另外 {unclear} 个模型没测出明显区别。" if unclear else "")
        ).rstrip()
    elif effective and harmful:
        sentence2 = (
            f"结论：{effective} 个模型带 skill 明显更好，{harmful} 个模型反而更差，"
            "这个 skill 的效果因模型而异，按模型分别决定开不开。"
        )
    else:
        sentence2 = "结论：这次没测出明显区别——各模型两种模式的差距都很小，或波动太大，还不能下结论。"

    if total_with + total_without > 0:
        if total_with > total_without:
            sentence3 = f"评委投票整体也偏向带 skill（{total_with} 比 {total_without}）。"
        elif total_without > total_with:
            sentence3 = f"评委投票整体偏向不带 skill（{total_without} 比 {total_with}）。"
        else:
            sentence3 = f"评委投票两边打平（各 {total_with} 票）。"
    else:
        sentence3 = "评委这次没有留下有效投票，结论以自动任务得分为准。"

    return " ".join(part for part in (sentence1, sentence2, sentence3) if part)
