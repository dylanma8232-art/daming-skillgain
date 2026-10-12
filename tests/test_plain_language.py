"""报告可读性测试：解读文案、按模型产物对照、术语翻译与层级重排。

覆盖验收标准：
1. verdict → 白话文案三场景映射（skill_effective / negative_migration / inconclusive）各有对应且互不串台（内部文案，不面向用户展示）
2. 白话输出零统计术语（Δ / CI / 置信区间 / 方差 / 标准差 / inconclusive / 断言）
3. 盲评票数 → 「X 位评委中有 Y 位认为带 skill 更好」+ 评委原话引用
4. 首屏总结三句话以内
5. 渲染报告：总结 + 按模型区块（含产物对照），统计术语下沉技术细节折叠区
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from evaluator.plain_language import (
    JARGON_TERMS,
    build_plain_summary,
    contains_untranslated_jargon,
    verdict_to_plain_language,
    votes_to_plain_language,
)
from evaluator.paired_gain import build_p1_payload, save_p1_outputs
from evaluator.reporting import build_report_payload, render_report_html

from tests.test_paired_gain import _build_run_dir


# ---------------------------------------------------------------------------
# 1. verdict → 白话文案三场景映射
# ---------------------------------------------------------------------------

class TestVerdictPlainLanguage:
    def test_skill_effective_maps_to_recommend_on(self):
        text = verdict_to_plain_language("skill_effective", n=3, mean_delta=12.0)
        assert "带这个 skill 的产出明显更好" in text
        assert "建议开启" in text
        assert contains_untranslated_jargon(text) == []

    def test_negative_migration_maps_to_recommend_off(self):
        text = verdict_to_plain_language("negative_migration", n=3, mean_delta=-8.0)
        assert "带这个 skill 反而更差" in text
        assert "建议在这个模型上关闭" in text
        assert contains_untranslated_jargon(text) == []

    def test_inconclusive_maps_to_no_difference_with_next_steps(self):
        text = verdict_to_plain_language("inconclusive", n=3, mean_delta=1.9)
        assert "没测出明显区别" in text
        # 下一步指引：重复次数 / 更难任务 / 看评委评价
        assert "重复" in text
        assert "更难" in text
        assert "评委" in text
        assert contains_untranslated_jargon(text) == []

    def test_unknown_verdict_falls_back_to_inconclusive_copy(self):
        text = verdict_to_plain_language("weird_unknown")
        assert "没测出明显区别" in text

    def test_plain_texts_never_contain_jargon_terms(self):
        for verdict in ("skill_effective", "negative_migration", "inconclusive"):
            text = verdict_to_plain_language(verdict, n=3, mean_delta=1.9)
            for term in JARGON_TERMS:
                assert term not in text, f"{verdict} 白话中出现了术语 {term}"


# ---------------------------------------------------------------------------
# 2. 盲评投票 → 白话
# ---------------------------------------------------------------------------

class TestVotesPlainLanguage:
    def test_votes_translated_with_judge_quote(self):
        text = votes_to_plain_language(3, 1, 0, ["方案A 结构完整且预算表清晰"])
        assert "4 位评委中有 3 位认为带 skill 更好" in text
        assert "1 位认为不带更好" in text
        assert "方案A 结构完整且预算表清晰" in text
        assert contains_untranslated_jargon(text) == []

    def test_ties_mentioned(self):
        text = votes_to_plain_language(2, 2, 1, [])
        assert "1 位觉得差不多" in text
        assert "理由" not in text  # 无理由时不留空引用

    def test_zero_votes(self):
        text = votes_to_plain_language(0, 0, 0, [])
        assert "没有留下有效投票" in text


# ---------------------------------------------------------------------------
# 3. 首屏白话总结
# ---------------------------------------------------------------------------

class TestPlainSummary:
    def test_all_inconclusive_summary(self):
        pair_gains = [
            {"model": "m1", "verdict": "inconclusive"},
            {"model": "m2", "verdict": "inconclusive"},
        ]
        groups = [{"model": "m1", "with_votes": 0, "without_votes": 0},
                  {"model": "m2", "with_votes": 0, "without_votes": 0}]
        text = build_plain_summary(pair_gains, groups)
        assert text.count("。") <= 4  # 三句话以内（句末标点计句）
        assert "2 个模型" in text
        assert "没测出明显区别" in text
        assert contains_untranslated_jargon(text) == []

    def test_effective_summary(self):
        pair_gains = [{"model": "m1", "verdict": "skill_effective"}]
        groups = [{"model": "m1", "with_votes": 4, "without_votes": 0}]
        text = build_plain_summary(pair_gains, groups)
        assert "明显更好" in text
        assert "放心开启" in text

    def test_harmful_summary(self):
        pair_gains = [{"model": "m1", "verdict": "negative_migration"}]
        groups = [{"model": "m1", "with_votes": 0, "without_votes": 4}]
        text = build_plain_summary(pair_gains, groups)
        assert "反而更差" in text
        assert "关闭" in text

    def test_mixed_summary(self):
        pair_gains = [
            {"model": "m1", "verdict": "skill_effective"},
            {"model": "m2", "verdict": "negative_migration"},
        ]
        groups = [
            {"model": "m1", "with_votes": 3, "without_votes": 0},
            {"model": "m2", "with_votes": 0, "without_votes": 3},
        ]
        text = build_plain_summary(pair_gains, groups)
        assert "因模型而异" in text

    def test_empty_runs(self):
        text = build_plain_summary([], [])
        assert "没有产出可对比的结果" in text


# ---------------------------------------------------------------------------
# 4. 渲染报告：总结区 + 按模型区块（合并后单一产物展示层）+ 术语下沉
# ---------------------------------------------------------------------------

class TestReportPlainLanguageRendering:
    def _render(self, tmp_path: Path) -> str:
        run_dir, manifest = _build_run_dir(
            tmp_path, "effective-marker-skill", reps=3,
            with_effective=True, without_effective=False,
            with_scores=[100.0, 95.0, 90.0], without_scores=[40.0, 45.0, 35.0],
        )
        save_p1_outputs(run_dir, build_p1_payload(manifest))
        return render_report_html(build_report_payload(run_dir, manifest))

    def test_plain_summary_block_present(self, tmp_path):
        html = self._render(tmp_path)
        assert "① 总结" in html
        assert "💡 总结" in html
        assert "💡 解读" in html
        assert "③ 按模型看结果" in html
        # 用户可见区零「白话」
        assert "白话" not in html

    def test_single_product_display_layer(self, tmp_path):
        """唯一产物展示层 = ③ 按模型看结果，不再有独立模型排行 artifact 卡区"""
        html = self._render(tmp_path)
        assert "模型排行" not in html
        assert "② 看看实际产出" not in html
        # 每个模型区块包含双栏主交付物预览（测试夹具只有 1 个模型）
        blocks = re.findall(r"<article class='compare-card model-result'>.*?</article>", html, flags=re.S)
        assert len(blocks) == 1
        for block in blocks:
            assert "compare-preview" in block
            assert "查看完整文件" in block

    def test_technical_terms_sunk_into_collapsible_details(self, tmp_path):
        html = self._render(tmp_path)
        # 解读条内的统计口径必须包在 details.tech-details 里
        for m in re.finditer(r"💡 解读：.*?</div>", html, flags=re.S):
            segment = m.group(0)
            assert "95% CI" not in segment
            assert "置信区间" not in segment
        # 技术细节折叠区存在且默认收起（details 无 open 属性）
        assert "tech-details" in html
        assert re.search(r"<details class='tech-details'>", html)
        assert not re.search(r"<details class='tech-details' open", html)

    def test_judgment_copy_is_plain(self, tmp_path):
        html = self._render(tmp_path)
        # 有效 skill 场景：解读里出现「建议开启」
        assert "建议开启" in html

    def test_per_model_block_has_full_content(self, tmp_path):
        """每个模型区块：结论 pill + 票数 + 评委评价 + 双栏预览 + 诊断 + 技术折叠"""
        html = self._render(tmp_path)
        blocks = re.findall(r"<article class='compare-card model-result'>.*?</article>", html, flags=re.S)
        assert len(blocks) == 1
        for block in blocks:
            assert "评委打分" in block
            assert "评委评价" in block
            assert "diagnostics" in block
            assert "▸ 技术细节" in block
            assert block.count("delivery-preview") == 2

    def test_product_preview_uses_expanded_not_details(self, tmp_path):
        html = self._render(tmp_path)
        start = html.index("③ 按模型看结果")
        end = html.index("④ 配对明细")
        section = html[start:end]
        # 主交付物预览不允许被 details 折叠（技术折叠只包统计口径与过程证据）
        assert "compare-preview" in section
        assert "查看完整文件" in section

    def test_section_order_summary_before_details(self, tmp_path):
        html = self._render(tmp_path)
        order = [
            html.index("① 总结"),
            html.index("③ 按模型看结果"),
            html.index("④ 配对明细"),
            html.index("⑤ 附录"),
        ]
        assert order == sorted(order)

    def test_negative_alert_section_only_when_detected(self, tmp_path):
        """② 负迁移警报：无负迁移时不渲染该区块；有负迁移时醒目提示"""
        html = self._render(tmp_path)
        assert "② 负迁移警报" not in html
