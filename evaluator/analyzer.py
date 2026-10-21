from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .io_utils import read_json, write_json, write_text
from .models import AnalysisConfig, ExperimentResult

STUCK_PATTERNS = [
    r"请确认",
    r"请回复",
    r"请回复您的选择",
    r"等待用户",
    r"是否导出",
    r"是否保存",
    r"请告诉我",
    r"请选择",
    r"您希望",
]

COMPLETION_PATTERNS = [
    r"任务完成",
    r"任务已完成",
    r"分析完成",
    r"已完成分析",
    r"最终报告",
    r"报告已生成",
    r"执行完成",
    r"处理完成",
    r"done",
    r"completed",
    r"finished",
]

CHECKPOINT_PATTERNS = [
    r"\[CHECKPOINT\]",
    r"\[checkpoint\]",
    r"CHECKPOINT:",
    r"检查点:",
    r"阶段完成",
    r"Phase \d+ completed",
    r"Step \d+ completed",
    r"决策:",
    r"decision:",
    r"假设:",
    r"assumption:",
]

DECISION_PATTERNS = [
    r"决策[：:]\s*(.+)",
    r"选择方案[：:]\s*(.+)",
    r"决定[：:]\s*(.+)",
    r"我选择[：]\s*(.+)",
    r"采用[：]\s*(.+)",
]

ASSUMPTION_PATTERNS = [
    r"假设[：:]\s*(.+)",
    r"假设前提[：:]\s*(.+)",
    r"默认[：:]\s*(.+)",
    r"假定[：:]\s*(.+)",
]

ERROR_RECOVERY_PATTERNS = [
    r"遇到错误",
    r"error发生",
    r"重试",
    r"retry",
    r"恢复",
    r"recovery",
    r"fallback",
    r"备选方案",
]

CATEGORY_EXPECTATIONS = {
    "browser_automation": {".md", ".json", ".html", ".htm", ".png"},
    "data_analysis": {".md", ".json", ".csv", ".xlsx", ".xls"},
    "code_generation": {".py", ".js", ".ts", ".tsx", ".jsx", ".md"},
    "database": {".md", ".json", ".sql", ".csv"},
    "document": {".md", ".txt", ".html", ".docx"},
    "api_integration": {".json", ".md", ".txt", ".csv"},
    "file_processing": {".md", ".json", ".csv", ".xlsx", ".txt"},
}

TEXT_PREVIEW_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".csv",
    ".html",
    ".htm",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sql",
    ".yaml",
    ".yml",
}

SKILL_USAGE_PATTERNS = [
    r"skill",
    r"search\.py",
    r"design\.system",
]


def detect_stuck_state(stdout_text: str, transcript_text: str = "") -> dict[str, Any]:
    stuck_info = {
        "is_stuck": False,
        "stuck_type": None,
        "stuck_at_phase": None,
        "waiting_for_user": False,
        "details": [],
        "self_resolved": False,
    }

    if not stdout_text:
        return stuck_info

    combined = f"{stdout_text}\n{transcript_text}"
    combined_lower = combined.lower()

    user_prompts_found = []
    for pattern in STUCK_PATTERNS:
        matches = list(re.finditer(pattern, combined, re.IGNORECASE))
        if matches:
            user_prompts_found.append(pattern)
            for match in matches:
                stuck_info["waiting_for_user"] = True
                stuck_info["stuck_type"] = "waiting_for_user_input"

    if user_prompts_found:
        self_response_indicators = [
            r"确认",
            r"选择",
            r"继续",
            r"是",
            r"否",
            r"默认",
            r"assume",
            r"assume",
            r"decision",
            r"自主",
            r"自动",
            r"我选择",
            r"我将",
            r"我们继续",
            r" proceed",
            r"假设",
            r"保守默认",
        ]
        has_self_response = any(
            re.search(indicator, combined_lower)
            for indicator in self_response_indicators
        )

        last_prompt_pos = 0
        for pattern in user_prompts_found:
            for match in re.finditer(pattern, combined, re.IGNORECASE):
                last_prompt_pos = max(last_prompt_pos, match.end())

        remaining_text = combined[last_prompt_pos:] if last_prompt_pos > 0 else ""
        has_subsequent_execution = bool(
            re.search(
                r"(phase\s*\d+|执行|完成|分析|生成|创建|提交|下载|导出|运行|调用)",
                remaining_text,
                re.IGNORECASE,
            )
        )

        if has_self_response or has_subsequent_execution:
            stuck_info["self_resolved"] = True
            stuck_info["is_stuck"] = False
            stuck_info["details"].append(
                "检测到确认请求，但模型已自主响应或继续执行，不算卡住"
            )
        else:
            stuck_info["is_stuck"] = True
            stuck_info["details"].append(
                f"发现等待用户输入的模式，且未检测到自主响应: {', '.join(user_prompts_found[:2])}"
            )

    phase_matches = list(re.finditer(r"Phase\s*(\d+)\s*完成", combined))
    if phase_matches and not stuck_info.get("self_resolved"):
        last_phase = int(phase_matches[-1].group(1))
        after_phase = combined[phase_matches[-1].end() :]
        has_task_completion = bool(
            re.search(
                r"(任务完成|完成|done|completed|finished)", after_phase, re.IGNORECASE
            )
        )
        has_next_phase = bool(re.search(f"Phase\\s*{last_phase + 1}", combined))
        if not has_next_phase and not has_task_completion and last_phase < 8:
            stuck_info["is_stuck"] = True
            stuck_info["stuck_at_phase"] = last_phase
            stuck_info["stuck_type"] = "stopped_at_phase"
            stuck_info["details"].append(f"停在 Phase {last_phase}，未继续执行")

    return stuck_info


