"""P1 同模型配对增益测评 - 统计、判定与人审触发（零 LLM、确定性实现）

核心口径（依据 2026-10-11 改造计划 §5/§7 P1 行）：
- 主指标：每个模型先比自己的 with/without（同任务/同输入/同条件/同重复轮次），
  产出配对差值 Δ = with − without；报告给出 Δ 均值 ± 方差/标准差 + 95% 置信区间（t 分布，小样本适用）。
- 跨模型结果降为第二层（仅汇总展示，不参与主判定）。
- 客观任务：断言得分 Δ 优先（复用 P0 四类断言引擎 score，0~100）；
  主观任务：盲评得票 Δ 辅助 + 评委一致率统计
  （口径：同一配对内评委同判定比例 = 评委间同判定对数 / 总可比对数，粒度 = 每对任务 × 每轮投票 + 整体均值）。
- 负迁移检出：配对 Δ < 0 且证据充分（95% CI 整体低于 0 且重复轮次达标）
  → 显著标注「该模型上建议关闭 skill」；证据不足一律标 inconclusive，不强判。
- 人审按需触发（无法判定 / 分歧大 / 高风险），触发记录落盘。
- 判定器版本固定（P1_JUDGMENT_VERSION）+ 判定快照，支持同输入同判定复验。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

P1_JUDGMENT_VERSION = "1.0.0"

# 默认每组 (skill, model, mode) 重复次数
DEFAULT_REPETITIONS = 3
# 出具确定性结论所需的最小有效配对重复数
MIN_REPS_FOR_VERDICT = 3
# 评委一致率「分歧大」阈值（低于该值触发人审）
JUDGE_AGREEMENT_THRESHOLD = 0.6

# 双侧 95% 置信 t 临界值表 (df=1..30)，超出用正态近似 1.96
_T_CRITICAL_95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def t_critical_95(df: int) -> float:
    """双侧 95% t 临界值；df<=0 返回 0；df>30 用正态近似。"""
    if df <= 0:
        return 0.0
    if df > 30:
        return 1.96
    return _T_CRITICAL_95[df]


@dataclass
class ReplicateObservation:
    """单次重复 (model, use_skill, repetition) 的观测量"""
    model: str
    use_skill: bool
    repetition: int
    workspace_dir: str
    # 客观断言得分 (0~100)，无断言或 invalid 时为 None
    assertion_score: float | None = None
    assertion_passed: bool | None = None
    # 该工作区在盲评中获得的评委票数
    blind_votes: int = 0
    is_valid: bool = True
    status: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "use_skill": self.use_skill,
            "repetition": self.repetition,
            "workspace_dir": self.workspace_dir,
            "assertion_score": self.assertion_score,
            "assertion_passed": self.assertion_passed,
            "blind_votes": self.blind_votes,
            "is_valid": self.is_valid,
            "status": self.status,
        }


@dataclass
class PairDelta:
    """一次配对差值（同一模型同一重复轮次的 with − without）"""
    repetition: int
    metric: str  # assertion | blind_vote
    delta: float
    with_value: float
    without_value: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "repetition": self.repetition,
            "metric": self.metric,
            "delta": round(self.delta, 4),
            "with_value": round(self.with_value, 4),
            "without_value": round(self.without_value, 4),
        }


@dataclass
class PairGainResult:
    """单个模型的同模型配对增益判定结果"""
    model: str
    primary_metric: str  # assertion | blind_vote | none
    n_paired: int  # 有效配对重复数
    deltas: list[PairDelta] = field(default_factory=list)
    mean_delta: float = 0.0
    variance: float = 0.0
    std_dev: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    verdict: str = "inconclusive"  # skill_effective | negative_migration | inconclusive
    verdict_label: str = "无法判定 (inconclusive)"
    negative_migration: bool = False
    evidence_sufficient: bool = False
    reasons: list[str] = field(default_factory=list)
    small_sample_warning: str = ""
    inconclusive_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "primary_metric": self.primary_metric,
            "n_paired": self.n_paired,
            "deltas": [d.to_dict() for d in self.deltas],
            "mean_delta": round(self.mean_delta, 4),
            "variance": round(self.variance, 4),
            "std_dev": round(self.std_dev, 4),
            "ci_low": round(self.ci_low, 4),
            "ci_high": round(self.ci_high, 4),
            "confidence_level": 0.95,
            "verdict": self.verdict,
            "verdict_label": self.verdict_label,
            "negative_migration": self.negative_migration,
            "evidence_sufficient": self.evidence_sufficient,
            "reasons": list(self.reasons),
            "small_sample_warning": self.small_sample_warning,
            "inconclusive_reasons": list(self.inconclusive_reasons),
        }


def collect_paired_replicates(
    manifest: dict[str, Any],
    vote_details: list[dict[str, Any]] | None = None,
) -> list[ReplicateObservation]:
    """从 run_manifest.json 的 results 中提取每次重复的观测量。

    - 断言得分取 result.analysis.assertions.score（P0 四类断言引擎产出，0~100）；
    - 盲评票数通过 workspace 路径匹配 evaluations.json 中的 workspace_votes 统计；
    - invalid（基础设施失败）不计入观测量（is_valid=False，成功/失败分母纪律同 P0）。
    """
    vote_details = vote_details or []
    # workspace path -> 总得票
    workspace_vote_counts: dict[str, int] = {}
    for vd in vote_details:
        if not isinstance(vd, dict):
            continue
        for ev in vd.get("evaluations", []) or []:
            if not isinstance(ev, dict):
                continue
            wv = ev.get("workspace_votes", {}) or {}
            if not isinstance(wv, dict):
                continue
            for workspace in wv.values():
                ws = str(workspace or "").strip()
                if ws:
                    workspace_vote_counts[ws] = workspace_vote_counts.get(ws, 0) + 1

    observations: list[ReplicateObservation] = []
    for item in manifest.get("results", []):
        if not isinstance(item, dict):
            continue
        model = str(item.get("model") or "").strip()
        if not model:
            continue
        is_valid = bool(item.get("is_valid", True)) and item.get("status") != "invalid"
        workspace_dir = str(item.get("workspace_dir") or "")
        assertion_score = None
        assertion_passed = None
        analysis = item.get("analysis") or {}
        assertions = analysis.get("assertions") if isinstance(analysis, dict) else None
        if isinstance(assertions, dict):
            try:
                assertion_score = float(assertions.get("score"))
            except (TypeError, ValueError):
                assertion_score = None
            assertion_passed = bool(assertions.get("passed"))

        # 纪律 1：空产物/无断言数据的失败运行计 0 分；invalid 无得分。
        # 若断言引擎已产出得分（含 assertion_failure 的失败运行），保留真实得分。
        if not is_valid:
            assertion_score = None
            assertion_passed = None
        elif assertions is None or not isinstance(assertions, dict):
            if not item.get("success"):
                assertion_score = 0.0
                assertion_passed = False

        try:
            repetition = int(item.get("repetition", 1) or 1)
        except (TypeError, ValueError):
            repetition = 1

        observations.append(ReplicateObservation(
            model=model,
            use_skill=bool(item.get("use_skill")),
            repetition=repetition,
            workspace_dir=workspace_dir,
            assertion_score=assertion_score,
            assertion_passed=assertion_passed,
            blind_votes=workspace_vote_counts.get(workspace_dir, 0),
            is_valid=is_valid,
            status=str(item.get("status") or "unknown"),
        ))
    return observations


def _stats(deltas: list[float]) -> tuple[float, float, float]:
    """返回 (mean, variance[样本方差 n-1], std_dev)"""
    n = len(deltas)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean = sum(deltas) / n
    variance = (sum((d - mean) ** 2 for d in deltas) / (n - 1)) if n > 1 else 0.0
    return mean, variance, math.sqrt(max(variance, 0.0))


def decide_verdict(
    mean_delta: float,
    ci_low: float,
    ci_high: float,
    n_paired: int,
    min_reps: int = MIN_REPS_FOR_VERDICT,
) -> tuple[str, str, bool, bool, list[str]]:
    """确定性判定。返回 (verdict, verdict_label, negative_migration, evidence_sufficient, reasons)。

    规则：
    - 证据充分 = n_paired >= min_reps 且 95% 置信区间不跨越 0；
    - mean > 0 且 ci_low > 0 且证据充分 → skill_effective（明确有效）；
    - mean < 0 且 ci_high < 0 且证据充分 → negative_migration（检出负迁移，建议关闭 skill）；
    - 其余一律 inconclusive，不强判。
    """
    reasons: list[str] = []
    evidence_sufficient = n_paired >= min_reps
    if not evidence_sufficient:
        reasons.append(f"有效配对重复数 N={n_paired} < {min_reps}，证据不足，按规范不强判")

    ci_crosses_zero = (ci_low <= 0.0 <= ci_high)
    if ci_crosses_zero:
        reasons.append(f"95% 置信区间 [{ci_low:.2f}, {ci_high:.2f}] 跨越 0，差异不显著")

    if evidence_sufficient and not ci_crosses_zero:
        if mean_delta > 0 and ci_low > 0:
            return ("skill_effective", "明确有效 (skill_effective)", False, True,
                    [f"配对 Δ 均值 {mean_delta:.2f} > 0，且 95% CI 下界 {ci_low:.2f} > 0"])
        if mean_delta < 0 and ci_high < 0:
            return ("negative_migration", "明确有害 (negative_migration)", True, True,
                    [f"配对 Δ 均值 {mean_delta:.2f} < 0，且 95% CI 上界 {ci_high:.2f} < 0，检出负迁移"])

    return ("inconclusive", "无法判定 (inconclusive)", False, evidence_sufficient, reasons)


def compute_pair_gain(
    model: str,
    with_obs: list[ReplicateObservation],
    without_obs: list[ReplicateObservation],
    has_assertions: bool,
    min_reps: int = MIN_REPS_FOR_VERDICT,
) -> PairGainResult:
    """计算单个模型的同模型配对增益。

    配对方式：按重复轮次一一配对（同任务/同输入/同条件，仅重复序号不同）。
    主指标选择：有断言观测量时用断言得分 Δ（客观优先）；否则用盲评得票 Δ。
    """
    by_rep_with = {o.repetition: o for o in with_obs if o.is_valid and o.assertion_score is not None}
    by_rep_without = {o.repetition: o for o in without_obs if o.is_valid and o.assertion_score is not None}
    shared_reps = sorted(set(by_rep_with) & set(by_rep_without))

    assertion_deltas = [
        PairDelta(
            repetition=rep,
            metric="assertion",
            delta=by_rep_with[rep].assertion_score - by_rep_without[rep].assertion_score,
            with_value=by_rep_with[rep].assertion_score,
            without_value=by_rep_without[rep].assertion_score,
        )
        for rep in shared_reps
    ]

    votes_with = {o.repetition: o.blind_votes for o in with_obs if o.is_valid}
    votes_without = {o.repetition: o.blind_votes for o in without_obs if o.is_valid}
    shared_vote_reps = sorted(set(votes_with) & set(votes_without))
    vote_deltas = [
        PairDelta(
            repetition=rep,
            metric="blind_vote",
            delta=votes_with[rep] - votes_without[rep],
            with_value=float(votes_with[rep]),
            without_value=float(votes_without[rep]),
        )
        for rep in shared_vote_reps
    ]

    if has_assertions and assertion_deltas:
        primary_metric, deltas = "assertion", assertion_deltas
    elif vote_deltas:
        primary_metric, deltas = "blind_vote", vote_deltas
    elif assertion_deltas:
        primary_metric, deltas = "assertion", assertion_deltas
    else:
        primary_metric, deltas = "none", []

    delta_values = [d.delta for d in deltas]
    n_paired = len(delta_values)
    mean, variance, std = _stats(delta_values)
    if n_paired > 1 and std > 0:
        half = t_critical_95(n_paired - 1) * std / math.sqrt(n_paired)
        ci_low, ci_high = mean - half, mean + half
    elif n_paired >= 1:
        # 单重复或零方差：区间退化为点估计
        ci_low, ci_high = mean, mean
    else:
        ci_low, ci_high = 0.0, 0.0

    verdict, label, neg, suff, reasons = decide_verdict(mean, ci_low, ci_high, n_paired, min_reps)

    inconclusive_reasons: list[str] = []
    if verdict == "inconclusive":
        if n_paired == 0:
            inconclusive_reasons.append("无可配对的重复观测（with/without 重复轮次无交集或均无效）")
        if primary_metric == "none":
            inconclusive_reasons.append("既无断言得分也无盲评得票，无任何可观测量")
        inconclusive_reasons.extend(reasons)

    small_sample_warning = ""
    if 0 < n_paired < 5:
        small_sample_warning = f"⚠️ 样本量不足（{model} 有效配对样本量 N={n_paired} < 5），统计指标仅供参考，可能存在较高偶然性。"

    return PairGainResult(
        model=model,
        primary_metric=primary_metric,
        n_paired=n_paired,
        deltas=deltas,
        mean_delta=mean,
        variance=variance,
        std_dev=std,
        ci_low=ci_low,
        ci_high=ci_high,
        verdict=verdict,
        verdict_label=label,
        negative_migration=neg,
        evidence_sufficient=suff,
        reasons=reasons,
        small_sample_warning=small_sample_warning,
        inconclusive_reasons=inconclusive_reasons,
    )


# ---------------------------------------------------------------------------
# 评委一致率（口径：同一配对内评委同判定比例）
# ---------------------------------------------------------------------------

def _judge_pair_preference(ev: dict[str, Any], pair_workspaces: dict[str, str]) -> str:
    """判定单个评委在某一轮投票中，对某个配对 (with/without workspace) 的偏好。

    pair_workspaces: {"with": workspace_path, "without": workspace_path}
    返回 "with" | "without" | "abstain"（未投票/两侧同票/平票）
    """
    wv = ev.get("workspace_votes", {}) or {}
    if not isinstance(wv, dict):
        return "abstain"
    ws_to_side = {str(ws): side for side, ws in pair_workspaces.items() if ws}
    with_votes = without_votes = 0
    for _label, workspace in wv.items():
        side = ws_to_side.get(str(workspace or ""))
        if side == "with":
            with_votes += 1
        elif side == "without":
            without_votes += 1
    if with_votes > without_votes:
        return "with"
    if without_votes > with_votes:
        return "without"
    return "abstain"


def compute_judge_agreement(
    vote_details: list[dict[str, Any]],
    pair_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """评委一致率统计。

    口径：同一配对内，多个评委对该配对（with vs without）给出相同偏好判定的比例，
    即 评委间同判定对数 / 总可比对数。报告粒度 = 每对任务 × 每轮投票 + 整体均值。

    vote_details: evaluations.json 反序列化结果（每个元素含 voting_model + evaluations[]）
    pair_list: [{"model": str, "repetition": int, "with": ws, "without": ws}, ...]
    """
    per_round: list[dict[str, Any]] = []
    total_comparable = 0
    total_agree = 0

    pair_map: dict[tuple[str, int], dict[str, str]] = {}
    for p in pair_list:
        pair_map[(str(p["model"]), int(p.get("repetition", 1)))] = {
            "with": str(p.get("with") or ""),
            "without": str(p.get("without") or ""),
        }

    for vd in vote_details:
        if not isinstance(vd, dict):
            continue
        voting_model = str(vd.get("voting_model") or "unknown")
        for round_idx, ev in enumerate(vd.get("evaluations", []) or []):
            if not isinstance(ev, dict):
                continue
            for (model, rep), pair_ws in pair_map.items():
                pref = _judge_pair_preference(ev, pair_ws)
                if pref == "abstain":
                    continue
                per_round.append({
                    "model": model,
                    "repetition": rep,
                    "round": round_idx,
                    "voting_model": voting_model,
                    "preference": pref,
                })

    # 按 (model, repetition, round) 分组，计算组内评委两两同判定比例
    groups: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for entry in per_round:
        groups.setdefault((entry["model"], entry["repetition"], entry["round"]), []).append(entry)

    per_round_summary: list[dict[str, Any]] = []
    for (model, rep, round_idx), judges in sorted(groups.items()):
        comparable = 0
        agree = 0
        for i in range(len(judges)):
            for j in range(i + 1, len(judges)):
                comparable += 1
                if judges[i]["preference"] == judges[j]["preference"]:
                    agree += 1
        rate = (agree / comparable) if comparable > 0 else None
        total_comparable += comparable
        total_agree += agree
        per_round_summary.append({
            "model": model,
            "repetition": rep,
            "round": round_idx,
            "judge_count": len(judges),
            "comparable_pairs": comparable,
            "agree_pairs": agree,
            "agreement_rate": round(rate, 4) if rate is not None else None,
        })

    overall_rate = (total_agree / total_comparable) if total_comparable > 0 else None
    return {
        "definition": "同一配对内评委同判定比例 = 评委间同判定对数 / 总可比对数（粒度：每对任务 × 每轮投票，另汇总整体均值）",
        "overall_agreement_rate": round(overall_rate, 4) if overall_rate is not None else None,
        "overall_comparable_pairs": total_comparable,
        "overall_agree_pairs": total_agree,
        "per_round": per_round_summary,
        "threshold": JUDGE_AGREEMENT_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# 人审按需触发
# ---------------------------------------------------------------------------

def evaluate_human_review_triggers(
    pair_gains: list[PairGainResult],
    judge_agreement: dict[str, Any],
) -> list[dict[str, Any]]:
    """主观任务人审按需触发。触发条件（满足其一即触发并留记录）：
    1. 无法判定：配对结论为 inconclusive；
    2. 分歧大：评委一致率低于阈值（默认 0.6）；
    3. 高风险：检出负迁移（结论直接影响「是否关闭 skill」的决策）。
    """
    triggers: list[dict[str, Any]] = []
    overall_rate = judge_agreement.get("overall_agreement_rate")
    threshold = judge_agreement.get("threshold", JUDGE_AGREEMENT_THRESHOLD)

    for pg in pair_gains:
        model_triggers: list[dict[str, Any]] = []
        if pg.verdict == "inconclusive":
            model_triggers.append({
                "type": "undecidable",
                "reason": "无法判定：配对结论为 inconclusive",
                "detail": "；".join(pg.inconclusive_reasons) or "统计证据不足以支持确定性结论",
            })
        if pg.negative_migration:
            model_triggers.append({
                "type": "high_risk",
                "reason": "高风险：检出负迁移，结论将触发「该模型上建议关闭 skill」决策",
                "detail": f"配对 Δ 均值 {pg.mean_delta:.2f}，95% CI [{pg.ci_low:.2f}, {pg.ci_high:.2f}]",
            })
        if overall_rate is not None and overall_rate < threshold:
            model_triggers.append({
                "type": "judge_disagreement",
                "reason": f"分歧大：评委整体一致率 {overall_rate:.2%} 低于阈值 {threshold:.0%}",
                "detail": "建议引入人工评审复核盲评偏好",
            })
        if model_triggers:
            triggers.append({
                "model": pg.model,
                "verdict": pg.verdict,
                "triggered": True,
                "triggers": model_triggers,
            })
    return triggers


# ---------------------------------------------------------------------------
# 汇总入口：构建 P1 payload 与判定快照
# ---------------------------------------------------------------------------

def _group_observations(
    observations: list[ReplicateObservation],
) -> dict[str, dict[str, list[ReplicateObservation]]]:
    grouped: dict[str, dict[str, list[ReplicateObservation]]] = {}
    for obs in observations:
        side = "with" if obs.use_skill else "without"
        grouped.setdefault(obs.model, {"with": [], "without": []})[side].append(obs)
    return grouped


def build_p1_payload(
    manifest: dict[str, Any],
    vote_details: list[dict[str, Any]] | None = None,
    has_assertions_config: bool = False,
    min_reps: int = MIN_REPS_FOR_VERDICT,
) -> dict[str, Any]:
    """构建 P1 配对增益 payload（确定性、零 LLM）。

    返回：
    - pair_gains: 每个模型的 PairGainResult（主指标 Skill Gain Δ + 均值±方差 + CI）
    - overall: 跨模型汇总（第二层，仅参考）
    - judge_agreement: 评委一致率
    - human_review_triggers: 人审触发记录
    - judgment_snapshot: 判定快照（同输入同判定复验依据）
    """
    observations = collect_paired_replicates(manifest, vote_details)
    grouped = _group_observations(observations)

    has_assertions = has_assertions_config or any(o.assertion_score is not None for o in observations)

    pair_gains = [
        compute_pair_gain(
            model,
            sides["with"],
            sides["without"],
            has_assertions=has_assertions,
            min_reps=min_reps,
        )
        for model, sides in sorted(grouped.items())
    ]

    # 配对工作区映射（供评委一致率统计）
    pair_list: list[dict[str, Any]] = []
    for model, sides in grouped.items():
        by_rep_with = sorted(sides["with"], key=lambda o: o.repetition)
        by_rep_without = sorted(sides["without"], key=lambda o: o.repetition)
        reps = sorted({o.repetition for o in by_rep_with} | {o.repetition for o in by_rep_without})
        for rep in reps:
            with_ws = next((o.workspace_dir for o in by_rep_with if o.repetition == rep), "")
            without_ws = next((o.workspace_dir for o in by_rep_without if o.repetition == rep), "")
            if with_ws or without_ws:
                pair_list.append({
                    "model": model, "repetition": rep,
                    "with": with_ws, "without": without_ws,
                })
    judge_agreement = compute_judge_agreement(vote_details or [], pair_list)
    human_review_triggers = evaluate_human_review_triggers(pair_gains, judge_agreement)

    effective = [pg for pg in pair_gains if pg.verdict == "skill_effective"]
    negative = [pg for pg in pair_gains if pg.verdict == "negative_migration"]
    inconclusive = [pg for pg in pair_gains if pg.verdict == "inconclusive"]
    all_means = [pg.mean_delta for pg in pair_gains if pg.n_paired > 0]
    overall_mean, overall_var, overall_std = _stats(all_means)

    payload: dict[str, Any] = {
        "version": P1_JUDGMENT_VERSION,
        "primary_metric": "assertion" if has_assertions else "blind_vote",
        "pair_gains": [pg.to_dict() for pg in pair_gains],
        "overall": {
            "model_count": len(pair_gains),
            "skill_effective_count": len(effective),
            "negative_migration_count": len(negative),
            "inconclusive_count": len(inconclusive),
            "mean_of_mean_deltas": round(overall_mean, 4),
            "variance_of_mean_deltas": round(overall_var, 4),
            "std_dev_of_mean_deltas": round(overall_std, 4),
            "note": "跨模型汇总为第二层参考指标，主结论以每个模型的同模型配对增益为准",
        },
        "judge_agreement": judge_agreement,
        "human_review_triggers": human_review_triggers,
    }

    # 判定快照：记录全部输入与输出，供复验时同输入同判定比对
    payload["judgment_snapshot"] = {
        "version": P1_JUDGMENT_VERSION,
        "inputs": {
            "observations": [o.to_dict() for o in observations],
            "min_reps": min_reps,
            "has_assertions": has_assertions,
        },
        "outputs": {
            "pair_gain_verdicts": {pg.model: pg.verdict for pg in pair_gains},
            "pair_gain_stats": {
                pg.model: {
                    "mean_delta": round(pg.mean_delta, 4),
                    "variance": round(pg.variance, 4),
                    "ci_low": round(pg.ci_low, 4),
                    "ci_high": round(pg.ci_high, 4),
                    "n_paired": pg.n_paired,
                }
                for pg in pair_gains
            },
            "overall_agreement_rate": judge_agreement.get("overall_agreement_rate"),
        },
    }
    return payload


def verify_p1_judgment_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """复验 P1 判定快照：用快照中的输入重跑判定，逐项比对输出是否一致（同输入同判定）。"""
    version = snapshot.get("version", "unknown")
    inputs = snapshot.get("inputs", {})
    outputs = snapshot.get("outputs", {})

    observations = [
        ReplicateObservation(
            model=o["model"],
            use_skill=o["use_skill"],
            repetition=o.get("repetition", 1),
            workspace_dir=o.get("workspace_dir", ""),
            assertion_score=o.get("assertion_score"),
            assertion_passed=o.get("assertion_passed"),
            blind_votes=o.get("blind_votes", 0),
            is_valid=o.get("is_valid", True),
            status=o.get("status", "unknown"),
        )
        for o in inputs.get("observations", [])
    ]
    min_reps = int(inputs.get("min_reps", MIN_REPS_FOR_VERDICT))
    has_assertions = bool(inputs.get("has_assertions", False))

    grouped = _group_observations(observations)

    recomputed: dict[str, str] = {}
    stats: dict[str, dict[str, Any]] = {}
    for model, sides in sorted(grouped.items()):
        pg = compute_pair_gain(model, sides["with"], sides["without"],
                               has_assertions=has_assertions, min_reps=min_reps)
        recomputed[model] = pg.verdict
        stats[model] = {
            "mean_delta": round(pg.mean_delta, 4),
            "variance": round(pg.variance, 4),
            "ci_low": round(pg.ci_low, 4),
            "ci_high": round(pg.ci_high, 4),
            "n_paired": pg.n_paired,
        }

    expected_verdicts = outputs.get("pair_gain_verdicts", {})
    expected_stats = outputs.get("pair_gain_stats", {})
    mismatches: list[dict[str, Any]] = []
    for model in sorted(set(expected_verdicts) | set(recomputed)):
        if expected_verdicts.get(model) != recomputed.get(model):
            mismatches.append({
                "model": model,
                "field": "verdict",
                "recorded": expected_verdicts.get(model),
                "current": recomputed.get(model),
            })
    for model in sorted(set(expected_stats) | set(stats)):
        for key in ("mean_delta", "variance", "ci_low", "ci_high", "n_paired"):
            recorded = expected_stats.get(model, {}).get(key)
            current = stats.get(model, {}).get(key)
            if recorded is None or current is None:
                if recorded != current:
                    mismatches.append({"model": model, "field": key, "recorded": recorded, "current": current})
            elif isinstance(recorded, (int, float)) and isinstance(current, (int, float)):
                if abs(float(recorded) - float(current)) > 1e-3:
                    mismatches.append({"model": model, "field": key, "recorded": recorded, "current": current})
            elif recorded != current:
                mismatches.append({"model": model, "field": key, "recorded": recorded, "current": current})

    total_checks = len(expected_verdicts) + sum(len(v) for v in expected_stats.values())
    reproduced = total_checks - len(mismatches)
    return {
        "verified": (len(mismatches) == 0) and total_checks > 0,
        "version": version,
        "current_version": P1_JUDGMENT_VERSION,
        "total_checks": total_checks,
        "reproduced_checks": reproduced,
        "mismatches": mismatches,
    }


def save_p1_outputs(run_dir: Path, payload: dict[str, Any]) -> dict[str, str]:
    """将 P1 payload、判定快照与人审触发记录落盘到 run_dir/p1/ 目录。"""
    p1_dir = Path(run_dir) / "p1"
    p1_dir.mkdir(parents=True, exist_ok=True)
    (p1_dir / "paired_gain.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (p1_dir / "judgment_snapshot.json").write_text(
        json.dumps(payload.get("judgment_snapshot", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    (p1_dir / "human_review_triggers.json").write_text(
        json.dumps(payload.get("human_review_triggers", []), ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "paired_gain": str(p1_dir / "paired_gain.json"),
        "judgment_snapshot": str(p1_dir / "judgment_snapshot.json"),
        "human_review_triggers": str(p1_dir / "human_review_triggers.json"),
    }


def rebuild_p1_outputs(run_dir: Path) -> dict[str, Any] | None:
    """从 run 目录重建 P1 payload 并落盘；缺 run_manifest.json 时返回 None。

    在 run_from_plan（量化路径）与 run_blind_vote_phase（盲评完成）后被调用，
    保证无论是否含盲评，p1/ 目录始终反映最新观测。
    """
    run_dir = Path(run_dir)
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    vote_details: list[dict[str, Any]] = []
    evals_path = run_dir / "blind_vote" / "evaluations.json"
    if evals_path.exists():
        try:
            raw = json.loads(evals_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                vote_details = raw
        except Exception:
            pass

    has_assertions_config = False
    contract_path = run_dir / "evaluation_contract.json"
    if contract_path.exists():
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            has_assertions_config = bool(contract.get("validation_assertions"))
        except Exception:
            pass

    payload = build_p1_payload(manifest, vote_details, has_assertions_config=has_assertions_config)
    save_p1_outputs(run_dir, payload)
    return payload
