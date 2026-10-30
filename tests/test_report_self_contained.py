from __future__ import annotations

from evaluator.report_payload import build_first_screen_payload, check_report_self_contained
from evaluator.reporting import render_report_html


def _sample_payload() -> dict:
    return {
        "evaluation_name": "demo-evaluation",
        "target_skill": "demo-skill",
        "task_prompt": "把这段话总结成三条要点",
        "tested_models": ["m1"],
        "judge_models": ["j1"],
        "host_boundary": {"tested_hosts": ["script"], "statement": "结论只适用于已测工具。"},
        "grouped_results": [
            {
                "model": "m1",
                "with_votes": 3,
                "without_votes": 1,
                "ties": 0,
                "total_votes": 4,
                "with_skill": {"artifacts": [{"name": "a.md", "path": "a.md"}], "workspace_dir": "/tmp/a"},
                "without_skill": {"artifacts": [{"name": "b.md", "path": "b.md"}], "workspace_dir": "/tmp/b"},
                "final_reasons": [],
                "key_differences": [],
                "with_reasons": [],
                "without_reasons": [],
            }
        ],
        "statistics": {
            "sample_size_total": 1,
            "sample_size_valid": 3,
            "invalid_runs_count": 1,
            "mean_delta": 1.5,
            "variance": 0.25,
            "std_dev": 0.5,
            "distribution": {"with_wins": 2, "without_wins": 1, "ties": 0},
            "small_sample_warning": "",
        },
        "p1": {
            "pair_gains": [
                {
                    "model": "m1",
                    "verdict": "skill_effective",
                    "n_paired": 3,
                    "mean_delta": 1.5,
                    "primary_metric": "blind_vote",
                    "deltas": [{"repetition": 1, "with_value": 1.0, "without_value": 0.0, "delta": 1.0}],
                    "reasons": ["配对 Δ 均值 1.5 > 0"],
                }
            ]
        },
        "assertions_snapshot": {
            "engine_version": "1.1.0",
            "rules_count": 1,
            "rules_snapshot": [{"name": "must_contain"}],
            "records": [
                {
                    "model": "m1",
                    "use_skill": True,
                    "repetition": 1,
                    "results": [
                        {"name": "字数达标", "passed": True, "message": "输出包含三条要点"},
                        {"name": "包含关键词", "passed": False, "message": "缺少关键词：天气"},
                    ],
                }
            ],
        },
    }


def test_first_screen_payload_has_four_parts():
    first = build_first_screen_payload(_sample_payload())
    assert first["verdict"] == "skill_effective"
    assert len(first["kpis"]) == 3
    assert first["models"] and first["models"][0]["with_votes"] == 3
    assert first["cases_passed"] == 1 and first["cases_failed"] == 1
    assert first["headline"]


def test_rendered_report_first_screen_order_and_content():
    html_text = render_report_html(_sample_payload())
    # 首屏四件套顺序：结论 → 指标 → 红绿清单 → 有/无对比
    idx_verdict = html_text.index("明确有效")
    idx_kpi = html_text.index("结论分布")
    idx_cases = html_text.index("自动检查结果")
    idx_compare = html_text.index("带 Skill vs 不带 Skill")
    assert idx_verdict < idx_kpi < idx_cases < idx_compare
    # 红绿清单逐条
    assert "✓ 字数达标" in html_text
    assert "✗ 包含关键词" in html_text


def test_non_folded_area_has_no_jargon():
    import re as _re

    html_text = render_report_html(_sample_payload())
    folded_start = html_text.index("<details")
    first_screen = html_text[:folded_start]
    first_screen = _re.sub(r"<style>.*?</style>", "", first_screen, flags=_re.S)
    first_screen = _re.sub(r"<script>.*?</script>", "", first_screen, flags=_re.S)
    for term in ("Δ", "σ²", "N=", "rep", "With/Without", "断言", "置信区间", "宿主"):
        assert term not in first_screen, term


def test_report_is_self_contained():
    html_text = render_report_html(_sample_payload())
    assert "<link" not in html_text.lower()
    assert check_report_self_contained(html_text) == []


def test_self_check_catches_external_links():
    problems = check_report_self_contained("<link rel='x' href='y'><script src='https://x/y.js'></script>")
    assert problems