def validate_artifact_content(artifacts_dir: Path) -> dict[str, Any]:
    validation = {
        "artifacts_checked": 0,
        "artifacts_valid": 0,
        "artifacts_invalid": 0,
        "details": {},
    }

    if not artifacts_dir.exists():
        return validation

    for f in sorted(artifacts_dir.rglob("*")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(artifacts_dir))
        validation["artifacts_checked"] += 1

        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            validation["details"][rel] = {
                "exists": True,
                "valid": False,
                "reason": "无法读取",
            }
            validation["artifacts_invalid"] += 1
            continue

        if len(content.strip()) < 10:
            validation["details"][rel] = {
                "exists": True,
                "valid": False,
                "reason": "文件内容过少",
                "size": len(content),
            }
            validation["artifacts_invalid"] += 1
            continue

        if f.suffix == ".json":
            try:
                data = json.loads(content)
                if isinstance(data, (dict, list)) and len(data) > 0:
                    validation["details"][rel] = {
                        "exists": True,
                        "valid": True,
                        "reason": "有效JSON",
                        "size": len(content),
                    }
                    validation["artifacts_valid"] += 1
                else:
                    validation["details"][rel] = {
                        "exists": True,
                        "valid": False,
                        "reason": "JSON为空",
                    }
                    validation["artifacts_invalid"] += 1
            except json.JSONDecodeError as e:
                validation["details"][rel] = {
                    "exists": True,
                    "valid": False,
                    "reason": f"JSON解析失败: {str(e)[:50]}",
                }
                validation["artifacts_invalid"] += 1
        else:
            validation["details"][rel] = {
                "exists": True,
                "valid": True,
                "reason": "文件非空",
                "size": len(content),
            }
            validation["artifacts_valid"] += 1

    return validation


def check_task_completion(stdout_text: str, transcript_text: str) -> dict[str, Any]:
    completion = {"is_completed": False, "completion_type": None, "evidence": []}
    combined_text = stdout_text + "\n" + transcript_text
    for pattern in COMPLETION_PATTERNS:
        if re.search(pattern, combined_text, re.IGNORECASE):
            completion["is_completed"] = True
            completion["completion_type"] = "explicit_completion"
            completion["evidence"].append(f"发现完成模式: {pattern}")
    return completion


def extract_skill_usage(transcript_text: str, skill_name: str = "") -> dict[str, Any]:
    usage = {
        "skill_name": skill_name,
        "mentioned": False,
        "mentioned_count": 0,
        "likely_used": False,
        "usage_evidence": [],
        "behavior_matches": [],
    }
    if not transcript_text:
        return usage
    text_lower = transcript_text.lower()
    skill_name_lower = skill_name.lower() if skill_name else ""
    for pattern in SKILL_USAGE_PATTERNS:
        matches = list(re.finditer(pattern, text_lower, re.IGNORECASE))
        if matches:
            usage["mentioned"] = True
            usage["mentioned_count"] += len(matches)
            for m in matches[:3]:
                context_start = max(0, m.start() - 30)
                context_end = min(len(transcript_text), m.end() + 30)
                context = transcript_text[context_start:context_end].replace("\n", " ")
                usage["usage_evidence"].append(f"...{context}...")
    if skill_name_lower and skill_name_lower not in {"", "skill"}:
        if re.search(rf"\b{re.escape(skill_name_lower)}\b", text_lower):
            usage["likely_used"] = True
        if skill_name_lower in text_lower:
            usage["mentioned"] = True
            usage["mentioned_count"] += text_lower.count(skill_name_lower)
    return usage


