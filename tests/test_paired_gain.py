"""同模型配对增益测评测试

覆盖验收标准：
1. 三种结论（明确有效 / 明确有害 / inconclusive）实测且互不混淆（夹具构造，断言判定可证明）
2. 配对报告含 Δ + 分布 + 方差/置信区间 + 样本量
3. 客观断言判定同输入同判定（快照比对通过）
4. 主观任务报告含评委一致率统计
5. inconclusive 场景实测不被判胜
6. 重复运行配置（默认 3 次）
7. 负迁移检出与「建议关闭 skill」标注
8. 人审按需触发（无法判定/分歧大/高风险）留记录
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from evaluator.models import EvaluationConfig, RunnerConfig
from evaluator.engine import EvaluationEngine
from evaluator.assertions import (
    AssertionEngine,
    AssertionRule,
    save_assertions_snapshot,
    verify_assertions_snapshot_file,
)
from evaluator.paired_gain import (
    MIN_REPS_FOR_VERDICT,
    ReplicateObservation,
    build_p1_payload,
    compute_judge_agreement,
    compute_pair_gain,
    decide_verdict,
    evaluate_human_review_triggers,
    rebuild_p1_outputs,
    save_p1_outputs,
    verify_p1_judgment_snapshot,
)
from evaluator.reporting import build_report_payload, render_report_html


SKILL_DIR = Path(__file__).parent / "fixtures" / "skills"


# ---------------------------------------------------------------------------
# 夹具构造：两个样例 skill（断言可证明确实有效 / 确实有害）
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path, skill_name: str, repetitions: int) -> EvaluationConfig:
    skill_path = SKILL_DIR / skill_name
    return EvaluationConfig(
        evaluation_name=f"{skill_name}-paired",
        target_skill=skill_name,
        target_skill_path=str(skill_path),
        task_prompt="产出 artifacts/summary.md，包含 EVAL_PASS 标记行",
        workspace_root=str(tmp_path / "workspaces"),
        models=["test-model"],
        blind_review_models=["judge-a", "judge-b"],
        runners=[RunnerConfig(name="opencode", command_template="opencode run --model {model}")],
        use_skill_modes=[False, True],
        repetitions=repetitions,
    )


# 断言规则：summary.md 必须以 # Evaluation Summary 开头且含 EVAL_PASS: 行（effective skill 的行为契约）
GOOD_RULES = [
    {"name": "summary_header", "type": "contains_facts", "target": "summary.md",
     "parameters": {"keywords": ["# Evaluation Summary"]}},
    {"name": "eval_pass_marker", "type": "contains_facts", "target": "summary.md",
     "parameters": {"keywords": ["EVAL_PASS:"], "forbidden": ["EVAL_FAIL:", "# Broken Output"]}},
]


def _summary_text(effective: bool, model: str = "test-model") -> str:
    if effective:
        return f"# Evaluation Summary\n\nEVAL_PASS: {model}\n任务完成。\n"
    return f"# Broken Output\n\nEVAL_FAIL: {model}\n任务完成。\n"


def _build_run_dir(
    tmp_path: Path,
    skill_name: str,
    reps: int,
    with_effective: bool,
    without_effective: bool,
    with_scores: list[float] | None = None,
    without_scores: list[float] | None = None,
    run_name: str = "",
) -> tuple[Path, dict]:
    """构造一个模拟 run 目录：results 已带断言得分（0~100），manifest 就绪。

    with_effective / without_effective 控制对应 mode 的 summary.md 内容是否符合契约。
    with_scores / without_scores 直接给出断言得分序列（覆盖自动得分）。
    """
    run_dir = tmp_path / (run_name or f"run_{skill_name}")
    if run_dir.exists():
        import shutil
        shutil.rmtree(run_dir)
    results = []
    for mode_is_with, effective, scores in (
        (True, with_effective, with_scores),
        (False, without_effective, without_scores),
    ):
        for rep in range(1, reps + 1):
            ws = run_dir / "experiments" / (
                f"opencode__test_model__{'with-skill' if mode_is_with else 'without-skill'}__rep{rep}"
            )
            art = ws / "artifacts"
            art.mkdir(parents=True)
            (art / "summary.md").write_text(_summary_text(effective), encoding="utf-8")

            if scores is not None:
                score = scores[rep - 1]
                passed = score >= 60.0
            else:
                engine = AssertionEngine()
                suite = engine.evaluate_suite(
                    GOOD_RULES,
                    output_text="",
                    artifacts={"summary.md": _summary_text(effective)},
                )
                score = suite.score
                passed = suite.passed

            results.append({
                "model": "test-model",
                "use_skill": mode_is_with,
                "repetition": rep,
                "workspace_dir": str(ws),
                "is_valid": True,
                "status": "completed",
                "success": passed,
                "artifacts": ["artifacts/summary.md"],
                "analysis": {"assertions": {"passed": passed, "score": score, "results": []}},
            })

    manifest = {
        "evaluation_name": f"{skill_name}-paired",
        "target_skill": skill_name,
        "task_prompt": "产出 artifacts/summary.md",
        "repetitions": reps,
        "results": results,
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return run_dir, manifest


# ---------------------------------------------------------------------------
# 1. 三种结论（用断言判定可证明的夹具 skill）互不混淆
# ---------------------------------------------------------------------------

class TestThreeVerdicts:
    def test_effective_skill_yields_skill_effective(self, tmp_path):
        """有效 skill：with 侧每次满足断言契约（得分 ~100），without 侧每次违反（得分 ~0）"""
        run_dir, manifest = _build_run_dir(
            tmp_path, "effective-marker-skill", reps=3,
            with_effective=True, without_effective=False,
            with_scores=[100.0, 100.0, 100.0], without_scores=[0.0, 0.0, 0.0],
        )
        payload = build_p1_payload(manifest)
        pg = payload["pair_gains"][0]
        assert pg["verdict"] == "skill_effective"
        assert pg["verdict_label"].startswith("明确有效")
        assert pg["primary_metric"] == "assertion"
        assert pg["n_paired"] == 3
        assert pg["mean_delta"] == pytest.approx(100.0)
        assert pg["ci_low"] > 0
        assert not pg["negative_migration"]

    def test_harmful_skill_yields_negative_migration(self, tmp_path):
        """有害 skill：with 侧每次被断言判负（得分 ~0），without 侧每次满足契约（~100）"""
        run_dir, manifest = _build_run_dir(
            tmp_path, "harmful-marker-skill", reps=3,
            with_effective=False, without_effective=True,
            with_scores=[0.0, 0.0, 0.0], without_scores=[100.0, 100.0, 100.0],
        )
        payload = build_p1_payload(manifest)
        pg = payload["pair_gains"][0]
        assert pg["verdict"] == "negative_migration"
        assert pg["negative_migration"] is True
        assert pg["mean_delta"] == pytest.approx(-100.0)
        assert pg["ci_high"] < 0
        assert "建议" in pg["verdict_label"] or "有害" in pg["verdict_label"]

    def test_three_verdicts_do_not_confuse(self, tmp_path):
        """同一代码路径下，三种结论互不混淆：有效→effective，有害→negative，噪声→inconclusive"""
        _, m_eff = _build_run_dir(
            tmp_path, "effective-marker-skill", reps=3,
            with_effective=True, without_effective=False,
            with_scores=[100.0] * 3, without_scores=[0.0] * 3,
            run_name="run_eff",
        )
        v1 = build_p1_payload(m_eff)["pair_gains"][0]["verdict"]

        _, m_harm = _build_run_dir(
            tmp_path, "harmful-marker-skill", reps=3,
            with_effective=False, without_effective=True,
            with_scores=[0.0] * 3, without_scores=[100.0] * 3,
            run_name="run_harm",
        )
        v2 = build_p1_payload(m_harm)["pair_gains"][0]["verdict"]

        _, m_noise = _build_run_dir(
            tmp_path, "effective-marker-skill", reps=3,
            with_effective=True, without_effective=False,
            with_scores=[95.0, 10.0, 90.0], without_scores=[40.0, 85.0, 15.0],
            run_name="run_noise",
        )
        v3 = build_p1_payload(m_noise)["pair_gains"][0]["verdict"]

        assert v1 == "skill_effective"
        assert v2 == "negative_migration"
        assert v3 == "inconclusive"
        assert len({v1, v2, v3}) == 3


# ---------------------------------------------------------------------------
# 2. 配对报告含 Δ + 分布 + 方差/置信区间 + 样本量
# ---------------------------------------------------------------------------

class TestPairedStatsReporting:
    def test_report_contains_delta_distribution_variance_ci_n(self, tmp_path):
        run_dir, manifest = _build_run_dir(
            tmp_path, "effective-marker-skill", reps=3,
            with_effective=True, without_effective=False,
            with_scores=[100.0, 90.0, 95.0], without_scores=[20.0, 25.0, 30.0],
        )
        payload = build_p1_payload(manifest)
        pg = payload["pair_gains"][0]
        deltas = [d["delta"] for d in pg["deltas"]]
        assert deltas == [pytest.approx(x, abs=1e-3) for x in (80.0, 65.0, 65.0)]
        assert pg["variance"] > 0
        assert pg["std_dev"] == pytest.approx(math.sqrt(pg["variance"]), rel=1e-3)
        assert pg["ci_low"] < pg["mean_delta"] < pg["ci_high"]
        assert pg["n_paired"] == 3

        # HTML 报告包含这些要素
        p1_saved = save_p1_outputs(run_dir, payload)
        assert Path(p1_saved["paired_gain"]).exists()
        report_payload = build_report_payload(run_dir, manifest)
        html_text = render_report_html(report_payload)
        # 增益结论已合并进「③ 按模型看结果」区块（结论 pill + 解读 + 技术细节）
        assert "③ 按模型看结果" in html_text
        assert "负迁移警报" in html_text or "② 负迁移警报" in html_text or "负迁移" not in html_text
        assert "配对明细" in html_text
        # 统计术语下沉到「技术细节」折叠区，仍必须在报告中可见
        assert "95% CI" in html_text
        assert "平均分差" in html_text  # Δ̄ 的白话替代
        assert "技术细节" in html_text

    def test_model_ranking_demoted_to_second_tier(self, tmp_path):
        """合并后：按模型区块在前，配对明细/附录折叠在后（不再有独立模型排行区）"""
        run_dir, manifest = _build_run_dir(
            tmp_path, "effective-marker-skill", reps=3,
            with_effective=True, without_effective=False,
            with_scores=[100.0] * 3, without_scores=[0.0] * 3,
        )
        save_p1_outputs(run_dir, build_p1_payload(manifest))
        html_text = render_report_html(build_report_payload(run_dir, manifest))
        idx_models = html_text.index("③ 按模型看结果")
        idx_details = html_text.index("④ 配对明细")
        idx_appendix = html_text.index("⑤ 附录")
        assert idx_models < idx_details < idx_appendix
        # 旧独立模型排行区不再存在
        assert "模型排行" not in html_text


# ---------------------------------------------------------------------------
# 3. 客观断言判定同输入同判定（快照比对通过）
# ---------------------------------------------------------------------------

class TestAssertionDeterminism:
    def test_assertion_snapshot_reproduction(self, tmp_path):
        """同输入下断言判定结果逐条一致（快照机制在 配对夹具上复验）"""
        engine = AssertionEngine()
        rules = [AssertionRule.from_dict(r) for r in GOOD_RULES]
        records = [
            {"output_text": "", "artifacts": {"summary.md": _summary_text(True)},
             "context": {}, "results": [r.to_dict() for r in engine.evaluate_suite(
                 rules, output_text="", artifacts={"summary.md": _summary_text(True)}).results]},
            {"output_text": "", "artifacts": {"summary.md": _summary_text(False)},
             "context": {}, "results": [r.to_dict() for r in engine.evaluate_suite(
                 rules, output_text="", artifacts={"summary.md": _summary_text(False)}).results]},
        ]
        snapshot = engine.create_snapshot(rules, records, metadata={"case": "paired"})
        snap_path = tmp_path / "assertions_snapshot.json"
        save_assertions_snapshot(snapshot, snap_path)

        result = verify_assertions_snapshot_file(snap_path)
        assert result["verified"] is True
        assert result["mismatches"] == []
        assert result["total_checks"] == 4  # 2 records × 2 rules

    def test_paired_judgment_snapshot_reproduction(self, tmp_path):
        """判定快照：同输入重跑判定，结论与统计逐项一致"""
        _, manifest = _build_run_dir(
            tmp_path, "effective-marker-skill", reps=3,
            with_effective=True, without_effective=False,
            with_scores=[100.0, 95.0, 90.0], without_scores=[50.0, 45.0, 40.0],
        )
        payload = build_p1_payload(manifest)
        result = verify_p1_judgment_snapshot(payload["judgment_snapshot"])
        assert result["verified"] is True
        assert result["mismatches"] == []

        # 篡改输入后复验必须失败（证明比对有效）
        tampered = json.loads(json.dumps(payload["judgment_snapshot"]))
        tampered["inputs"]["observations"][0]["assertion_score"] = 0.0
        result2 = verify_p1_judgment_snapshot(tampered)
        assert result2["verified"] is False
        assert len(result2["mismatches"]) > 0


# ---------------------------------------------------------------------------
# 4. 主观任务报告含评委一致率统计
# ---------------------------------------------------------------------------

class TestJudgeAgreement:
    def _vote_details(self, base_ws: str) -> list[dict]:
        return [
            {"voting_model": "judge-a", "evaluations": [{"workspace_votes": {"方案A": f"{base_ws}/with-r1"}}]},
            {"voting_model": "judge-b", "evaluations": [{"workspace_votes": {"方案A": f"{base_ws}/without-r1"}}]},
            {"voting_model": "judge-c", "evaluations": [{"workspace_votes": {"方案A": f"{base_ws}/with-r1"}}]},
        ]

    def test_judge_agreement_in_report(self, tmp_path):
        base = str(tmp_path)
        manifest = {"results": [
            {"model": "m1", "use_skill": True, "repetition": 1, "workspace_dir": f"{base}/with-r1",
             "is_valid": True, "status": "completed", "success": True, "analysis": {}},
            {"model": "m1", "use_skill": False, "repetition": 1, "workspace_dir": f"{base}/without-r1",
             "is_valid": True, "status": "completed", "success": True, "analysis": {}},
        ]}
        payload = build_p1_payload(manifest, vote_details=self._vote_details(base))
        ja = payload["judge_agreement"]
        assert ja["definition"].startswith("同一配对内评委同判定比例")
        assert ja["overall_agreement_rate"] == pytest.approx(1 / 3, abs=1e-3)  # 3 个评委两两 3 对，仅 1 对相同
        assert len(ja["per_round"]) == 1
        assert ja["per_round"][0]["comparable_pairs"] == 3
        assert ja["per_round"][0]["agree_pairs"] == 1

        # 报告渲染含一致率
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        save_p1_outputs(run_dir, payload)
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        html_text = render_report_html(build_report_payload(run_dir, manifest))
        assert "评委一致率" in html_text
        assert "同一配对内评委同判定比例" in html_text

    def test_disagreement_triggers_human_review(self, tmp_path):
        """分歧大（一致率低于阈值）触发人审记录"""
        _, manifest = _build_run_dir(
            tmp_path, "effective-marker-skill", reps=3,
            with_effective=True, without_effective=False,
            with_scores=[95.0, 10.0, 90.0], without_scores=[40.0, 85.0, 15.0],
        )
        ws_with = manifest["results"][0]["workspace_dir"]  # with-skill rep1
        ws_without = manifest["results"][3]["workspace_dir"]  # without-skill rep1
        vote_details = [
            {"voting_model": "judge-a", "evaluations": [{"workspace_votes": {"A": ws_with}}]},
            {"voting_model": "judge-b", "evaluations": [{"workspace_votes": {"A": ws_without}}]},
        ]
        payload = build_p1_payload(manifest, vote_details=vote_details)
        triggers = payload["human_review_triggers"]
        types = {t["type"] for entry in triggers for t in entry["triggers"]}
        assert "judge_disagreement" in types
        assert "undecidable" in types  # 噪声场景 inconclusive 也触发
        # 触发记录落盘
        run_dir = tmp_path / "run"
        save_p1_outputs(run_dir, payload)
        saved = json.loads((run_dir / "p1" / "human_review_triggers.json").read_text(encoding="utf-8"))
        assert saved == payload["human_review_triggers"]
        assert saved  # 非空


# ---------------------------------------------------------------------------
# 5. inconclusive 实测不被判胜
# ---------------------------------------------------------------------------

class TestInconclusiveNotJudged:
    def test_high_variance_inconclusive(self):
        """大方差小样本：CI 跨 0 → 必须 inconclusive，不得判胜"""
        with_obs = [ReplicateObservation("m", True, i, f"w{i}", assertion_score=s)
                    for i, s in enumerate([95.0, 5.0, 90.0], start=1)]
        without_obs = [ReplicateObservation("m", False, i, f"wo{i}", assertion_score=s)
                       for i, s in enumerate([40.0, 80.0, 10.0], start=1)]
        pg = compute_pair_gain("m", with_obs, without_obs, has_assertions=True)
        assert pg.verdict == "inconclusive"
        assert pg.ci_low <= 0 <= pg.ci_high
        assert pg.negative_migration is False
        assert not pg.verdict_label.startswith("明确")

    def test_insufficient_reps_inconclusive_even_if_delta_positive(self):
        """N=2（< MIN_REPS_FOR_VERDICT）：即使 Δ 恒正也必须 inconclusive"""
        with_obs = [ReplicateObservation("m", True, i, f"w{i}", assertion_score=90.0) for i in (1, 2)]
        without_obs = [ReplicateObservation("m", False, i, f"wo{i}", assertion_score=10.0) for i in (1, 2)]
        pg = compute_pair_gain("m", with_obs, without_obs, has_assertions=True)
        assert pg.verdict == "inconclusive"
        assert pg.n_paired == 2
        assert "证据不足" in "".join(pg.reasons)

    def test_zero_variance_negative_not_strong_judged_below_min_reps(self):
        """N=2 且 Δ 恒负：证据不足 → inconclusive，不得触发负迁移警报"""
        with_obs = [ReplicateObservation("m", True, i, f"w{i}", assertion_score=10.0) for i in (1, 2)]
        without_obs = [ReplicateObservation("m", False, i, f"wo{i}", assertion_score=90.0) for i in (1, 2)]
        pg = compute_pair_gain("m", with_obs, without_obs, has_assertions=True)
        assert pg.verdict == "inconclusive"
        assert pg.negative_migration is False

    def test_decide_verdict_direct(self):
        assert decide_verdict(10, 5, 15, 3)[0] == "skill_effective"
        assert decide_verdict(-10, -15, -5, 3)[0] == "negative_migration"
        assert decide_verdict(10, -5, 15, 3)[0] == "inconclusive"  # CI 跨 0
        assert decide_verdict(-10, -15, -5, 2)[0] == "inconclusive"  # reps 不足
        assert decide_verdict(10, 5, 15, 2)[3] is False  # evidence_sufficient=False


# ---------------------------------------------------------------------------
# 6. 重复运行配置（默认 3）与计划展开
# ---------------------------------------------------------------------------

class TestRepetitions:
    def test_default_repetitions_is_three(self):
        cfg = EvaluationConfig(
            evaluation_name="t", task_prompt="t", workspace_root="w",
            models=["m"], runners=[RunnerConfig(name="r", command_template="run")],
        )
        assert cfg.repetitions == 3

    def test_plan_expands_repetitions(self, tmp_path):
        """repetitions=2 时计划展开 2 模型 × 2 mode × 2 rep = 8 个实验目录"""
        cfg = _make_config(tmp_path, "effective-marker-skill", repetitions=2)
        cfg.workspace_root = str(tmp_path / "ws")
        engine = EvaluationEngine(cfg, tmp_path / "config.json")
        run_dir = engine.create_run_directory()
        plans = engine.build_plans(run_dir)
        assert len(plans) == 1 * 2 * 2  # models × modes × reps
        reps_seen = {plan.repetition for _, plan in plans}
        assert reps_seen == {1, 2}
        names = {plan.workspace_dir.name for _, plan in plans}
        assert "opencode__test-model__with-skill__rep1" in names
        assert "opencode__test-model__without-skill__rep2" in names

    def test_config_repetitions_validation(self):
        raw = {
            "evaluation_name": "t", "task_prompt": "t", "workspace_root": "w",
            "models": ["m"], "runners": [{"name": "r", "command_template": "run"}],
            "repetitions": 5,
        }
        cfg = EvaluationConfig.from_dict(raw)
        assert cfg.repetitions == 5


# ---------------------------------------------------------------------------
# 7. 负迁移检出 → 「该模型上建议关闭 skill」显著标注
# ---------------------------------------------------------------------------

class TestNegativeMigrationAlert:
    def test_report_shows_close_skill_recommendation(self, tmp_path):
        run_dir, manifest = _build_run_dir(
            tmp_path, "harmful-marker-skill", reps=3,
            with_effective=False, without_effective=True,
            with_scores=[0.0, 0.0, 0.0], without_scores=[100.0, 100.0, 100.0],
        )
        payload = build_p1_payload(manifest)
        save_p1_outputs(run_dir, payload)
        html_text = render_report_html(build_report_payload(run_dir, manifest))
        assert "负迁移警报" in html_text
        assert "建议在该模型上关闭此 skill" in html_text
        assert "🚨" in html_text

    def test_high_risk_trigger_recorded(self, tmp_path):
        _, manifest = _build_run_dir(
            tmp_path, "harmful-marker-skill", reps=3,
            with_effective=False, without_effective=True,
            with_scores=[0.0] * 3, without_scores=[100.0] * 3,
        )
        payload = build_p1_payload(manifest)
        triggers = payload["human_review_triggers"]
        assert any(
            t["type"] == "high_risk" and "关闭 skill" in t["reason"]
            for entry in triggers for t in entry["triggers"]
        )


# ---------------------------------------------------------------------------
# 8. rebuild_p1_outputs 端到端（含 blind_vote evaluations）
# ---------------------------------------------------------------------------

class TestRebuildEndToEnd:
    def test_rebuild_from_run_dir(self, tmp_path):
        base = str(tmp_path)
        run_dir, manifest = _build_run_dir(
            tmp_path, "effective-marker-skill", reps=3,
            with_effective=True, without_effective=False,
            with_scores=[100.0, 100.0, 95.0], without_scores=[10.0, 15.0, 5.0],
        )
        # 模拟盲评落盘
        bv = run_dir / "blind_vote"
        bv.mkdir()
        (bv / "evaluations.json").write_text(json.dumps([
            {"voting_model": "judge-a", "evaluations": [{"workspace_votes": {
                "A": f"{base}/run_effective-marker-skill/experiments/opencode__test_model__with-skill__rep1"}}]},
            {"voting_model": "judge-b", "evaluations": [{"workspace_votes": {
                "A": f"{base}/run_effective-marker-skill/experiments/opencode__test_model__with-skill__rep1"}}]},
        ]), encoding="utf-8")

        payload = rebuild_p1_outputs(run_dir)
        assert payload is not None
        pg = payload["pair_gains"][0]
        assert pg["verdict"] == "skill_effective"
        # 盲评票数也进入观测量（第二层 blind_vote deltas 存在）
        assert payload["judge_agreement"]["overall_agreement_rate"] == 1.0

        # 判定快照可复验
        snap = json.loads((run_dir / "p1" / "judgment_snapshot.json").read_text(encoding="utf-8"))
        assert verify_p1_judgment_snapshot(snap)["verified"] is True

    def test_rebuild_without_manifest_returns_none(self, tmp_path):
        assert rebuild_p1_outputs(tmp_path) is None
