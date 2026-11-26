"""demo.py - 零配置、零 API Key 的离线示例报告生成器。

让新用户在 1 秒内直观体验 daming-skillgain 的配对测评结论与自包含 HTML 报告：
- 覆盖三种真实结论：明确有效 / 负迁移（应关闭）/ 测不出来
- 直接复用正式报告渲染链路（render_report_html），确保产物 100% 自包含、零外链
"""
from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Any

from .io_utils import write_text
from .reporting import render_report_html

DEFAULT_DEMO_OUTPUT = "skillgain-demo-report.html"


def build_demo_payload() -> dict[str, Any]:
    """构造覆盖三种结论（有效 / 负迁移 / 测不出来）的离线示例报告 payload。"""
    return {
        "evaluation_name": "release-notes-writer-demo",
        "target_skill": "release-notes-writer",
        "task_prompt": (
            "根据本周 12 条 Git Commit 记录，整理一份面向外部开发者的 v2.1.0 发布说明（Markdown），"
            "要求按「新特性 / 问题修复 / 破坏性变更」分组，列出升级命令，且不编造未提交的功能。"
        ),
        "tested_models": [
            "claude-sonnet-4",
            "gpt-4.1-mini",
            "deepseek-v3",
        ],
        "judge_models": ["claude-sonnet-4", "gpt-4.1"],
        "host_boundary": {
            "tested_hosts": ["opencode"],
            "statement": (
                "本示例报告由 `skillgain demo` 离线生成，用于演示配对评测结构。"
                "真实评测结论仅适用于本次实际测过的工具与模型。"
            ),
        },
        "grouped_results": [
            {
                "model": "claude-sonnet-4",
                "with_votes": 3,
                "without_votes": 0,
                "ties": 0,
                "total_votes": 3,
                "with_skill": {
                    "status": "completed",
                    "duration_seconds": 28.4,
                    "tokens": 6840,
                    "artifacts": [
                        {
                            "name": "RELEASE_NOTES.md",
                            "path": "RELEASE_NOTES.md",
                            "preview": (
                                "# v2.1.0 发布说明\n\n"
                                "## ✨ 新特性\n"
                                "- 新增 `skillgain demo` 离线体验命令，无需 API Key 即可生成示例报告\n"
                                "- 新增 `skillgain doctor` 环境体检命令，一键检查本地 AI 工具与依赖\n\n"
                                "## 🐛 问题修复\n"
                                "- 修复更新检查器把正式仓库误判为占位符的问题\n\n"
                                "## ⚠️ 破坏性变更\n"
                                "- 无破坏性变更，支持平滑升级\n\n"
                                "## 升级方式\n"
                                "```bash\npip install -U daming-skillgain\n```"
                            ),
                        }
                    ],
                },
                "without_skill": {
                    "status": "completed",
                    "duration_seconds": 24.1,
                    "tokens": 5120,
                    "artifacts": [
                        {
                            "name": "RELEASE_NOTES.md",
                            "path": "RELEASE_NOTES.md",
                            "preview": (
                                "# Release Notes v2.1.0\n\n"
                                "本次更新做了一些改进和修复：\n"
                                "- 增加了 demo 命令和 doctor 命令\n"
                                "- 修复了若干 bug\n"
                                "- 优化了文档与用户体验\n"
                            ),
                        }
                    ],
                },
                "final_reasons": [
                    "带 Skill 版本严格按三大分组组织，包含升级命令与破坏性变更确认，无编造内容",
                    "不带 Skill 版本缺少分组、遗漏升级命令，描述偏笼统",
                ],
                "key_differences": [
                    "结构完整度：带 Skill 3/3 必选分组齐全，不带 Skill 仅列流水账",
                    "事实核对：带 Skill 逐条对应 commit，不带 Skill 出现泛化表述",
                ],
                "with_reasons": ["分组清晰、升级命令明确、事实准确"],
                "without_reasons": ["篇幅较短但遗漏升级步骤与破坏性变更说明"],
            },
            {
                "model": "gpt-4.1-mini",
                "with_votes": 0,
                "without_votes": 3,
                "ties": 0,
                "total_votes": 3,
                "with_skill": {
                    "status": "completed",
                    "duration_seconds": 41.2,
                    "tokens": 11420,
                    "artifacts": [
                        {
                            "name": "RELEASE_NOTES.md",
                            "path": "RELEASE_NOTES.md",
                            "preview": (
                                "# v2.1.0 企业级变更审计报告（超长模板）\n\n"
                                "注意：本模板包含 8 个强制章节，以下为占位填充：\n"
                                "- 性能基准提升 40%（未在 commit 中出现，按模板补齐）\n"
                                "- 安全合规声明：待补充\n"
                            ),
                        }
                    ],
                },
                "without_skill": {
                    "status": "completed",
                    "duration_seconds": 19.6,
                    "tokens": 4280,
                    "artifacts": [
                        {
                            "name": "RELEASE_NOTES.md",
                            "path": "RELEASE_NOTES.md",
                            "preview": (
                                "# v2.1.0 更新日志\n\n"
                                "## 新特性\n"
                                "- 新增 `skillgain demo` 和 `skillgain doctor`\n\n"
                                "## 问题修复\n"
                                "- 修复版本检查误判\n\n"
                                "## 破坏性变更\n"
                                "- 无\n\n"
                                "升级：`pip install -U daming-skillgain`"
                            ),
                        }
                    ],
                },
                "final_reasons": [
                    "带 Skill 在小模型上触发过度遵循模板，反而编造了未提交的性能数据，判定为负迁移",
                    "不带 Skill 版本简洁准确，满足全部要求，建议在该模型上关闭此 Skill",
                ],
                "key_differences": [
                    "负迁移：带 Skill 强塞占位章节并引入幻觉，不带 Skill 干净利落",
                    "成本：带 Skill 耗时与 Token 消耗翻倍以上，结果反而更差",
                ],
                "with_reasons": ["格式规整但出现编造指标与多余占位符"],
                "without_reasons": ["简明准确、零编造、符合全部硬性要求"],
            },
            {
                "model": "deepseek-v3",
                "with_votes": 2,
                "without_votes": 1,
                "ties": 0,
                "total_votes": 3,
                "with_skill": {
                    "status": "completed",
                    "duration_seconds": 31.0,
                    "tokens": 6150,
                    "artifacts": [
                        {
                            "name": "RELEASE_NOTES.md",
                            "path": "RELEASE_NOTES.md",
                            "preview": (
                                "# v2.1.0 发布说明\n\n"
                                "## 新特性\n- 新增离线 demo 与环境体检命令\n\n"
                                "## 问题修复\n- 修复版本更新检查逻辑\n\n"
                                "## 破坏性变更\n- 无\n"
                            ),
                        }
                    ],
                },
                "without_skill": {
                    "status": "completed",
                    "duration_seconds": 29.8,
                    "tokens": 5890,
                    "artifacts": [
                        {
                            "name": "RELEASE_NOTES.md",
                            "path": "RELEASE_NOTES.md",
                            "preview": (
                                "# v2.1.0 Release Notes\n\n"
                                "## 新特性\n- 新增 `skillgain demo` 与 `skillgain doctor`\n\n"
                                "## 修复\n- 修复版本检查问题\n\n"
                                "## 升级命令\n`pip install -U daming-skillgain`\n"
                            ),
                        }
                    ],
                },
                "final_reasons": [
                    "两组产出质量接近，3 轮盲评呈 2:1 波动，证据不足以下确定结论",
                    "建议增加重复轮次或换更复杂的真实任务再复测",
                ],
                "key_differences": [
                    "两版均完成核心要求，仅在个别小节与命令示例上有细微差别",
                ],
                "with_reasons": ["结构略规范，但在其中一轮遗漏了升级命令"],
                "without_reasons": ["整体质量稳定，与带 Skill 差距不显著"],
            },
        ],
        "statistics": {
            "sample_size_total": 9,
            "sample_size_valid": 9,
            "invalid_runs_count": 0,
            "mean_delta": 0.11,
            "variance": 0.86,
            "std_dev": 0.93,
            "distribution": {"with_wins": 5, "without_wins": 4, "ties": 0},
            "small_sample_warning": "",
        },
        "p1": {
            "pair_gains": [
                {
                    "model": "claude-sonnet-4",
                    "verdict": "skill_effective",
                    "n_paired": 3,
                    "mean_delta": 1.0,
                    "primary_metric": "blind_vote",
                    "deltas": [
                        {"repetition": 1, "with_value": 1.0, "without_value": 0.0, "delta": 1.0},
                        {"repetition": 2, "with_value": 1.0, "without_value": 0.0, "delta": 1.0},
                        {"repetition": 3, "with_value": 1.0, "without_value": 0.0, "delta": 1.0},
                    ],
                    "reasons": ["3 轮配对盲评全胜（3:0），确定性检查全部通过"],
                },
                {
                    "model": "gpt-4.1-mini",
                    "verdict": "negative_migration",
                    "negative_migration": True,
                    "n_paired": 3,
                    "mean_delta": -1.0,
                    "primary_metric": "blind_vote",
                    "deltas": [
                        {"repetition": 1, "with_value": 0.0, "without_value": 1.0, "delta": -1.0},
                        {"repetition": 2, "with_value": 0.0, "without_value": 1.0, "delta": -1.0},
                        {"repetition": 3, "with_value": 0.0, "without_value": 1.0, "delta": -1.0},
                    ],
                    "reasons": ["3 轮配对盲评全败（0:3），且触发「禁止编造」红线，检出负迁移"],
                },
                {
                    "model": "deepseek-v3",
                    "verdict": "inconclusive",
                    "n_paired": 3,
                    "mean_delta": 0.33,
                    "primary_metric": "blind_vote",
                    "deltas": [
                        {"repetition": 1, "with_value": 1.0, "without_value": 0.0, "delta": 1.0},
                        {"repetition": 2, "with_value": 0.0, "without_value": 1.0, "delta": -1.0},
                        {"repetition": 3, "with_value": 1.0, "without_value": 0.0, "delta": 1.0},
                    ],
                    "reasons": ["3 轮结果 2:1 波动较大，置信区间跨 0，证据不足不强下结论"],
                },
            ]
        },
        "assertions_snapshot": {
            "engine_version": "2.1.0",
            "rules_count": 4,
            "rules_snapshot": [
                {"name": "包含三大必选分组"},
                {"name": "包含可执行升级命令"},
                {"name": "显式说明破坏性变更"},
                {"name": "未编造 commit 之外的性能数字"},
            ],
            "records": [
                {
                    "model": "claude-sonnet-4",
                    "use_skill": True,
                    "repetition": 1,
                    "results": [
                        {"name": "包含三大必选分组", "passed": True, "message": "新特性/问题修复/破坏性变更齐全"},
                        {"name": "包含可执行升级命令", "passed": True, "message": "包含 pip install -U daming-skillgain"},
                        {"name": "显式说明破坏性变更", "passed": True, "message": "明确标注无破坏性变更"},
                        {"name": "未编造 commit 之外的性能数字", "passed": True, "message": "无未核实指标"},
                    ],
                },
                {
                    "model": "gpt-4.1-mini",
                    "use_skill": True,
                    "repetition": 1,
                    "results": [
                        {"name": "包含三大必选分组", "passed": False, "message": "输出了无关的企业审计模板章节"},
                        {"name": "未编造 commit 之外的性能数字", "passed": False, "message": "命中未提交的「性能提升 40%」表述"},
                    ],
                },
            ],
        },
    }


def generate_demo_report(
    output_path: str | Path = DEFAULT_DEMO_OUTPUT,
    open_browser: bool = False,
) -> dict[str, Any]:
    """生成离线自包含示例报告并返回结果摘要。"""
    target = Path(output_path).expanduser().resolve()
    payload = build_demo_payload()
    html_text = render_report_html(payload)
    write_text(target, html_text)

    opened = False
    if open_browser:
        try:
            opened = bool(webbrowser.open(target.as_uri()))
        except Exception:
            opened = False

    return {
        "success": True,
        "demo": True,
        "report_path": str(target),
        "opened_in_browser": opened,
        "models_demonstrated": {
            "claude-sonnet-4": "skill_effective",
            "gpt-4.1-mini": "negative_migration",
            "deepseek-v3": "inconclusive",
        },
        "next_step": f"在浏览器中打开 {target} 查看交互式自包含报告；准备好自己的 Skill 后运行 `skillgain simple` 开始真实测评。",
    }