def extract_execution_metrics(stdout_text: str, stderr_text: str) -> dict[str, Any]:
    metrics = {
        "has_errors": False,
        "error_count": 0,
        "has_timeout": False,
        "has_memory_issue": False,
        "has_network_issue": False,
        "stdout_length": len(stdout_text),
        "stderr_length": len(stderr_text),
        "execution_time_seconds": None,
    }
    combined = f"{stdout_text}\n{stderr_text}"
    error_patterns = [
        r"error",
        r"exception",
        r"traceback",
        r"failed",
        r"failure",
        r"fatal",
    ]
    for pattern in error_patterns:
        count = len(re.findall(pattern, combined, re.IGNORECASE))
        if count > 0:
            metrics["has_errors"] = True
            metrics["error_count"] += count
    if "timeout" in combined.lower():
        metrics["has_timeout"] = True
    if (
        "memory" in combined.lower()
        or "out of memory" in combined.lower()
        or "oom" in combined.lower()
    ):
        metrics["has_memory_issue"] = True
    if "connection refused" in combined.lower() or "network" in combined.lower():
        metrics["has_network_issue"] = True
    time_match = re.search(r"execution time:\s*([\d.]+)s", combined, re.IGNORECASE)
    if time_match:
        metrics["execution_time_seconds"] = float(time_match.group(1))
    return metrics


PHASE_PATTERNS = [
    re.compile(r"(?:Phase|阶段)\s*(\d+)[\s:：\-—]+([^\n#]+)", re.IGNORECASE),
    re.compile(r"第([一二三四五六七八九十\d]+)步[\s:：\-—]+([^\n#]+)", re.IGNORECASE),
    re.compile(r"(?:Step)\s*(\d+)[\s:：\-—]+([^\n#]+)", re.IGNORECASE),
]


def extract_phase_structure(skill_md_content: str) -> list[dict[str, str]]:
    """从 SKILL.md 内容中提取 Phase/Step 结构。"""
    phases = []
    for pattern in PHASE_PATTERNS:
        for match in pattern.finditer(skill_md_content):
            num = match.group(1)
            name = match.group(2).strip()[:50]
            if name and len(name) > 2:
                phases.append({"number": str(num), "name": name})
    # 去重
    seen = set()
    unique = []
    for p in phases:
        key = p["number"]
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return sorted(unique, key=lambda x: x["number"])


def check_phase_completion(
    artifacts_dir: Path, phases: list[dict[str, str]], transcript_text: str = ""
) -> dict[str, Any]:
    """基于 skill 实际 Phase 结构检查完成情况。"""
    if not phases:
        return {
            "phase_count": 0,
            "completed_phases": [],
            "missing_phases": [],
            "coverage": 0.0,
        }

    completed = []
    missing = []
    for phase in phases:
        num = phase["number"]
        name = phase["name"]
        # 检查 artifacts 中是否有对应文件
        found = False
        search_names = [
            f"phase{num}-checkpoint",
            f"phase{num}_checkpoint",
            f"phase_{num}_checkpoint",
            f"phase{num}",
            f"step{num}",
            f"step_{num}",
            num,
        ]
        if artifacts_dir.exists():
            for f in artifacts_dir.rglob("*"):
                if not f.is_file():
                    continue
                fname_lower = f.name.lower()
                for sn in search_names:
                    if sn.lower() in fname_lower:
                        found = True
                        break
                if found:
                    break
            # 也检查内容中是否提到该 Phase
            if not found and transcript_text:
                for keyword in [
                    f"Phase {num}",
                    f"phase {num}",
                    f"第{num}步",
                    f"Step {num}",
                ]:
                    if keyword.lower() in transcript_text.lower():
                        found = True
                        break
        if found:
            completed.append(phase)
        else:
            missing.append(phase)

    total = len(phases)
    coverage = len(completed) / total if total > 0 else 0.0
    return {
        "phase_count": total,
        "completed_phases": completed,
        "missing_phases": missing,
        "coverage": round(coverage * 100, 1),
    }


def extract_evidence_chain(
    stdout_text: str, transcript_text: str, skill_phases: list[dict[str, str]] = None
) -> dict[str, Any]:
    combined = f"{stdout_text}\n{transcript_text}"

    # 基于 skill 实际 Phase 结构检查（优先级最高）
    phase_check = None
    if skill_phases:
        phase_check = check_phase_completion(Path(""), skill_phases, combined)

    checkpoints = []
    if skill_phases:
        # 用 skill 实际 Phase 名称做精确匹配
        for phase in skill_phases:
            patterns = [
                f"Phase {phase['number']}",
                f"phase {phase['number']}",
                f"第{phase['number']}步",
                f"Step {phase['number']}",
                phase["name"][:10],
            ]
            for pat in patterns:
                if pat.lower() in combined.lower():
                    idx = combined.lower().index(pat.lower())
                    start = max(0, idx - 10)
                    end = min(len(combined), idx + len(pat) + 50)
                    ctx = combined[start:end].replace("\n", " ").strip()[:100]
                    checkpoints.append(
                        f"Phase {phase['number']}: {phase['name']} — {ctx}"
                    )
                    break
    else:
        # 回退到通用关键词
        for pattern in CHECKPOINT_PATTERNS:
            for match in re.finditer(pattern, combined, re.IGNORECASE):
                start = max(0, match.start() - 20)
                end = min(len(combined), match.end() + 50)
                context = combined[start:end].replace("\n", " ").strip()
                checkpoints.append(context[:100])
                break

    decisions = []
    for pattern in DECISION_PATTERNS:
        for match in re.finditer(pattern, combined, re.IGNORECASE):
            if match.lastindex and match.lastindex >= 1:
                decision = match.group(1).strip()
                if decision and len(decision) < 200:
                    decisions.append(decision)
            else:
                decisions.append(match.group(0).strip()[:100])

    assumptions = []
    for pattern in ASSUMPTION_PATTERNS:
        for match in re.finditer(pattern, combined, re.IGNORECASE):
            if match.lastindex and match.lastindex >= 1:
                assumption = match.group(1).strip()
                if assumption and len(assumption) < 200:
                    assumptions.append(assumption)
            else:
                assumptions.append(match.group(0).strip()[:100])

    error_recoveries = []
    for pattern in ERROR_RECOVERY_PATTERNS:
        matches = list(re.finditer(pattern, combined, re.IGNORECASE))
        if matches:
            for m in matches[:3]:
                start = max(0, m.start() - 20)
                end = min(len(combined), m.end() + 40)
                context = combined[start:end].replace("\n", " ").strip()
                error_recoveries.append(context[:100])

    evidence_score = 0
    # 如果有 Phase 结构，用覆盖率评分（最准确）
    if phase_check and phase_check["phase_count"] > 0:
        evidence_score = int(phase_check["coverage"] * 0.6)  # 覆盖率占 60%
        if checkpoints:
            evidence_score += min(20, len(checkpoints) * 5)
    elif checkpoints:
        evidence_score += min(30, len(checkpoints) * 10)
    if decisions:
        evidence_score += min(25, len(decisions) * 8)
    if assumptions:
        evidence_score += min(20, len(assumptions) * 7)
    if error_recoveries:
        evidence_score += min(25, len(error_recoveries) * 12)

    result = {
        "checkpoints": checkpoints[:10],
        "checkpoint_count": len(checkpoints),
        "decisions": decisions[:10],
        "decision_count": len(decisions),
        "assumptions": assumptions[:10],
        "assumption_count": len(assumptions),
        "error_recoveries": error_recoveries[:5],
        "error_recovery_count": len(error_recoveries),
        "evidence_score": min(100, evidence_score),
        "evidence_completeness": (
            "high"
            if evidence_score >= 70
            else "medium" if evidence_score >= 40 else "low"
        ),
    }
    if phase_check:
        result["phase_coverage"] = phase_check
    return result


def extract_artifact_quality(artifacts_dir: Path) -> dict[str, Any]:
    quality = {
        "total_files": 0,
        "total_size_bytes": 0,
        "max_single_file_bytes": 0,
        "extension_counts": {},
    }
    if not artifacts_dir.exists():
        return quality
    for f in sorted(artifacts_dir.rglob("*")):
        if not f.is_file():
            continue
        quality["total_files"] += 1
        try:
            size = f.stat().st_size
            quality["total_size_bytes"] += size
            quality["max_single_file_bytes"] = max(
                quality["max_single_file_bytes"], size
            )
        except Exception:
            pass
        ext = f.suffix.lower()
        quality["extension_counts"][ext] = quality["extension_counts"].get(ext, 0) + 1
    return quality


def load_experiment_metadata(workspace_dir: Path) -> dict[str, Any]:
    metadata_path = workspace_dir / "experiment-metadata.json"
    if metadata_path.exists():
        return read_json(metadata_path)
    return {}


def aggregate_run(run_dir: Path) -> dict[str, Any]:
    """汇总 run 目录的基本信息，不依赖自动评分"""
    manifest_path = run_dir / "run_manifest.json"
    manifest = read_json(manifest_path)

    results = manifest.get("results", [])
    comparison: dict[str, dict[str, dict[str, Any]]] = {}
    for item in results:
        model = item.get("model", "unknown")
        model_entry = comparison.setdefault(model, {})
        key = "with_skill" if item.get("use_skill") else "without_skill"
        metrics = item.get("execution_metrics", {})
        model_entry[key] = {
            "success": item.get("success", False),
            "status": item.get("status", "unknown"),
            "artifact_count": len(item.get("artifacts", [])),
            "wall_clock_seconds": metrics.get("wall_clock_seconds"),
            "total_tokens": metrics.get("total_tokens"),
        }

    blind_vote_dir = run_dir / "blind_vote"
    blind_review = {
        "completed": False,
        "judge_model_count": 0,
        "paired_model_count": 0,
        "skill_helped_count": 0,
    }
    config_path = blind_vote_dir / "config.json"
    if config_path.exists():
        config = read_json(config_path)
        blind_review["judge_model_count"] = len(config.get("voting_models", []))

    stats_path = blind_vote_dir / "stats.json"
    if stats_path.exists():
        stats = read_json(stats_path)
        model_stats = stats.values() if isinstance(stats, dict) else []
        blind_review["completed"] = True
        blind_review["paired_model_count"] = len(list(model_stats))
        blind_review["skill_helped_count"] = (
            sum(1 for item in stats.values() if item.get("skill_helped"))
            if isinstance(stats, dict)
            else 0
        )

    return {
        "comparison": comparison,
        "result_count": len(results),
        "blind_review": blind_review,
    }


def write_run_summary(run_dir: Path) -> dict[str, Any]:
    """写入运行后汇总分析，包含执行概览和盲评阶段状态。"""
    summary_data = aggregate_run(run_dir)
    write_json(run_dir / "analysis.json", summary_data)

    blind_review = summary_data.get("blind_review", {})
    lines = ["# Skill 评测汇总分析", ""]
    lines.append(f"**结果数量**: {summary_data['result_count']}")
    if blind_review.get("completed"):
        lines.append(
            f"**盲评阶段**: 已完成 | 评委模型: {blind_review.get('judge_model_count', 0)} | "
            f"完成对比的模型: {blind_review.get('paired_model_count', 0)} | "
            f"带 skill 产出更优的模型: {blind_review.get('skill_helped_count', 0)}"
        )
    else:
        lines.append(
            f"**盲评阶段**: 结果缺失或执行失败 | 预期评委模型: {blind_review.get('judge_model_count', 0)}"
        )
    lines.append("")

    if summary_data["comparison"]:
        lines.append("")
        lines.append("## 结论（普通用户视角）")
        lines.append("")
        for model, data in summary_data["comparison"].items():
            with_info = data.get("with_skill") or {}
            without_info = data.get("without_skill") or {}
            if with_info.get("success") and not without_info.get("success"):
                rec = "带 skill 能完成任务，不带失败，建议开启"
            elif without_info.get("success") and not with_info.get("success"):
                rec = "带 skill 反而失败，建议在该模型上关闭"
            elif with_info.get("success") and without_info.get("success"):
                rec = "两种模式都能完成任务，具体质量差异看报告里的评委评价与产出对照"
            else:
                rec = "两种模式都未能完成任务，优先排查执行问题"
            lines.append(f"- **{model}**: {rec}")
        lines.append("")
        lines.append("## 执行概览")
        lines.append("")
        for model, data in summary_data["comparison"].items():
            lines.append(f"### {model}")
            for mode, info in data.items():
                label = "With Skill" if mode == "with_skill" else "Without Skill"
                status_icon = "✅" if info.get("success") else "❌"
                time_str = (
                    f"{info['wall_clock_seconds']:.1f}s"
                    if info.get("wall_clock_seconds")
                    else "N/A"
                )
                token_str = (
                    f"{info['total_tokens']:,}" if info.get("total_tokens") else "N/A"
                )
                lines.append(
                    f"- {label}: {status_icon} {info['status']} | 耗时: {time_str} | Token: {token_str}"
                )
            lines.append("")

    write_text(run_dir / "summary.md", "\n".join(lines))
    return summary_data
