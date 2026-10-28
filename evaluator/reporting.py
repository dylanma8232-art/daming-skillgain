"""
reporting.py - Simplified report generation for qualitative evaluation mode.
Only supports blind_review_eval_only_v1 schema (no scores).
"""
from __future__ import annotations

import html
import json
import logging
import math
import re
import hashlib
import zipfile
import xml.etree.ElementTree as ET
import base64
from pathlib import Path
from typing import Any
from urllib.parse import quote

_RE_SCHEME_LABEL = re.compile(r'方案[A-Z]')

from .analyzer import (
    check_task_completion,
    detect_stuck_state,
    extract_artifact_quality,
    extract_evidence_chain,
    extract_execution_metrics as inspect_execution_metrics,
    extract_phase_structure,
    extract_skill_usage,
    validate_artifact_content,
)
from .io_utils import read_json, write_text
from .plain_language import (
    JARGON_TERMS,
    build_plain_summary,
    verdict_to_plain_language,
    votes_to_plain_language,
)
from .result_files import select_result_files
from .terminology import to_plain_language

LOGGER = logging.getLogger(__name__)


def load_blind_vote_data(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str], dict[str, dict[str, str]]]:
    blind_vote_stats: list[dict[str, Any]] = []
    vote_details: list[dict[str, Any]] = []
    scheme_label_map: dict[str, str] = {}
    scheme_critiques: dict[str, dict[str, str]] = {}
    blind_vote_dir = run_dir / "blind_vote"
    if not blind_vote_dir.exists():
        return blind_vote_stats, vote_details, scheme_label_map, scheme_critiques

    stats_path = blind_vote_dir / "stats.json"
    if stats_path.exists():
        try:
            stats_data = json.loads(stats_path.read_text())
            if isinstance(stats_data, dict):
                for model, stats in stats_data.items():
                    with_votes = int(stats.get("with_votes", stats.get("with_wins", 0)) or 0)
                    without_votes = int(stats.get("without_votes", stats.get("without_wins", 0)) or 0)
                    ties = int(stats.get("ties", 0) or 0)
                    total_votes = int(stats.get("total_votes", with_votes + without_votes + ties) or 0)
                    with_reasons = stats.get("with_reasons", []) or []
                    without_reasons = stats.get("without_reasons", []) or []
                    final_reasons = stats.get("final_reasons", []) or []
                    if not final_reasons:
                        final_reasons = _summarize_vote_reasons(with_reasons, without_reasons)
                    blind_vote_stats.append(
                        {
                            "model": model,
                            "mode": stats.get("mode", "batch_vote"),
                            "with_votes": with_votes,
                            "without_votes": without_votes,
                            "with_wins": with_votes,
                            "without_wins": without_votes,
                            "ties": ties,
                            "total_votes": total_votes,
                            "skill_helped": stats.get("skill_helped"),
                            "key_differences": stats.get("key_differences", []),
                            "final_reasons": final_reasons,
                            "with_reasons": with_reasons,
                            "without_reasons": without_reasons,
                            "with_critiques": stats.get("with_critiques", []),
                            "without_critiques": stats.get("without_critiques", []),
                            "task_profile": stats.get("task_profile", {}),
                            "raw_outputs": stats.get("raw_outputs", []),
                            "num_evaluations": stats.get("num_evaluations", 0),
                        }
                    )
        except Exception as e:
            LOGGER.warning("读取 blind eval stats 失败: %s", e)

    evals_path = blind_vote_dir / "evaluations.json"
    if evals_path.exists():
        try:
            raw_evals = json.loads(evals_path.read_text())
            if isinstance(raw_evals, list):
                vote_details = raw_evals
                for eval_item in raw_evals:
                    if not isinstance(eval_item, dict):
                        continue
                    evaluations = eval_item.get("evaluations", [])
                    if not isinstance(evaluations, list):
                        continue
                    for ev in evaluations:
                        if not isinstance(ev, dict):
                            continue
                        workspace_votes = ev.get("workspace_votes", {})
                        if isinstance(workspace_votes, dict):
                            for label, workspace in workspace_votes.items():
                                if label not in scheme_label_map:
                                    scheme_label_map[label] = workspace
                        workspace_evaluations = ev.get("workspace_evaluations", {})
                        if isinstance(workspace_evaluations, dict):
                            for label, workspace in workspace_evaluations.items():
                                if label not in scheme_label_map:
                                    scheme_label_map[label] = workspace
                        reasons = ev.get("reasons", {})
                        all_evaluations = ev.get("all_evaluations") or ev.get("evaluations_by_label") or ev.get("逐方案评价") or {}
                        overall = ev.get("overall", "")
                        voting_model = eval_item.get("voting_model", "unknown")
                        if isinstance(all_evaluations, dict):
                            for label, reason in all_evaluations.items():
                                if label not in scheme_critiques:
                                    scheme_critiques[label] = {}
                                if str(reason).strip():
                                    scheme_critiques[label][voting_model] = str(reason).strip()
                        if isinstance(reasons, dict):
                            for label, reason in reasons.items():
                                if label not in scheme_critiques:
                                    scheme_critiques[label] = {}
                                scheme_critiques[label][voting_model] = reason
                        if overall:
                            all_labels_in_overall = _RE_SCHEME_LABEL.findall(overall)
                            for label in all_labels_in_overall:
                                if label not in scheme_critiques:
                                    scheme_critiques[label] = {}
                                if voting_model not in scheme_critiques[label]:
                                    pattern = rf"{re.escape(label)}[^，,；;。]*"
                                    match = re.search(pattern, overall)
                                    if match:
                                        scheme_critiques[label][voting_model] = match.group(0).strip()
                                    else:
                                        scheme_critiques[label][voting_model] = overall
                            for label in workspace_votes.keys():
                                if label not in scheme_critiques:
                                    scheme_critiques[label] = {}
                                if voting_model not in scheme_critiques[label]:
                                    scheme_critiques[label][voting_model] = overall
        except Exception as e:
            LOGGER.warning("读取 blind eval 详情失败: %s", e)

    return blind_vote_stats, vote_details, scheme_label_map, scheme_critiques


def _summarize_vote_reasons(with_reasons: list[Any], without_reasons: list[Any]) -> list[str]:
    summary: list[str] = []
    if with_reasons and without_reasons:
        summary.append(f"使用 skill 获票理由：{with_reasons[0]}；不使用 skill 获票理由：{without_reasons[0]}")
    elif with_reasons:
        summary.append(f"评委主要投给使用 skill：{with_reasons[0]}")
    elif without_reasons:
        summary.append(f"评委主要投给不使用 skill：{without_reasons[0]}")
    return summary


def safe_read_text(path: Path | None, default: str = "") -> str:
    if path is None or not path.exists():
        return default
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return default


def safe_read_json(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def clean_console_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\r\n?", "\n", text)
    lines = []
    for line in text.split("\n"):
        line = line.rstrip()
        if len(line) > 500:
            line = line[:500] + "...(truncated)"
        lines.append(line)
    return "\n".join(lines)


def safe_len(obj: Any) -> int:
    if obj is None:
        return 0
    if isinstance(obj, (list, dict, str)):
        return len(obj)
    return 0


def flatten_key_metrics(obj: Any) -> list[Any]:
    if obj is None:
        return []
    if isinstance(obj, dict):
        result = []
        for v in obj.values():
            if isinstance(v, list):
                result.extend(v)
            else:
                result.append(v)
        return result
    if isinstance(obj, list):
        return obj
    return []


def summarize_metrics(data: dict | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    return {
        "insights": safe_len(data.get("insights")),
        "risks": safe_len(data.get("risks")),
        "anomalies": safe_len(data.get("anomalies")),
        "sheets": safe_len(data.get("sheets")),
        "key_metrics": len(flatten_key_metrics(data.get("key_metrics"))),
    }


def _resolve_workspace_file(raw_path: Any, workspace_dir: Path, fallback_name: str) -> Path:
    path = Path(str(raw_path or ""))
    if path.exists():
        return path
    if path.name:
        relocated = workspace_dir / path.name
        if relocated.exists():
            return relocated
    return workspace_dir / fallback_name


def build_result_payload(
    item: dict[str, Any],
    skill_phases: list[dict[str, str]] = None,
    run_dir: Path | None = None,
    task_prompt: str = "",
    target_skill: str = "",
) -> dict[str, Any]:
    workspace_dir = Path(item["workspace_dir"])
    if not workspace_dir.exists() and run_dir is not None:
        relocated = run_dir / "experiments" / workspace_dir.name
        if relocated.exists():
            workspace_dir = relocated
    report_path = workspace_dir / "artifacts" / "report.md"
    metrics_path = workspace_dir / "artifacts" / "metrics.json"
    metadata_path = workspace_dir / "metadata.json"
    stdout_path = _resolve_workspace_file(item.get("stdout_file"), workspace_dir, "stdout.txt")
    stderr_path = _resolve_workspace_file(item.get("stderr_file"), workspace_dir, "stderr.txt")
    transcript_path = _resolve_workspace_file(item.get("transcript_file"), workspace_dir, "transcript.md")
    artifacts_dir = workspace_dir / "artifacts"

    report_text = safe_read_text(report_path)
    stdout_text = clean_console_text(safe_read_text(stdout_path))
    stderr_text = clean_console_text(safe_read_text(stderr_path))
    transcript_text = safe_read_text(transcript_path)
    metrics_data = safe_read_json(metrics_path)
    metadata = safe_read_json(metadata_path) or {}
    metrics_summary = summarize_metrics(metrics_data)
    insight_count = safe_len(metrics_data.get("insights")) if isinstance(metrics_data, dict) else 0
    risk_count = safe_len(metrics_data.get("risks")) if isinstance(metrics_data, dict) else 0
    anomaly_count = safe_len(metrics_data.get("anomalies")) if isinstance(metrics_data, dict) else 0
    sheet_count = safe_len(metrics_data.get("sheets")) if isinstance(metrics_data, dict) else 0
    key_metric_count = len(flatten_key_metrics(metrics_data.get("key_metrics"))) if isinstance(metrics_data, dict) else 0
    target_skill = target_skill or str(metadata.get("target_skill", "") or "")

    artifact_validation = validate_artifact_content(artifacts_dir)
    completion_info = check_task_completion(stdout_text, transcript_text)
    stuck_info = detect_stuck_state(stdout_text, transcript_text)
    skill_usage = extract_skill_usage(transcript_text, target_skill)
    execution_diagnostics = inspect_execution_metrics(stdout_text, stderr_text)
    execution_evidence = extract_evidence_chain(stdout_text, transcript_text, skill_phases or [])
    artifact_quality = extract_artifact_quality(artifacts_dir)
    artifact_quality["total_size"] = artifact_quality.get("total_size_bytes", 0)
    preflight = metadata.get("preflight", {}) if isinstance(metadata, dict) else {}

    artifact_files = []
    if artifacts_dir.exists():
        for file_path in sorted(artifacts_dir.rglob("*")):
            if not file_path.is_file():
                continue
            rel_path = str(file_path.relative_to(artifacts_dir))
            try:
                suffix = file_path.suffix.lower()
                is_binary = suffix in {
                    ".xlsx", ".xls", ".csv", ".pdf", ".png", ".jpg", ".jpeg",
                    ".gif", ".bmp", ".docx", ".doc", ".pptx", ".ppt",
                }
                if is_binary:
                    size = file_path.stat().st_size
                    artifact_files.append({
                        "name": file_path.name,
                        "path": rel_path,
                        "size": size,
                        "type": "binary",
                    })
                else:
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="replace")
                        size = len(content.encode("utf-8"))
                        if size > 500 * 1024:
                            content = content[:200 * 1024] + "\n...（文件过大已截断）"
                        artifact_files.append({
                            "name": file_path.name,
                            "path": rel_path,
                            "size": size,
                            "type": "text",
                            "preview": content[:2000],
                        })
                    except Exception:
                        artifact_files.append({
                            "name": file_path.name,
                            "path": rel_path,
                            "size": file_path.stat().st_size,
                            "type": "binary",
                        })
            except Exception:
                continue

    result_selection = select_result_files(
        workspace_dir,
        task_prompt=task_prompt,
        target_skill=target_skill,
    )

    anonymous_id = item.get("anonymous_id", "")

    return {
        "model": item.get("model", ""),
        "use_skill": item.get("use_skill", False),
        "status": item.get("status", ""),
        "success": item.get("success", False),
        "failure_reason": item.get("failure_reason", ""),
        "workspace_name": item.get("workspace_name", workspace_dir.name),
        "workspace_dir": str(workspace_dir),
        "run_dir": str(run_dir) if run_dir else "",
        "artifacts": [
            {"name": a["name"], "path": a["path"]}
            for a in artifact_files
        ],
        "artifact_files": artifact_files,
        "result_selection": result_selection,
        "result_files": result_selection.get("validated_result_files", []),
        "evidence_files": result_selection.get("evidence_files", []),
        "report_text": report_text,
        "metrics_summary": metrics_summary,
        "insight_count": insight_count,
        "risk_count": risk_count,
        "anomaly_count": anomaly_count,
        "sheet_count": sheet_count,
        "key_metric_count": key_metric_count,
        "artifact_validation": artifact_validation,
        "completion_info": completion_info,
        "stuck_info": stuck_info,
        "skill_usage": skill_usage,
        "execution_diagnostics": execution_diagnostics,
        "execution_evidence": execution_evidence,
        "artifact_quality": artifact_quality,
        "execution_metrics": item.get("execution_metrics", {}),
        "anonymous_id": anonymous_id,
    }


def assign_anonymous_ids(results: list[dict], run_dir: str) -> None:
    """Assign anonymous IDs to results for blind review."""
    run_path = Path(run_dir)
    anon_map_path = run_path / "anonymous_ids.json"
    if anon_map_path.exists():
        try:
            anon_map = json.loads(anon_map_path.read_text())
            for item in results:
                model = item.get("model", "")
                use_skill = item.get("use_skill", False)
                key = f"{model}:{'with' if use_skill else 'without'}"
                if key in anon_map:
                    item["anonymous_id"] = anon_map[key]
                else:
                    anon_id = hashlib.md5(key.encode()).hexdigest()[:6].upper()
                    item["anonymous_id"] = anon_id
                    anon_map[key] = anon_id
            anon_map_path.write_text(json.dumps(anon_map, ensure_ascii=False))
            return
        except Exception:
            pass

    anon_map: dict[str, str] = {}
    for item in results:
        model = item.get("model", "")
        use_skill = item.get("use_skill", False)
        key = f"{model}:{'with' if use_skill else 'without'}"
        if key not in anon_map:
            anon_map[key] = hashlib.md5(key.encode()).hexdigest()[:6].upper()
        item["anonymous_id"] = anon_map[key]

    try:
        anon_map_path.write_text(json.dumps(anon_map, ensure_ascii=False))
    except Exception:
        pass


def build_group_payload(
    model: str,
    with_skill: dict[str, Any] | None,
    without_skill: dict[str, Any] | None,
    blind_vote_stat: dict[str, Any] | None = None,
    workspace_to_label: dict[str, str] | None = None,
    workspace_to_critiques: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    stat = blind_vote_stat or {}
    with_votes = int(stat.get("with_votes", stat.get("with_wins", 0)) or 0)
    without_votes = int(stat.get("without_votes", stat.get("without_wins", 0)) or 0)
    ties = int(stat.get("ties", 0) or 0)
    total_votes = int(stat.get("total_votes", with_votes + without_votes + ties) or 0)
    with_label = ""
    without_label = ""
    if workspace_to_label:
        if with_skill:
            ws_name = Path(str((with_skill or {}).get("workspace_dir") or "")).name
            with_label = workspace_to_label.get(ws_name, "")
        if without_skill:
            ws_name = Path(str((without_skill or {}).get("workspace_dir") or "")).name
            without_label = workspace_to_label.get(ws_name, "")
    with_reviews: list[str] = []
    without_reviews: list[str] = []
    if workspace_to_critiques:
        if with_skill:
            ws_name = Path(str((with_skill or {}).get("workspace_dir") or "")).name
            with_reviews = list(workspace_to_critiques.get(ws_name, []))
        if without_skill:
            ws_name = Path(str((without_skill or {}).get("workspace_dir") or "")).name
            without_reviews = list(workspace_to_critiques.get(ws_name, []))
    return {
        "model": model,
        "with_skill": with_skill,
        "without_skill": without_skill,
        "blind_vote": stat,
        "mode": stat.get("mode", "batch_vote"),
        "with_votes": with_votes,
        "without_votes": without_votes,
        "with_wins": with_votes,
        "without_wins": without_votes,
        "ties": ties,
        "total_votes": total_votes,
        "final_reasons": stat.get("final_reasons", []),
        "key_differences": stat.get("key_differences", []),
        "with_reasons": stat.get("with_reasons", []),
        "without_reasons": stat.get("without_reasons", []),
        "with_scheme_label": with_label,
        "without_scheme_label": without_label,
        "with_judge_reviews": with_reviews,
        "without_judge_reviews": without_reviews,
    }


def _load_insight_data(run_dir: Path) -> dict[str, Any]:
    """读取洞察结论数据"""
    insight_path = run_dir / "blind_vote" / "summary.json"
    if insight_path.exists():
        try:
            return json.loads(insight_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def build_report_payload(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    results = [
        build_result_payload(
            item,
            [],
            run_dir,
            task_prompt=str(manifest.get("task_prompt", "") or ""),
            target_skill=str(manifest.get("target_skill", "") or ""),
        )
        for item in manifest.get("results", [])
    ]
    assign_anonymous_ids(results, str(run_dir))
    blind_vote_stats, vote_details, scheme_label_map, scheme_critiques = load_blind_vote_data(run_dir)
    blind_vote_map = {item.get("model"): item for item in blind_vote_stats}

    workspace_to_label: dict[str, str] = {}
    for label, workspace in scheme_label_map.items():
        ws_path = Path(workspace)
        ws_name = ws_path.name
        workspace_to_label[ws_name] = label
    workspace_to_critiques: dict[str, list[str]] = {}
    for label, critiques in scheme_critiques.items():
        workspace = scheme_label_map.get(label)
        if not workspace:
            continue
        ws_name = Path(workspace).name
        values = [
            str(reason).strip()
            for _judge, reason in sorted(critiques.items())
            if str(reason).strip()
        ]
        if values:
            workspace_to_critiques[ws_name] = _dedupe_nonempty(values, 3)

    model_groups: dict[str, dict[str, Any]] = {}
    for item in results:
        model = item["model"]
        pair = model_groups.setdefault(model, {"with_skill": None, "without_skill": None})
        if item["use_skill"]:
            pair["with_skill"] = item
        else:
            pair["without_skill"] = item

    grouped_results = [
        build_group_payload(
            model,
            pair["with_skill"],
            pair["without_skill"],
            blind_vote_map.get(model),
            workspace_to_label,
            workspace_to_critiques,
        )
        for model, pair in sorted(model_groups.items())
    ]

    tested_models = [group["model"] for group in grouped_results]
    judge_models = sorted(
        {
            str(item.get("voting_model") or "").strip()
            for item in vote_details
            if isinstance(item, dict) and str(item.get("voting_model") or "").strip()
        }
    )

    with_skill_winners = [
        g for g in grouped_results
        if g.get("with_votes", 0) > g.get("without_votes", 0)
    ]
    without_skill_winners = [
        g for g in grouped_results
        if g.get("without_votes", 0) > g.get("with_votes", 0)
    ]
    with_skill_best = max(grouped_results, key=lambda g: g.get("with_votes", 0), default=None)
    without_skill_best = max(grouped_results, key=lambda g: g.get("without_votes", 0), default=None)

    # 纪律 5：统计指标与不确定性 (样本量 N、配对差值 Δ、方差、标准差、分布、小样本告警)
    valid_pairs = []
    paired_deltas = []
    invalid_runs_count = 0

    for model, pair in sorted(model_groups.items()):
        with_item = pair["with_skill"]
        without_item = pair["without_skill"]
        with_invalid = bool(with_item and (not with_item.get("is_valid", True) or with_item.get("status") == "invalid"))
        without_invalid = bool(without_item and (not without_item.get("is_valid", True) or without_item.get("status") == "invalid"))
        if with_invalid:
            invalid_runs_count += 1
        if without_invalid:
            invalid_runs_count += 1

        # 基础设施错误 (invalid) 不计入能力评测分母；只有双侧非 invalid 时才进入能力对比
        if with_item and without_item and (not with_invalid) and (not without_invalid):
            stat = blind_vote_map.get(model, {})
            with_v = int(stat.get("with_votes", stat.get("with_wins", 0)) or 0)
            without_v = int(stat.get("without_votes", stat.get("without_wins", 0)) or 0)
            delta = with_v - without_v
            paired_deltas.append(delta)
            valid_pairs.append({
                "model": model,
                "with_votes": with_v,
                "without_votes": without_v,
                "delta": delta,
            })

    N = len(valid_pairs)
    if N > 0:
        mean_delta = sum(paired_deltas) / N
        variance = (sum((d - mean_delta) ** 2 for d in paired_deltas) / (N - 1)) if N > 1 else 0.0
        std_dev = math.sqrt(variance)
    else:
        mean_delta = 0.0
        variance = 0.0
        std_dev = 0.0

    small_sample_warning = ""
    if N < 5:
        small_sample_warning = f"⚠️ 样本量不足（有效配对样本量 N={N} < 5），统计指标仅供参考，可能存在较高偶然性。"

    statistics = {
        "sample_size_total": len(model_groups),
        "sample_size_valid": N,
        "invalid_runs_count": invalid_runs_count,
        "paired_deltas": paired_deltas,
        "mean_delta": round(mean_delta, 3),
        "variance": round(variance, 3),
        "std_dev": round(std_dev, 3),
        "distribution": {
            "with_wins": sum(1 for d in paired_deltas if d > 0),
            "without_wins": sum(1 for d in paired_deltas if d < 0),
            "ties": sum(1 for d in paired_deltas if d == 0),
        },
        "small_sample_warning": small_sample_warning,
    }

    # 读取断言快照
    snapshot_path = run_dir / "assertions_snapshot.json"
    assertions_snapshot = None
    if snapshot_path.exists():
        try:
            assertions_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # P1: 读取同模型配对增益 payload
    p1_payload = _load_p1_payload(run_dir)

    # P2: 宿主适用边界数据（结论仅适用于已测宿主，禁止跨宿主外推）
    tested_hosts = sorted({
        str(item.get("runner_name") or "").strip()
        for item in manifest.get("results", [])
        if isinstance(item, dict) and str(item.get("runner_name") or "").strip()
    })
    config_host = ""
    config_snapshot_path = run_dir / "config.snapshot.json"
    if config_snapshot_path.exists():
        try:
            config_host = str(json.loads(config_snapshot_path.read_text(encoding="utf-8")).get("host") or "")
        except Exception:
            config_host = ""
    if not tested_hosts and config_host:
        tested_hosts = [config_host]
    host_boundary = {
        "tested_hosts": tested_hosts,
        "statement": to_plain_language(
            "这份报告是用 " + "、".join(tested_hosts) + " 测出来的。"
            "同一个 skill 在别的 AI 工具（如 Claude Code、Cursor、Trae 等）里表现可能不一样，"
            "想知道在别的工具里行不行，就在那个工具上再测一次。"
        ) if tested_hosts else to_plain_language("本次运行没有记录用的是什么 AI 工具，无法确认结论适用于哪个工具。"),
    }

    return {
        "evaluation_name": manifest.get("evaluation_name") or run_dir.name,
        "host_boundary": host_boundary,
        "target_skill": manifest.get("target_skill", ""),
        "task_prompt": manifest.get("task_prompt", ""),
        "run_dir": str(run_dir),
        "grouped_results": grouped_results,
        "blind_vote_stats": blind_vote_stats,
        "vote_details": vote_details,
        "tested_models": tested_models,
        "judge_models": judge_models,
        "summary": {
            "model_count": len(grouped_results),
            "with_skill_wins": len(with_skill_winners),
            "without_skill_wins": len(without_skill_winners),
            "ties": len(grouped_results) - len(with_skill_winners) - len(without_skill_winners),
        },
        "with_skill_best": with_skill_best if with_skill_best and with_skill_best.get("with_votes", 0) > 0 else None,
        "without_skill_best": without_skill_best if without_skill_best and without_skill_best.get("without_votes", 0) > 0 else None,
        "insight": _load_insight_data(run_dir),
        "statistics": statistics,
        "assertions_snapshot": assertions_snapshot,
        "p1": p1_payload,
    }



def _compact(text: Any, limit: int = 200) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _list_text(values: Any, limit: int = 3) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_compact(v, 260) for v in values[:limit] if _compact(v, 260)]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _format_seconds(value: Any) -> str:
    try:
        seconds = float(value or 0)
    except Exception:
        return "耗时未知"
    if seconds <= 0:
        return "耗时未知"
    if seconds >= 60:
        return f"{seconds / 60:.1f}m"
    return f"{seconds:.0f}s"


def _format_tokens(value: Any) -> str:
    tokens = _safe_int(value)
    if tokens <= 0:
        return "tokens未知"
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k tokens"
    return f"{tokens} tokens"


def _artifact_abs_path(item: dict[str, Any] | None, artifact: dict[str, Any]) -> Path:
    workspace = Path(str((item or {}).get("workspace_dir") or ""))
    return workspace / "artifacts" / str(artifact.get("path") or artifact.get("name") or "")


def _artifact_lookup(item: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for artifact in list((item or {}).get("artifact_files") or []):
        path = str(artifact.get("path") or artifact.get("name") or "")
        if path:
            lookup[path] = artifact
    return lookup


def _hydrate_artifact(item: dict[str, Any] | None, artifact: dict[str, Any]) -> dict[str, Any]:
    path = str(artifact.get("path") or artifact.get("name") or "")
    base = dict(_artifact_lookup(item).get(path, {}))
    base.update(artifact)
    if "name" not in base and path:
        base["name"] = Path(path).name
    return base


def _artifact_href(item: dict[str, Any] | None, artifact: dict[str, Any], use_data_uri: bool = False) -> str:
    path = _artifact_abs_path(item, artifact)
    run_dir = Path(str((item or {}).get("run_dir") or ""))

    def quote_path(value: str) -> str:
        return quote(value.replace("\\", "/"), safe="/:#?&=%+~@!$,;'()*[]")

    try:
        if use_data_uri and path.exists() and path.is_file():
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            ext = path.suffix.lower()
            mime_map = {
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".xls": "application/vnd.ms-excel",
                ".csv": "text/csv",
                ".md": "text/markdown",
                ".txt": "text/plain",
                ".json": "application/json",
                ".html": "text/html",
                ".htm": "text/html",
            }
            mime = mime_map.get(ext, "application/octet-stream")
            return f"data:{mime};base64,{data}"
    except Exception:
        pass

    try:
        if run_dir and path.exists():
            return html.escape(quote_path(str(path.relative_to(run_dir))))
    except Exception:
        pass
    return html.escape(quote_path(str(path)))


def _artifact_kind(path: Path, artifact: dict[str, Any]) -> str:
    ext = path.suffix.lower()
    if ext in {".xlsx", ".xls", ".csv"}:
        return "spreadsheet"
    if ext in {".pptx", ".ppt"}:
        return "presentation"
    if ext in {".html", ".htm"}:
        return "html"
    if ext in {".md", ".markdown", ".txt", ".json", ".py", ".js", ".ts", ".tsx", ".sql", ".yaml", ".yml"}:
        return "text"
    if str(artifact.get("type")) == "text":
        return "text"
    return "binary"


def _artifact_priority(artifact: dict[str, Any]) -> tuple[int, int, str]:
    name = str(artifact.get("name") or "").lower()
    path = str(artifact.get("path") or "").lower()
    text = f"{path}/{name}"
    suffix = Path(name).suffix.lower()
    score = 100
    if any(k in text for k in ("result_selection", "result_manifest", "summary", "execution_trace", "stdout", "stderr", "metadata", "metrics", "preflight")):
        score += 120
    if any(k in text for k in ("final", "result", "deliverable", "output")):
        score -= 40
    if any(k in text for k in ("report", "analysis", "presentation", "dashboard")):
        score -= 30
    if suffix in {".pptx", ".xlsx", ".html", ".pdf"}:
        score -= 20
    if suffix in {".md", ".json", ".csv"}:
        score -= 10
    if any(k in text for k in ("trace", "log", "stderr", "stdout", "metadata", "metrics", "notes")):
        score += 40
    size = -_safe_int(artifact.get("size"))
    return (score, size, name)


def _split_artifacts(item: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    artifacts = list((item or {}).get("artifact_files") or [])
    if not artifacts:
        artifacts = list((item or {}).get("artifacts") or [])
    if not artifacts:
        return None, []
    artifacts = sorted(artifacts, key=_artifact_priority)
    return artifacts[0], artifacts[1:]


def _read_text_preview(path: Path, artifact: dict[str, Any], limit: int = 6000) -> str:
    preview = artifact.get("preview")
    if isinstance(preview, str) and preview.strip():
        return preview[:limit]
    return safe_read_text(path)[:limit]


def _xlsx_preview(path: Path, max_rows: int = 8, max_cols: int = 8) -> list[list[str]]:
    if not path.exists():
        return []
    try:
        with zipfile.ZipFile(path) as zf:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in root.iter():
                    if si.tag.endswith("}si") or si.tag == "si":
                        parts = [node.text or "" for node in si.iter() if node.tag.endswith("}t") or node.tag == "t"]
                        shared.append("".join(parts))
            sheet_name = next((n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")), "")
            if not sheet_name:
                return []
            root = ET.fromstring(zf.read(sheet_name))
            rows: list[list[str]] = []
            for row in root.iter():
                if not (row.tag.endswith("}row") or row.tag == "row"):
                    continue
                values: list[str] = []
                for cell in list(row)[:max_cols]:
                    if not (cell.tag.endswith("}c") or cell.tag == "c"):
                        continue
                    cell_type = cell.attrib.get("t", "")
                    raw = ""
                    for child in cell.iter():
                        if child.tag.endswith("}v") or child.tag == "v":
                            raw = child.text or ""
                            break
                        if child.tag.endswith("}t") or child.tag == "t":
                            raw = child.text or ""
                            break
                    if cell_type == "s" and raw.isdigit() and int(raw) < len(shared):
                        raw = shared[int(raw)]
                    values.append(raw)
                if values:
                    rows.append(values)
                if len(rows) >= max_rows:
                    break
            return rows
    except Exception:
        return []


def _csv_preview(text: str, max_rows: int = 8, max_cols: int = 8) -> list[list[str]]:
    rows = []
    for line in text.splitlines()[:max_rows]:
        if not line.strip():
            continue
        if "," in line:
            values = [v.strip() for v in line.split(",")]
        elif "|" in line:
            values = [v.strip() for v in line.split("|")]
        else:
            values = [line.strip()]
        rows.append(values[:max_cols])
    return rows


def _pptx_preview(path: Path, max_slides: int = 6) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with zipfile.ZipFile(path) as zf:
            slide_names = sorted(
                [n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")],
                key=lambda n: int(re.search(r"slide(\d+)\.xml", n).group(1)) if re.search(r"slide(\d+)\.xml", n) else 0,
            )
            slides: list[dict[str, str]] = []
            for idx, name in enumerate(slide_names[:max_slides], 1):
                root = ET.fromstring(zf.read(name))
                texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t") or node.tag == "t"]
                title = next((t for t in texts if t.strip()), f"第 {idx} 页")
                body = " ".join(t.strip() for t in texts[1:] if t.strip())[:180]
                slides.append({"title": title, "body": body or "（此页未提取到正文文本）"})
            return slides
    except Exception:
        return []


def _render_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    header = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    def cells(values: list[str], tag: str) -> str:
        padded = values + [""] * (width - len(values))
        return "".join(f"<{tag}>{html.escape(str(v))}</{tag}>" for v in padded)
    head_html = f"<thead><tr>{cells(header, 'th')}</tr></thead>"
    body_html = "".join(f"<tr>{cells(row, 'td')}</tr>" for row in body)
    return f"<div class='sheet-wrap'><table class='sheet'>{head_html}<tbody>{body_html}</tbody></table></div>"


def _render_slide_deck(slides: list[dict[str, str]]) -> str:
    if not slides:
        return ""
    slide_html = []
    total = len(slides)
    for idx, slide in enumerate(slides, 1):
        active = " active" if idx == 1 else ""
        slide_html.append(
            f"""
            <section class='deck-slide{active}' data-slide-index='{idx - 1}'>
              <div class='slide-page-number'>{idx} / {total}</div>
              <h4>{html.escape(slide['title'])}</h4>
              <p>{html.escape(slide['body'])}</p>
            </section>
            """
        )
    return f"""
      <div class='slide-deck' data-slide-deck>
        <div class='deck-stage'>
          {''.join(slide_html)}
        </div>
        <div class='deck-controls'>
          <button type='button' class='deck-btn' data-slide-prev aria-label='上一页幻灯片'>上一页</button>
          <span class='deck-counter' data-slide-counter>1 / {total}</span>
          <button type='button' class='deck-btn' data-slide-next aria-label='下一页幻灯片'>下一页</button>
        </div>
      </div>
    """


def _render_artifact_preview(item: dict[str, Any] | None, artifact: dict[str, Any], primary: bool = False) -> str:
    path = _artifact_abs_path(item, artifact)
    kind = _artifact_kind(path, artifact)
    href = _artifact_href(item, artifact, use_data_uri=primary)
    name = artifact.get("path") or artifact.get("name") or path.name or "artifact"
    tag = "结果文件" if primary else "文件"
    kind_label = {
        "spreadsheet": "表格预览",
        "presentation": "幻灯片预览",
        "html": "HTML 预览",
        "text": "文本预览",
        "binary": "文件预览",
    }.get(kind, "文件预览")
    title = f"""
      <div class='artifact-title'>
        <div class='file-main'><span class='tag {'result' if primary else 'file'}'>{tag}</span><strong>{html.escape(str(name))}</strong></div>
        <div class='file-actions'><span class='tag file'>{kind_label}</span><a class='download' href='{href}' download title='下载 {html.escape(str(name))}' aria-label='下载 {html.escape(str(name))}'>下载</a></div>
      </div>
    """
    body = ""
    if kind == "spreadsheet":
        rows = _xlsx_preview(path) if path.suffix.lower() in {".xlsx", ".xls"} else _csv_preview(_read_text_preview(path, artifact, 4000))
        note = "展示表格核心区域，完整文件可下载审计。" if primary else "该表格属于过程证据，默认收起，可下载复核。"
        body = f"<p class='result-note'>{note}</p>" + (_render_table(rows) or "<pre>[表格文件，无法直接提取预览，请下载查看]</pre>")
    elif kind == "presentation":
        slides = _pptx_preview(path)
        if slides:
            note = "按页展示幻灯片文本摘要，完整文件可下载查看。" if primary else "该幻灯片属于过程证据，默认收起，可下载复核。"
            body = f"<p class='result-note'>{note}</p>" + _render_slide_deck(slides)
        else:
            note = "幻灯片文件暂无法提取页面文本，完整文件可下载查看。" if primary else "该幻灯片属于过程证据，默认收起，可下载复核。"
            body = f"<p class='result-note'>{note}</p>"
    else:
        if not primary and kind == "binary":
            body = "<p class='result-note'>该文件为二进制过程证据，默认不展开内容，可下载复核。</p>"
        else:
            text = _read_text_preview(path, artifact, 7000)
            body = f"<pre>{html.escape(text or '[无可预览文本，请下载查看]')}</pre>"
    cls = "artifact result-file" if primary else "artifact"
    return f"<div class='{cls}'>{title}{body}</div>"


def _render_artifact_stack(item: dict[str, Any] | None) -> str:
    result_files = [_hydrate_artifact(item, art) for art in list((item or {}).get("result_files") or [])]
    result_paths = {str(art.get("path") or art.get("name") or "") for art in result_files}

    missing_message = ""
    if not result_files:
        selection = (item or {}).get("result_selection") or {}
        missing = selection.get("missing_required_outputs") or []
        if missing:
            lines = "\n".join(f"- {m.get('label', '结果文件')} {m.get('ext', '')}: {m.get('reason', '')}" for m in missing)
            missing_message = f"<div class='artifact result-file'><div class='artifact-title'><div class='file-main'><span class='tag result'>结果文件</span><strong>未交付要求的结果文件</strong></div></div><pre>{html.escape(lines)}</pre></div>"
        if selection:
            parts = []
            if missing_message:
                parts.append(missing_message)
            evidence_files = list((item or {}).get("evidence_files") or [])
            if evidence_files:
                others = [_hydrate_artifact(item, art) for art in evidence_files]
                other_names = "、".join(html.escape(str(a.get("name") or a.get("path") or "artifact")) for a in others[:4])
                more = "" if len(others) <= 4 else f" 等 {len(others)} 个"
                inner = "".join(_render_artifact_preview(item, art, primary=False) for art in others)
                parts.append(f"<details class='secondary-files'><summary>过程证据 {len(others)} 个：{other_names}{more}</summary><div class='secondary-inner'>{inner}</div></details>")
            if not parts:
                parts.append("<p class='empty-artifacts'>未发现可展示的最终结果文件或过程证据。</p>")
            return "<div class='artifact-stack'>" + "".join(parts) + "</div>"
        primary, fallback_others = _split_artifacts(item)
        if not primary:
            parts = []
            if missing_message:
                parts.append(missing_message)
            evidence_files = list((item or {}).get("evidence_files") or [])
            if evidence_files:
                others = [_hydrate_artifact(item, art) for art in evidence_files]
                other_names = "、".join(html.escape(str(a.get("name") or a.get("path") or "artifact")) for a in others[:4])
                more = "" if len(others) <= 4 else f" 等 {len(others)} 个"
                inner = "".join(_render_artifact_preview(item, art, primary=False) for art in others)
                parts.append(f"<details class='secondary-files'><summary>过程证据 {len(others)} 个：{other_names}{more}</summary><div class='secondary-inner'>{inner}</div></details>")
            return "<div class='artifact-stack'>" + "".join(parts) + "</div>"
        result_files = [primary]
        result_paths = {str(primary.get("path") or primary.get("name") or "")}
        others = fallback_others
    else:
        others = []

    evidence_paths = {str(art.get("path") or art.get("name") or "") for art in list((item or {}).get("evidence_files") or [])}
    for artifact in list((item or {}).get("artifact_files") or []):
        path = str(artifact.get("path") or artifact.get("name") or "")
        if path and path not in result_paths:
            others.append(artifact)
    if evidence_paths:
        others = sorted(others, key=lambda art: (0 if str(art.get("path") or art.get("name") or "") in evidence_paths else 1, str(art.get("path") or art.get("name") or "")))

    parts = []
    if missing_message:
        parts.append(missing_message)
    parts.extend([_render_artifact_preview(item, artifact, primary=True) for artifact in result_files])
    if others:
        other_names = "、".join(html.escape(str(a.get("name") or a.get("path") or "artifact")) for a in others[:4])
        more = "" if len(others) <= 4 else f" 等 {len(others)} 个"
        inner = "".join(_render_artifact_preview(item, art, primary=False) for art in others)
        parts.append(f"<details class='secondary-files'><summary>过程证据 {len(others)} 个：{other_names}{more}</summary><div class='secondary-inner'>{inner}</div></details>")
    return "<div class='artifact-stack'>" + "".join(parts) + "</div>"


def _render_diagnostics(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    metrics = item.get("execution_metrics") or {}
    tokens = metrics.get("total_tokens")
    wall = metrics.get("wall_clock_seconds")
    count = len(item.get("artifact_files") or item.get("artifacts") or [])
    bits = [
        f"状态 {html.escape(str(item.get('status') or 'unknown'))}",
        f"{count} 文件",
        _format_seconds(wall),
        _format_tokens(tokens),
    ]
    return "<div class='diagnostics'>" + "".join(f"<span>{html.escape(bit)}</span>" for bit in bits) + "</div>"


def _mode_reason(group: dict[str, Any], use_skill: bool) -> str:
    reasons = _list_text(group.get("with_reasons" if use_skill else "without_reasons"), 2)
    if reasons:
        return "；".join(reasons)
    reviews = _list_text(group.get("with_judge_reviews" if use_skill else "without_judge_reviews"), 2)
    if reviews:
        return "；".join(reviews)
    final_reasons = _list_text(group.get("final_reasons"), 1)
    if final_reasons:
        return final_reasons[0]
    return "暂无评委理由。"


def _fallback_mode_review(group: dict[str, Any], use_skill: bool) -> str:
    item = group.get("with_skill" if use_skill else "without_skill") or {}
    selection = item.get("result_selection") or {}
    missing = selection.get("missing_required_outputs") or []
    status = str(item.get("status") or "unknown")
    failure = str(item.get("failure_reason") or "").strip()
    votes = _safe_int(group.get("with_votes" if use_skill else "without_votes"))
    if missing:
        missing_bits = "；".join(
            f"{m.get('label', '结果文件')} {m.get('ext', '')} 未交付"
            for m in missing[:2]
        )
        return f"评委未给出单独获票评价；该方案{missing_bits}，按评测规则应视为未满足交付要求。"
    if status not in {"completed", "ok", "success"}:
        detail = f"，原因：{failure}" if failure else ""
        return f"评委未给出单独评价；该方案执行状态为 {status}{detail}，需要优先复核产物是否可验收。"
    if votes <= 0:
        return "评委未给该方案投票，因此没有单独获票理由；报告保留该说明，表示它在匿名对比中未进入评委优选集合。"
    return "评委未返回该方案的逐项文字评价；请结合票数、总体评价和下方实际产物复核。"


def _mode_review_text(group: dict[str, Any], use_skill: bool) -> str:
    reviews = _list_text(group.get("with_judge_reviews" if use_skill else "without_judge_reviews"), 2)
    reasons = _list_text(group.get("with_reasons" if use_skill else "without_reasons"), 2)
    if reviews:
        return "；".join(reviews)
    if reasons:
        return "；".join(reasons)
    return _fallback_mode_review(group, use_skill)


def _group_verdict(group: dict[str, Any]) -> tuple[str, str]:
    with_votes = group.get("with_votes", 0)
    without_votes = group.get("without_votes", 0)
    if with_votes > without_votes:
        return "开 Skill 更好", "green"
    if without_votes > with_votes:
        return "不开 Skill 更好", "blue"
    return "接近 / 难判断", "gray"


def _dedupe_nonempty(values: list[Any], limit: int = 3) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _compact(str(value or "").strip(), 220)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _report_css() -> str:
    return r'''
    :root { --bg:#f8fafc; --panel:#fff; --ink:#1e293b; --muted:#64748b; --line:#e2e8f0; --primary:#2563eb; --green:#10b981; --green-soft:#ecfdf5; --green-border:#a7f3d0; --blue:#3b82f6; --blue-soft:#eff6ff; --blue-border:#bfdbfe; --amber:#f59e0b; --amber-soft:#fffbeb; --amber-border:#fde68a; --code:#f1f5f9; --shadow-sm:0 1px 2px rgb(0 0 0 / .05); --shadow:0 4px 6px -1px rgb(0 0 0 / .1),0 2px 4px -2px rgb(0 0 0 / .1); --shadow-md:0 10px 15px -3px rgb(0 0 0 / .1),0 4px 6px -4px rgb(0 0 0 / .1); --radius:12px; }
    *{box-sizing:border-box;-webkit-font-smoothing:antialiased} body{margin:0;background:var(--bg);color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.6;overflow-x:hidden}.page{width:100%;max-width:1280px;margin:0 auto;padding:clamp(16px,3vw,40px);overflow-x:hidden}
    .hero{background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%);color:#fff;border-radius:var(--radius);padding:32px;margin-bottom:24px;box-shadow:var(--shadow-md);position:relative;overflow:hidden}.hero::before{content:"";position:absolute;top:0;right:0;width:300px;height:300px;background:radial-gradient(circle,rgba(37,99,235,.15) 0%,transparent 70%);pointer-events:none}.hero-top{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;position:relative;z-index:1}h1{font-size:28px;font-weight:800;margin:0 0 8px;letter-spacing:0}.hero p{color:#94a3b8;font-size:15px;max-width:800px;margin:0}.verdict-badge{background:var(--green);color:#fff;border-radius:99px;padding:8px 18px;font-size:14px;font-weight:700;box-shadow:0 4px 12px rgba(16,185,129,.3);white-space:nowrap}.meta-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:28px;position:relative;z-index:1}.meta{background:rgba(255,255,255,.05);backdrop-filter:blur(4px);border:1px solid rgba(255,255,255,.1);border-radius:10px;padding:14px}.meta span{display:block;color:#94a3b8;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0;margin-bottom:6px}.meta strong{display:block;color:#f1f5f9;font-size:14px;word-break:break-word}
    .section{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:24px;margin-bottom:24px;box-shadow:var(--shadow-sm)}.section-head{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:20px;border-bottom:1px solid var(--line);padding-bottom:12px}.section h2{margin:0;font-size:18px;font-weight:700;color:var(--ink);display:flex;align-items:center;gap:8px}.section-note{color:var(--muted);font-size:13px;font-weight:500}.icon{width:16px;height:16px;flex-shrink:0}
    .kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.kpi{border:1px solid var(--line);border-radius:10px;padding:18px;background:#fff}.kpi.good{background:var(--green-soft);border-color:var(--green-border)}.kpi.blue{background:var(--blue-soft);border-color:var(--blue-border)}.kpi.warn{background:var(--amber-soft);border-color:var(--amber-border)}.kpi span{display:block;color:var(--muted);font-size:12px;font-weight:700;margin-bottom:6px}.kpi strong{display:block;font-size:24px;font-weight:800;color:var(--ink);line-height:1.1}.kpi p{margin:10px 0 0;color:var(--muted);font-size:13px;line-height:1.5}.vote-bar{display:flex;height:36px;overflow:hidden;border-radius:99px;border:4px solid #fff;box-shadow:var(--shadow-sm);margin-top:20px}.vote-bar div{display:flex;align-items:center;justify-content:center;color:#fff;font-size:13px;font-weight:700;min-width:0;text-align:center;line-height:1.15;padding:0 4px;overflow:hidden}.vote-with{background:var(--green)}.vote-without{background:var(--blue)}
    .verdict-panel{margin-top:22px;border:1px solid var(--line);border-radius:10px;background:#fff;overflow:hidden}.verdict-copy{padding:18px 20px;background:#f8fafc;border-bottom:1px solid var(--line)}.verdict-header-row{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.verdict-text{flex:1}.verdict-vote-cards{display:flex;align-items:flex-start}.vote-card-combined{background:#fff;border:1px solid var(--line);border-radius:8px;padding:10px 14px}.vote-card-combined-top{display:flex;align-items:center;gap:8px}.vote-card-item{display:flex;flex-direction:column;align-items:center;min-width:70px}.vote-card-item.green .vote-card-label{color:#047857}.vote-card-item.blue .vote-card-label{color:#1d4ed8}.vote-card-label{font-size:11px;font-weight:700}.vote-card-votes{font-size:18px;font-weight:800;color:var(--ink)}.vote-card-pct{font-size:11px;color:#64748b}.vote-divider{font-size:18px;color:#cbd5e1;font-weight:300}.verdict-proof{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0;border-bottom:1px solid var(--line)}.verdict-summary{margin-top:20px}.verdict-proof.compact{border-bottom:0}.verdict-proof div{padding:16px 20px}.verdict-proof div+div{border-left:1px solid var(--line)}.verdict-proof strong{display:block;margin-bottom:8px;font-size:13px;font-weight:800;color:var(--ink)}.verdict-proof p{margin:0;color:#475569;font-size:13px;line-height:1.65}.judge-quote-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;padding:16px}.judge-quotes{border-radius:8px;border:1px solid var(--line);padding:14px;background:#fff}.judge-quotes.support{background:var(--green-soft);border-color:var(--green-border)}.judge-quotes.counter{background:var(--blue-soft);border-color:var(--blue-border)}.judge-quotes.overall{background:var(--amber-soft);border-color:var(--amber-border)}.judge-quotes strong{display:block;margin-bottom:8px;font-size:13px;font-weight:800;color:var(--ink)}.judge-quotes ul{margin:0;padding-left:18px;color:#475569;font-size:13px;line-height:1.6}.judge-quotes li+li{margin-top:6px}rgin:0;padding-left:18px;color:#475569;font-size:13px;line-height:1.6}.judge-quotes li+li{margin-top:6px}
    .best-grid,.judge-grid,.evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(400px,100%),1fr));gap:20px}.judge-grid{grid-template-columns:repeat(3,1fr)}.evidence-grid{grid-template-columns:repeat(4,1fr)}.result-card,.judge-card,.evidence{border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff}.result-card:hover,.judge-card:hover{box-shadow:var(--shadow)}.result-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:16px;background:#f8fafc;border-bottom:1px solid var(--line);flex-wrap:wrap}.result-head h3{margin:0 0 4px;font-size:16px;font-weight:700;display:flex;align-items:center;gap:8px;flex-wrap:wrap}.result-head small{display:block;color:var(--muted);font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.result-body{padding:16px}.pill,.tag{display:inline-flex;align-items:center;border-radius:99px;padding:4px 12px;font-size:12px;font-weight:700;white-space:nowrap}.pill.green{background:var(--green-soft);color:#047857;border:1px solid var(--green-border)}.pill.blue{background:var(--blue-soft);color:#1d4ed8;border:1px solid var(--blue-border)}.pill.amber{background:var(--amber-soft);color:#b45309;border:1px solid var(--amber-border)}.pill.gray{background:#f1f5f9;color:#475569;border:1px solid var(--line)}.tag{padding:2px 8px;font-size:11px}.tag.result{background:#dcfce7;color:#166534;border:1px solid #bbf7d0}.tag.file{background:#f1f5f9;color:#475569;border:1px solid var(--line)}
    .reason-list{margin:0 0 16px;padding:0;list-style:none;display:grid;gap:10px}.reason-list li{padding:10px 12px;background:#f8fafc;border-radius:8px;border-left:4px solid var(--line);color:#334155;font-size:14px}.reason-list li.good{border-color:var(--green);background:var(--green-soft)}.reason-list li.blue{border-color:var(--blue);background:var(--blue-soft)}.reason-list li.warn{border-color:var(--amber);background:var(--amber-soft)}
    .artifact-stack{display:grid;gap:12px}.artifact{border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff;min-width:0}.artifact.result-file{border-color:var(--green-border);box-shadow:0 0 0 3px var(--green-soft)}.artifact-title{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 14px;background:#fff;border-bottom:1px solid var(--line);font-size:13px;font-weight:700}.file-main{display:flex;align-items:center;gap:10px;flex:1;min-width:0}.file-main strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.file-actions{display:flex;align-items:center;gap:8px;white-space:nowrap}.download{background:#fff;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:4px 10px;font-size:12px;font-weight:700;text-decoration:none}.download:hover{background:var(--primary);color:#fff;border-color:var(--primary)}.result-note{margin:0;padding:10px 14px;background:#fff;color:var(--muted);font-size:13px;border-bottom:1px dashed var(--line)}.empty-artifacts{margin:0;padding:14px;border:1px dashed var(--line);border-radius:8px;color:var(--muted);font-size:13px;background:#f8fafc}
    .sheet-wrap{overflow:auto;background:#fff;border-radius:0 0 8px 8px}.sheet{width:100%;border-collapse:collapse;font-size:13px}.sheet th,.sheet td{border:1px solid var(--line);padding:10px 12px;text-align:left}.sheet th{background:#f8fafc;font-weight:700;color:var(--muted)}.sheet td:first-child{background:#f8fafc;font-weight:600;color:var(--ink)}.slide-deck{background:#f1f5f9;padding:16px}.deck-stage{position:relative;min-height:260px}.deck-slide{display:none;aspect-ratio:16/9;background:#fff;border:1px solid var(--line);border-radius:8px;padding:20px;box-shadow:var(--shadow-sm);position:relative;overflow:auto}.deck-slide.active{display:flex;flex-direction:column;justify-content:center}.deck-slide h4{margin:0 0 14px;font-size:18px;color:var(--ink);line-height:1.35}.deck-slide p{margin:0;color:#475569;font-size:14px;line-height:1.7;white-space:pre-wrap}.slide-page-number{position:absolute;right:14px;top:10px;color:var(--muted);font-size:12px;font-weight:700}.deck-controls{display:flex;align-items:center;justify-content:center;gap:12px;margin-top:12px}.deck-btn{appearance:none;background:#fff;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 12px;font-size:12px;font-weight:700;cursor:pointer}.deck-btn:hover:not(:disabled){background:var(--primary);color:#fff;border-color:var(--primary)}.deck-btn:disabled{opacity:.45;cursor:not-allowed}.deck-counter{font-size:12px;font-weight:700;color:var(--muted);min-width:52px;text-align:center}pre{margin:0;padding:14px;background:#1e293b;color:#e2e8f0;font-family:ui-monospace,monospace;font-size:13px;line-height:1.6;max-height:300px;overflow:auto;white-space:pre-wrap;word-break:break-word}
    details.secondary-files{border:1px solid var(--line);border-radius:10px;background:#f8fafc;overflow:hidden}details.secondary-files summary{cursor:pointer;padding:12px 16px;font-size:13px;font-weight:700;color:var(--ink);list-style:none;display:flex;justify-content:space-between;align-items:center}details.secondary-files summary::-webkit-details-marker{display:none}details.secondary-files summary:hover{background:#f1f5f9}details.secondary-files summary::after{content:"＋";font-size:16px;color:var(--muted)}details.secondary-files[open] summary::after{content:"－"}.secondary-inner{display:grid;gap:12px;padding:12px;border-top:1px solid var(--line)}
    .model-card{border:1px solid var(--line);border-radius:var(--radius);background:#fff;margin-bottom:24px;overflow:hidden;box-shadow:var(--shadow-sm)}.model-head{display:flex;justify-content:space-between;align-items:center;padding:16px 20px;background:#f8fafc;border-bottom:1px solid var(--line)}.model-head h3{margin:0;font-size:18px;font-weight:800;color:var(--ink)}.model-body{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.side{padding:20px;min-width:0}.side+.side{border-left:1px solid var(--line);background:#fcfdfe}.side-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.side-head h4{margin:0;font-size:15px;font-weight:700;color:var(--muted);display:flex;align-items:center;gap:8px}.scheme-icon{background:var(--primary);color:#fff;border-radius:6px;padding:2px 8px;font-size:11px;font-weight:700;white-space:nowrap}.scheme-icon.muted{background:#e2e8f0;color:#475569}.small-reason{background:#f8fafc;border:1px solid var(--line);border-radius:8px;padding:12px;color:#475569;font-size:13px;margin-bottom:16px;line-height:1.5}.diagnostics{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px;justify-content:flex-end}.diagnostics span{background:#fff;border:1px solid var(--line);border-radius:99px;padding:2px 10px;font-size:11px;font-weight:600;color:var(--muted)}.judge-card,.evidence{padding:16px}.judge-card h3,.evidence strong{display:block;margin:0 0 10px;font-size:15px;font-weight:700;color:var(--ink)}.judge-card p,.evidence span{color:#475569;font-size:13px;line-height:1.6}
    .insight-panel{background:#fff;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}.insight-overall{padding:20px;background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%);color:#fff}.insight-overall h3{margin:0 0 8px;font-size:18px;font-weight:800}.insight-overall p{margin:0;color:#94a3b8;font-size:14px}.insight-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:0}.insight-item{padding:16px 20px;border-top:1px solid var(--line)}.insight-item+.insight-item{border-left:1px solid var(--line)}.insight-item h4{margin:0 0 10px;font-size:13px;font-weight:700;color:var(--muted)}.insight-item ul{margin:0;padding-left:18px;color:#475569;font-size:13px}.insight-item li+li{margin-top:6px}.insight-item.suggestion{grid-column:1/-1;background:#f8fafc}.insight-item.suggestion p{margin:0;color:#334155;font-size:14px}
    .model-recommendations{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;padding:12px;background:#f8fafc;border-radius:8px;border:1px solid var(--line)}.model-rec-item{display:flex;align-items:center;gap:8px;font-size:13px}.model-rec-item span:first-child{flex:1;min-width:120px}
    .plain-summary{background:linear-gradient(135deg,#ecfdf5 0%,#f0fdf4 100%);border:2px solid var(--green-border);border-radius:10px;padding:18px 22px}.plain-summary-title{font-size:14px;font-weight:800;color:#047857;margin-bottom:8px}.plain-summary-text{margin:0;font-size:16px;line-height:1.8;color:#064e3b;font-weight:600}
    .plain-verdict-row{border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:12px;background:#fff}.plain-verdict-model{font-size:15px;font-weight:800;margin-bottom:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}.plain-verdict-text{font-size:14px;line-height:1.7;color:#334155;background:var(--amber-soft);border:1px solid var(--amber-border);border-left:4px solid var(--amber);border-radius:8px;padding:12px 14px}.plain-votes{display:block;margin-top:8px;color:#475569;font-size:13px}
    .compare-card{border:1px solid var(--line);border-radius:10px;margin-bottom:20px;overflow:hidden;background:#fff}.compare-head{padding:12px 16px;background:#f8fafc;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:12px}.compare-head h3{margin:0;font-size:16px;font-weight:800}.model-result-head{border-bottom:1px solid var(--line)}.model-votes-line{padding:10px 16px 0;font-size:14px;line-height:1.7;color:#334155}.model-votes-line strong{color:var(--ink)}.model-result .plain-note{margin:10px 16px 0}.compare-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));margin-top:10px}.model-result .compare-side{padding:14px;min-width:0}.model-result .compare-side+.compare-side{border-left:1px solid var(--line);background:#fcfdfe}.model-tech{border-top:1px solid var(--line);padding:8px 16px 12px}.model-tech details.tech-details{margin-top:0}.model-tech .tech-details-body{white-space:pre-wrap}.process-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:10px}.process-side strong{display:block;font-size:12px;font-weight:700;color:var(--muted);margin-bottom:6px}.process-side details.secondary-files{background:#fff}@media(max-width:768px){.model-result .compare-grid,.process-grid{grid-template-columns:1fr}.model-result .compare-side+.compare-side{border-left:0;border-top:1px solid var(--line)}}.compare-preview{border:1px solid var(--line);border-radius:8px;overflow:hidden}.compare-preview.empty{padding:16px;color:var(--muted);font-size:13px;background:#f8fafc}.compare-preview-title{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 12px;background:#fff;border-bottom:1px solid var(--line);font-size:13px}.compare-preview-title strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}pre.delivery-preview{margin:0;padding:14px;background:#f8fafc;color:#1e293b;font-family:ui-monospace,monospace;font-size:13px;line-height:1.6;max-height:420px;overflow:auto;white-space:pre-wrap;word-break:break-word}
    details.tech-section{border:1px solid var(--line);border-radius:var(--radius);background:#fff;margin-bottom:24px;overflow:hidden}details.tech-section>summary{cursor:pointer;padding:16px 20px;font-size:15px;font-weight:800;color:var(--ink);list-style:none;display:flex;justify-content:space-between;align-items:center}details.tech-section>summary::-webkit-details-marker{display:none}details.tech-section>summary::after{content:"＋";font-size:18px;color:var(--muted)}details.tech-section[open]>summary::after{content:"－"}details.tech-section>summary:hover{background:#f8fafc}.tech-section-body{padding:0 16px 16px}
    details.tech-details{margin-top:10px}details.tech-details>summary{cursor:pointer;font-size:12px;font-weight:700;color:var(--primary);list-style:none}details.tech-details>summary::-webkit-details-marker{display:none}details.tech-details>summary::before{content:"▸ ";color:var(--muted)}details.tech-details[open]>summary::before{content:"▾ "}details.tech-details>summary:hover{text-decoration:underline}.tech-details-body{margin-top:6px;padding:10px 12px;background:#f8fafc;border:1px solid var(--line);border-radius:8px;color:#475569;font-size:12px;line-height:1.6;white-space:pre-wrap}.plain-note{margin:8px 0 0;font-size:14px;line-height:1.7;color:#78350f;background:var(--amber-soft);border:1px solid var(--amber-border);border-left:4px solid var(--amber);border-radius:8px;padding:10px 14px}.verdict-with-votes{display:grid;grid-template-columns:1fr auto;gap:16px;align-items:start}@media(max-width:900px){.verdict-with-votes{grid-template-columns:1fr}.vote-cards-side{flex-direction:row}}.vote-cards-side{display:flex;flex-direction:column;gap:8px}.vote-card{border-radius:8px;padding:12px 16px;text-align:center;min-width:90px}.vote-card.green{background:var(--green-soft);border:1px solid var(--green-border)}.vote-card.blue{background:var(--blue-soft);border:1px solid var(--blue-border)}.vote-card-head{font-size:11px;font-weight:700;color:#475569;margin-bottom:2px}.vote-card-votes{font-size:20px;font-weight:800;color:var(--ink)}.vote-card-pct{font-size:11px;color:#64748b}
    @media(max-width:1024px){.judge-grid,.evidence-grid,.judge-quote-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:768px){.hero-top{flex-direction:column;align-items:stretch}.meta-grid{grid-template-columns:1fr}.best-grid,.judge-grid,.evidence-grid,.model-body,.verdict-proof,.judge-quote-grid{grid-template-columns:1fr}.verdict-proof div+div{border-left:0;border-top:1px solid var(--line)}.side+.side{border-left:0;border-top:1px solid var(--line)}.section-head{flex-direction:column;align-items:flex-start;gap:8px}.artifact-title{align-items:flex-start;flex-direction:column}.file-main,.file-actions{width:100%}.file-main strong{white-space:normal;overflow-wrap:anywhere}.file-actions{justify-content:space-between}.diagnostics{justify-content:flex-start}.verdict-badge{white-space:normal;max-width:100%;overflow-wrap:anywhere}.hero,.section,.result-card,.model-card{min-width:0}.vote-bar{height:44px}.vote-bar div{font-size:12px;white-space:normal;overflow-wrap:anywhere}.insight-grid{grid-template-columns:1fr}.insight-item+.insight-item{border-left:0;border-top:1px solid var(--line)}}}
    '''


def _report_js() -> str:
    return r'''
    document.querySelectorAll('[data-slide-deck]').forEach((deck) => {
      const slides = Array.from(deck.querySelectorAll('.deck-slide'));
      const prev = deck.querySelector('[data-slide-prev]');
      const next = deck.querySelector('[data-slide-next]');
      const counter = deck.querySelector('[data-slide-counter]');
      let index = 0;

      function render() {
        slides.forEach((slide, idx) => slide.classList.toggle('active', idx === index));
        if (counter) counter.textContent = `${index + 1} / ${slides.length}`;
        if (prev) prev.disabled = index === 0;
        if (next) next.disabled = index >= slides.length - 1;
      }

      if (prev) prev.addEventListener('click', () => {
        index = Math.max(0, index - 1);
        render();
      });
      if (next) next.addEventListener('click', () => {
        index = Math.min(slides.length - 1, index + 1);
        render();
      });
      render();
    });
    '''


def _load_p1_payload(run_dir: Path) -> dict[str, Any] | None:
    """读取 P1 配对增益 payload（p1/paired_gain.json）；不存在时返回 None。"""
    p1_path = run_dir / "p1" / "paired_gain.json"
    if not p1_path.exists():
        return None
    try:
        return json.loads(p1_path.read_text(encoding="utf-8"))
    except Exception as e:
        LOGGER.warning("读取 P1 配对增益数据失败: %s", e)
        return None


def _verdict_pill(verdict: str) -> str:
    """结论胶囊标签（确定性颜色映射，零 LLM）"""
    mapping = {
        "skill_effective": ("明确有效", "green"),
        "negative_migration": ("明确有害 · 建议关闭", "red"),
        "inconclusive": ("无法判定", "gray"),
    }
    label, cls = mapping.get(verdict, (verdict, "gray"))
    return f"<span class='pill {cls}'>{html.escape(label)}</span>"


def _render_plain_summary_section(payload: dict[str, Any]) -> str:
    """首屏一：总结（三句话以内说清这次评测结果，零 LLM、确定性拼接）"""
    p1 = payload.get("p1") or {}
    summary_text = build_plain_summary(p1.get("pair_gains") or [], payload.get("grouped_results") or [])
    return f"""
      <div class='plain-summary'>
        <div class='plain-summary-title'>💡 总结</div>
        <p class='plain-summary-text'>{html.escape(summary_text)}</p>
      </div>
    """


def _extract_scheme_evals(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    """从 insight summary / evaluations 提取各匿名方案（方案A/B/…）的评委评价。"""
    scheme_evals: dict[str, dict[str, str]] = {}
    vote_details = payload.get("vote_details") or []
    insight = payload.get("insight") or {}
    scheme_summaries = insight.get("scheme_summaries") or {}
    if scheme_summaries:
        for scheme, info in scheme_summaries.items():
            if isinstance(info, dict):
                summary_text = info.get("summary", "")
                scheme_evals[scheme] = {"short": summary_text, "full": summary_text}
    else:
        # 兜底：直接从 evaluations.json 读取（兼容旧数据）
        for vd in vote_details:
            for ev in vd.get("evaluations", []):
                reasons = ev.get("reasons") or {}
                all_ev = ev.get("all_evaluations") or {}
                for scheme, reason in reasons.items():
                    if scheme not in scheme_evals:
                        scheme_evals[scheme] = {"short": "", "full": ""}
                    if reason and not scheme_evals[scheme]["short"]:
                        scheme_evals[scheme]["short"] = str(reason)
                for scheme, eval_text in all_ev.items():
                    if scheme not in scheme_evals:
                        scheme_evals[scheme] = {"short": "", "full": ""}
                    if not scheme_evals[scheme]["full"]:
                        scheme_evals[scheme]["full"] = str(eval_text)
    return scheme_evals


def _side_judge_eval_html(group: dict[str, Any], use_skill: bool, scheme_evals: dict[str, dict[str, str]] | None) -> str:
    """单侧评委评价（优先匿名方案聚合评价，兜底投票理由；始终给出说明，不丢信息）。"""
    scheme_label = group.get("with_scheme_label" if use_skill else "without_scheme_label", "")
    judge_eval = ""
    if scheme_label and scheme_evals and scheme_label in scheme_evals:
        eval_info = scheme_evals[scheme_label]
        short = eval_info.get("short", "")
        full = eval_info.get("full", "")
        if short:
            judge_eval = html.escape(short)
        elif full:
            judge_eval = html.escape(full[:150]) + "..." if len(full) > 150 else html.escape(full)
    review_text = _mode_review_text(group, use_skill)
    if not judge_eval and review_text and "评委" not in review_text:
        judge_eval = html.escape(review_text)
    if not judge_eval:
        judge_eval = html.escape(review_text)
    if not judge_eval:
        return ""
    return f"<div class='small-reason'><strong>评委评价：</strong>{judge_eval}</div>"


def _render_model_side(group: dict[str, Any], use_skill: bool, scheme_evals: dict[str, dict[str, str]] | None) -> str:
    """按模型区块内的单侧（带 / 不带 Skill）：评委评价 + 主交付物预览 + 诊断。"""
    item = group.get("with_skill" if use_skill else "without_skill")
    cls = "green" if use_skill else "blue"
    label = "带 Skill" if use_skill else "不带 Skill"
    judge_html = _side_judge_eval_html(group, use_skill, scheme_evals)
    preview = _render_primary_delivery_preview(item)
    diag = _render_diagnostics(item)
    return f"""
      <div class='compare-side'>
        <div class='compare-side-head'><span class='pill {cls}'>{label}</span></div>
        {judge_html}
        {preview}
        {diag}
      </div>
    """


def _process_evidence_artifacts(item: dict[str, Any] | None) -> list[dict[str, Any]]:
    """取该侧主交付物之外的全部过程证据（artifact_files，证据文件优先排序）。"""
    if not item:
        return []
    result_paths = {
        str(a.get("path") or a.get("name") or "")
        for a in list(item.get("result_files") or [])
    }
    if not result_paths:
        primary, _rest = _split_artifacts(item)
        if primary:
            result_paths = {str(primary.get("path") or primary.get("name") or "")}
    evidence_paths = {
        str(a.get("path") or a.get("name") or "")
        for a in list(item.get("evidence_files") or [])
    }
    others = []
    for artifact in list(item.get("artifact_files") or []):
        path = str(artifact.get("path") or artifact.get("name") or "")
        if path and path not in result_paths:
            others.append(artifact)
    if evidence_paths:
        others.sort(key=lambda art: (
            0 if str(art.get("path") or art.get("name") or "") in evidence_paths else 1,
            str(art.get("path") or art.get("name") or ""),
        ))
    return others


def _render_process_evidence(group: dict[str, Any]) -> str:
    """按模型「技术细节」里的其余过程证据（主交付物之外，默认收起，不重复展示主交付物）。"""
    parts: list[str] = []
    for use_skill in (True, False):
        item = group.get("with_skill" if use_skill else "without_skill")
        others = _process_evidence_artifacts(item)
        if not others:
            continue
        label = "带 Skill" if use_skill else "不带 Skill"
        other_names = "、".join(
            html.escape(str(a.get("name") or a.get("path") or "artifact")) for a in others[:4]
        )
        more = "" if len(others) <= 4 else f" 等 {len(others)} 个"
        inner = "".join(_render_artifact_preview(item, art, primary=False) for art in others)
        parts.append(
            f"<div class='process-side'><strong>{label}</strong>"
            f"<details class='secondary-files'><summary>过程证据 {len(others)} 个：{other_names}{more}</summary>"
            f"<div class='secondary-inner'>{inner}</div></details></div>"
        )
    if not parts:
        return ""
    return "<div class='process-grid'>" + "".join(parts) + "</div>"


def _render_model_result_sections(payload: dict[str, Any]) -> str:
    """③ 按模型看结果：每个模型一个完整区块（结论 + 票数 + 评委评价 + 双栏产物预览 + 诊断 + 技术折叠）。

    这是报告唯一的产物展示层：主交付物默认展开（含 base64 下载），
    过程证据与统计口径收进「▸ 技术细节」，不再单独渲染模型排行 artifact_stack。
    """
    groups = payload.get("grouped_results", []) or []
    if not groups:
        return "<p class='small-reason'>暂无模型结果。</p>"
    p1 = payload.get("p1") or {}
    pair_gains = {str(pg.get("model", "")): pg for pg in (p1.get("pair_gains") or [])}
    scheme_evals = _extract_scheme_evals(payload)

    blocks: list[str] = []
    for group in groups:
        model = html.escape(str(group.get("model") or "unknown"))
        pg = pair_gains.get(str(group.get("model") or ""))

        # 结论 pill：优先用 P1 配对增益 verdict，无 P1 时按票数推断
        if pg:
            verdict_pill = _verdict_pill(str(pg.get("verdict", "inconclusive")))
        else:
            verdict_text, verdict_cls = _group_verdict(group)
            verdict_pill = f"<span class='pill {verdict_cls}'>{html.escape(verdict_text)}</span>"

        # 评委打分（白话票数 + 评委原话引用）
        votes_plain = votes_to_plain_language(
            int(group.get("with_votes", 0) or 0),
            int(group.get("without_votes", 0) or 0),
            int(group.get("ties", 0) or 0),
            list(group.get("final_reasons") or []),
        )

        # 一句话解读（P1 verdict 的确定性翻译；无 P1 数据时省略）
        plain_html = ""
        if pg:
            plain = verdict_to_plain_language(
                str(pg.get("verdict", "inconclusive")),
                model=str(pg.get("model", "")),
                n=int(pg.get("n_paired", 0)),
                mean_delta=float(pg.get("mean_delta", 0.0)),
                ci_low=float(pg.get("ci_low", 0.0)),
                ci_high=float(pg.get("ci_high", 0.0)),
                primary_metric=str(pg.get("primary_metric") or "assertion"),
            )
            plain_html = f"<div class='plain-note'>💡 解读：{html.escape(plain)}</div>"

        # 技术细节：统计口径（平均分差 / 波动范围 / 轮次）+ 判定依据
        tech_bits: list[str] = []
        if pg:
            if pg.get("reasons"):
                tech_bits.append("判定依据：" + "；".join(str(r) for r in pg["reasons"]))
            tech_bits.append(
                f"平均分差 {float(pg.get('mean_delta', 0.0)):+.2f}，"
                f"结果波动范围 [{float(pg.get('ci_low', 0.0)):+.2f}, {float(pg.get('ci_high', 0.0)):+.2f}]，"
                f"对比 {int(pg.get('n_paired', 0))} 轮"
            )
        tech_text = html.escape("\n".join(tech_bits)) if tech_bits else "本轮无配对增益统计（缺少有效配对数据）。"

        process_html = _render_process_evidence(group)
        tech_body = f"<div class='tech-details-body'>{tech_text}</div>" + process_html

        blocks.append(f"""
          <article class='compare-card model-result'>
            <div class='compare-head model-result-head'><h3>{model}</h3>{verdict_pill}</div>
            <div class='model-votes-line'><strong>评委打分：</strong>{html.escape(votes_plain)}</div>
            {plain_html}
            <div class='compare-grid'>
              {_render_model_side(group, True, scheme_evals)}
              {_render_model_side(group, False, scheme_evals)}
            </div>
            <div class='model-tech'><details class='tech-details'><summary>▸ 技术细节</summary>{tech_body}</details></div>
          </article>
        """)
    return "".join(blocks)


def _render_primary_delivery_preview(item: dict[str, Any] | None, max_lines: int = 40) -> str:
    """渲染单侧主交付物正文预览（result_files[0] 的 content 前 N 行）。

    带完整文件 base64 下载链接（沿用 _artifact_href 机制）。没有产出时给出明确提示。
    """
    if not item:
        return "<div class='compare-preview empty'>该侧没有可展示的产出（执行失败或未交付）。</div>"
    result_files = [_hydrate_artifact(item, art) for art in list(item.get("result_files") or [])]
    if not result_files:
        primary, _ = _split_artifacts(item)
        if not primary:
            selection = item.get("result_selection") or {}
            missing = selection.get("missing_required_outputs") or []
            if missing:
                lines = "；".join(f"{m.get('label', '结果文件')} {m.get('ext', '')} 未交付" for m in missing[:2])
                return f"<div class='compare-preview empty'>未交付要求的结果文件：{html.escape(lines)}</div>"
            return "<div class='compare-preview empty'>未发现可展示的最终结果文件。</div>"
        result_files = [primary]
    artifact = result_files[0]
    path = _artifact_abs_path(item, artifact)
    kind = _artifact_kind(path, artifact)
    name = str(artifact.get("name") or artifact.get("path") or path.name or "artifact")

    body = ""
    if kind == "spreadsheet":
        rows = _xlsx_preview(path) if path.suffix.lower() in {".xlsx", ".xls"} else _csv_preview(_read_text_preview(path, artifact, 4000))
        body = (_render_table(rows) or "<pre>[表格文件，无法直接提取预览，请下载查看]</pre>")
    elif kind == "presentation":
        slides = _pptx_preview(path)
        body = _render_slide_deck(slides) if slides else "<p class='result-note'>幻灯片文件暂无法提取页面文本，可下载查看。</p>"
    else:
        text = _read_text_preview(path, artifact, 20000)
        lines = text.splitlines()
        truncated = len(lines) > max_lines or len(text) >= 20000
        shown = "\n".join(lines[:max_lines])
        note = "（正文已截断，下载后可查看全部内容）" if truncated else ""
        body = f"<pre class='delivery-preview'>{html.escape(shown or '[文件为空或无法读取，请下载查看]')}</pre>"
        if note:
            body += f"<p class='result-note'>{note}</p>"

    href = _artifact_href(item, artifact, use_data_uri=True)
    return f"""
      <div class='compare-preview'>
        <div class='compare-preview-title'><strong>{html.escape(name)}</strong><a class='download' href='{href}' download title='下载 {html.escape(name)}'>查看完整文件</a></div>
        {body}
      </div>
    """


def _render_p1_negative_alerts(p1: dict[str, Any] | None) -> str:
    """P1 首屏二：负迁移警报（醒目标注「该模型上建议关闭 skill」+ 解读）。

    无负迁移时返回空串，由调用方决定是否渲染该区块（「如有」才展示）。
    """
    if not p1:
        return ""
    negatives = [pg for pg in (p1.get("pair_gains", []) or []) if pg.get("negative_migration")]
    if not negatives:
        return ""
    alerts = []
    for pg in negatives:
        model = html.escape(str(pg.get("model", "?")))
        plain = verdict_to_plain_language(
            "negative_migration",
            model=str(pg.get("model", "")),
            n=int(pg.get("n_paired", 0)),
            mean_delta=float(pg.get("mean_delta", 0.0)),
            primary_metric=str(pg.get("primary_metric") or "assertion"),
        )
        alerts.append(f"""
        <div style='background:#fef2f2;border:2px solid #dc2626;border-radius:10px;padding:16px;margin-bottom:12px;'>
          <strong style='color:#dc2626;font-size:15px;'>🚨 负迁移警报：{model}</strong>
          <p style='margin:8px 0 0;font-size:14px;'><strong>建议在该模型上关闭此 skill。</strong></p>
          <div class='plain-note'>💡 解读：{html.escape(plain)}</div>
          <details class='tech-details'><summary>技术细节</summary><div class='tech-details-body'>{html.escape('；'.join(pg.get('reasons', [])))}</div></details>
        </div>""")
    return "".join(alerts)


def _render_p1_pair_details(p1: dict[str, Any] | None) -> str:
    """P1 首屏三：配对明细（每模型每重复轮次的配对差值）+ 评委一致率 + 人审触发"""
    if not p1:
        return ""
    rows = []
    for pg in (p1.get("pair_gains", []) or []):
        model = html.escape(str(pg.get("model", "?")))
        for d in (pg.get("deltas", []) or []):
            rows.append(
                f"<tr><td>{model}</td><td>rep{html.escape(str(d.get('repetition', '?')))}</td>"
                f"<td>{float(d.get('with_value', 0.0)):.1f}</td><td>{float(d.get('without_value', 0.0)):.1f}</td>"
                f"<td class='{'pos' if float(d.get('delta', 0)) > 0 else ('neg' if float(d.get('delta', 0)) < 0 else '')}'><strong>{float(d.get('delta', 0.0)):+.2f}</strong></td></tr>"
            )
    table_html = ""
    if rows:
        table_html = (
            "<table style='width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px;'>"
            "<tr style='text-align:left;border-bottom:2px solid #e5e7eb;'><th>模型</th><th>重复轮次</th><th>With</th><th>Without</th><th>配对 Δ</th></tr>"
            + "".join(rows) + "</table>"
        )
    else:
        table_html = "<p class='small-reason'>无配对明细（with/without 重复轮次无交集或均无效）。</p>"

    # 评委一致率
    ja = p1.get("judge_agreement", {}) or {}
    rate = ja.get("overall_agreement_rate")
    rate_str = f"{float(rate):.1%}" if rate is not None else "无可比对数据"
    per_round_rows = []
    for r in (ja.get("per_round", []) or []):
        r_rate = r.get("agreement_rate")
        per_round_rows.append(
            f"<tr><td>{html.escape(str(r.get('model', '?')))}</td><td>rep{html.escape(str(r.get('repetition', 1)))}</td>"
            f"<td>round {r.get('round', 0)}</td><td>{r.get('judge_count', 0)}</td>"
            f"<td>{r.get('agree_pairs', 0)}/{r.get('comparable_pairs', 0)}</td>"
            f"<td>{f'{float(r_rate):.0%}' if r_rate is not None else '—'}</td></tr>"
        )
    agreement_html = ""
    if per_round_rows:
        agreement_html = (
            f"<p><strong>评委一致率（口径：同一配对内评委同判定比例）：</strong>整体均值 {rate_str}"
            f"（同判定 {ja.get('overall_agree_pairs', 0)} / 总可比 {ja.get('overall_comparable_pairs', 0)}）</p>"
            "<table style='width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px;'>"
            "<tr style='text-align:left;border-bottom:2px solid #e5e7eb;'><th>模型</th><th>重复</th><th>轮次</th><th>评委数</th><th>同判定/总可比</th><th>一致率</th></tr>"
            + "".join(per_round_rows) + "</table>"
        )
    else:
        agreement_html = f"<p class='small-reason'>评委一致率：{rate_str}（无逐轮可比数据）。</p>"

    # 人审触发记录
    trigger_rows = []
    for t in (p1.get("human_review_triggers", []) or []):
        for trig in (t.get("triggers", []) or []):
            trigger_rows.append(
                f"<div style='background:#fffbeb;border:1px solid #f59e0b;border-radius:8px;padding:10px;margin-bottom:8px;font-size:13px;'>"
                f"<strong>{html.escape(str(t.get('model', '?')))}</strong> · 触发类型 {html.escape(str(trig.get('type', '')))}: "
                f"{html.escape(str(trig.get('reason', '')))}<br><span class='small-reason'>{html.escape(str(trig.get('detail', '')))}</span></div>"
            )
    trigger_html = ("<p><strong>人审按需触发记录（无法判定 / 分歧大 / 高风险）：</strong></p>" + "".join(trigger_rows)) if trigger_rows else \
        "<p class='small-reason'>无模型触发人审条件。</p>"

    return f"{table_html}{agreement_html}{trigger_html}"


def _render_statistics_section(stats: dict[str, Any] | None) -> str:
    if not stats:
        return "<p class='small-reason'>暂无统计指标数据。</p>"
    warning_html = ""
    if stats.get("small_sample_warning"):
        warning_html = f"<div style='background:#fffbeb;border:1px solid #fef3c7;color:#92400e;padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:13px;font-weight:600;'>{html.escape(stats['small_sample_warning'])}</div>"

    n_valid = stats.get("sample_size_valid", 0)
    n_total = stats.get("sample_size_total", 0)
    invalid_cnt = stats.get("invalid_runs_count", 0)
    mean_d = stats.get("mean_delta", 0.0)
    var = stats.get("variance", 0.0)
    sd = stats.get("std_dev", 0.0)
    dist = stats.get("distribution", {})
    w_wins = dist.get("with_wins", 0)
    wo_wins = dist.get("without_wins", 0)
    ties = dist.get("ties", 0)

    return f"""
      {warning_html}
      <div class='kpi-grid'>
        <div class='kpi'><span>有效样本量 (N)</span><strong>{n_valid} 对</strong><p>总计划模型 {n_total} 组，排除基础设施 invalid {invalid_cnt} 次</p></div>
        <div class='kpi good'><span>平均配对差值 (Δ̄)</span><strong>{mean_d:+.2f} 票</strong><p>With-Skill 得票减 Without-Skill 得票期望值</p></div>
        <div class='kpi blue'><span>配对差值标准差 (σ)</span><strong>{sd:.2f}</strong><p>样本方差 σ² = {var:.2f}</p></div>
        <div class='kpi warn'><span>成对胜负分布</span><strong>{w_wins} 胜 / {wo_wins} 负 / {ties} 平</strong><p>开 Skill 相对不开 Skill 的单模型对比结果</p></div>
      </div>
    """


def _render_snapshot_section(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return "<p class='small-reason'>当前运行未生成规则快照。</p>"
    ver = html.escape(str(snapshot.get("engine_version", "1.0.0")))
    rules_cnt = snapshot.get("rules_count", len(snapshot.get("rules_snapshot", [])))
    records_cnt = len(snapshot.get("records", []))
    created_at = html.escape(str(snapshot.get("created_at", "")))

    return f"""
      <div class='kpi-grid'>
        <div class='kpi'><span>断言引擎版本</span><strong>v{ver}</strong><p>固定判定版本号</p></div>
        <div class='kpi good'><span>规则快照数</span><strong>{rules_cnt} 条</strong><p>覆盖 exact_match / contains_facts / recomputable / script_eval</p></div>
        <div class='kpi blue'><span>比对落盘记录</span><strong>{records_cnt} 项</strong><p>记录输入、实际输出与断言证据</p></div>
        <div class='kpi warn'><span>确定性执行保障</span><strong>零 LLM 调用</strong><p>快照时间: {created_at[:19] if created_at else '已固定'}</p></div>
      </div>
    """


def render_report_html(payload: dict[str, Any]) -> str:
    groups = payload.get("grouped_results", []) or []
    task_prompt = _compact(payload.get("task_prompt", ""), 320)
    target_skill = html.escape(str(payload.get("target_skill") or "未指定"))
    tested_models = payload.get("tested_models", [])
    judge_models = payload.get("judge_models", [])
    tested_line = "、".join(str(m) for m in tested_models) if tested_models else "暂无"
    judge_line = "、".join(str(m) for m in judge_models) if judge_models else "评委未记录"
    total_with_votes = sum(_safe_int(g.get("with_votes")) for g in groups)
    total_without_votes = sum(_safe_int(g.get("without_votes")) for g in groups)
    total_votes = total_with_votes + total_without_votes
    with_best = payload.get("with_skill_best")
    without_best = payload.get("without_skill_best")

    model_result_html = _render_model_result_sections(payload)
    stats_html = _render_statistics_section(payload.get("statistics"))
    snapshot_html = _render_snapshot_section(payload.get("assertions_snapshot"))
    p1_payload = payload.get("p1")
    plain_summary_html = _render_plain_summary_section(payload)
    p1_alerts_html = _render_p1_negative_alerts(p1_payload)
    p1_details_html = _render_p1_pair_details(p1_payload)
    # ② 负迁移警报：仅在确实检出负迁移时才展示（「如有」才渲染整个区块）
    neg_alert_block = (
        f"<section class='section'><div class='section-head'><h2>② 负迁移警报</h2>"
        f"<span class='section-note'>如果带 skill 反而更差，这里会醒目提示</span></div>{p1_alerts_html}</section>"
        if p1_alerts_html else ""
    )

    return f"""<!DOCTYPE html>
<html lang='zh-CN'>
<head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Skill 评测报告 - {html.escape(str(payload.get('evaluation_name', '')))}</title><style>{_report_css()}</style></head>
<body><main class='page'>
<header class='hero'><div class='hero-top'><div><h1>Skill 评测报告</h1><p>让每个模型分别「带 skill」和「不带 skill」完成同一个任务，再由评委打分，告诉你这个 skill 到底该不该开。</p></div><div class='verdict-badge'>{_verdict_pill((p1_payload or {}).get('pair_gains', [{}])[0].get('verdict', 'inconclusive') if p1_payload and p1_payload.get('pair_gains') else 'inconclusive')}</div></div><div class='meta-grid'><div class='meta'><span>被测 Skill</span><strong>{target_skill}</strong></div><div class='meta'><span>用户任务</span><strong>{html.escape(task_prompt or '未记录')}</strong></div><div class='meta'><span>测试模型</span><strong>{html.escape(tested_line)}</strong></div><div class='meta'><span>评委模型</span><strong>{html.escape(judge_line)}</strong></div></div></header>
<section class='section' style='border:2px solid #b45309;background:#fffbeb;border-radius:12px;padding:16px 20px;'><div class='section-head'><h2>⚠️ 这次是用什么工具测的</h2><span class='section-note'>换一个 AI 工具，结果可能会不一样</span></div><p style='margin:8px 0 0;font-size:14px;line-height:1.7;'><strong>本次使用的 AI 工具:</strong> {html.escape('、'.join((payload.get('host_boundary') or {}).get('tested_hosts', [])) or '未记录')}</p><p style='margin:8px 0 0;font-size:14px;line-height:1.7;'>{html.escape(str((payload.get('host_boundary') or {}).get('statement', '')))}</p></section>
<section class='section'><div class='section-head'><h2>① 总结</h2><span class='section-note'>三句话以内说清这次评测结果</span></div>{plain_summary_html}</section>
{neg_alert_block}<section class='section'><div class='section-head'><h2>③ 按模型看结果</h2><span class='section-note'>每个模型一个完整区块：结论、评委打分与评价、带 / 不带 skill 主交付物对照、诊断；过程证据与统计口径收在技术细节里</span></div>{model_result_html}</section>
<details class='tech-section'><summary>④ 配对明细与统计口径（点开看完整数据）</summary><div class='tech-section-body'>
<section class='section'><div class='section-head'><h2>配对明细与评委打分一致性</h2><span class='section-note'>每对任务 × 每轮投票的配对差值、评委一致率（同一配对内评委同判定比例）与人审触发记录</span></div>{p1_details_html}</section>
<section class='section'><div class='section-head'><h2>统计口径明细（技术视角）</h2><span class='section-note'>有效样本量、配对差值、波动范围等统计口径，仅作复核用</span></div>{stats_html}</section>
</div></details>
<details class='tech-section'><summary>⑤ 附录（自动检查规则快照 / 证据链）</summary><div class='tech-section-body'>
<section class='section'><div class='section-head'><h2>自动检查规则快照</h2><span class='section-note'>同一份产出重查多少遍结果都一样，可复验</span></div>{snapshot_html}</section>
<section class='section'><div class='section-head'><h2>证据链与可信度</h2><span class='section-note'>透明化的底层记录，确保每项判断均可追溯</span></div><div class='evidence-grid'><div class='evidence'><strong>跑前体检记录</strong><span>开跑前确认输入文件与环境就绪，记录保存在 run 目录中。</span></div><div class='evidence'><strong>空产出自动过滤</strong><span>评委打分阶段会跳过没交出东西的实验。</span></div><div class='evidence'><strong>评委原始输出</strong><span>投票依据保存在 blind_vote/evaluations.json。</span></div><div class='evidence'><strong>实验全流程追溯</strong><span>包括 stdout、stderr、transcript 及全量 artifacts。</span></div></div></section>
</div></details>
</main><script>{_report_js()}</script></body></html>"""


def generate_run_report(run_dir: Path, output_path: Path | None = None) -> Path:
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"run_manifest.json not found in {run_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = build_report_payload(run_dir, manifest)
    output = output_path or (run_dir / "report.html")
    html_content = render_report_html(payload)
    output.write_text(html_content, encoding="utf-8")
    return output


def _read_text_preview(path: Path, artifact: dict[str, Any], limit: int = 5000) -> str:
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > limit:
            return content[:limit] + "\n\n...[已截断]"
        return content
    except Exception:
        try:
            content = path.read_text(encoding="gbk", errors="replace")
            if len(content) > limit:
                return content[:limit] + "\n\n...[已截断]"
            return content
        except Exception:
            return ""


def generate_anonymous_report(run_dir: Path, output_path: Path | None = None) -> Path:
    """生成匿名报告，只展示有实际产出物的方案，匿名标签为方案A/B/C..."""
    from .result_files import select_result_files

    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"run_manifest.json not found in {run_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    task_prompt = str(manifest.get("task_prompt", "") or "")
    target_skill = str(manifest.get("target_skill", "") or "")

    results = []
    for item in manifest.get("results", []):
        ws_dir = Path(str(item.get("workspace_dir", "")))
        artifacts_dir = ws_dir / "artifacts"
        if not artifacts_dir.exists():
            continue

        selection = select_result_files(ws_dir, task_prompt=task_prompt, target_skill=target_skill)
        result_files = selection.get("validated_result_files") or []
        if not result_files:
            continue

        results.append({
            "workspace_dir": str(ws_dir),
            "result_files": result_files,
            "use_skill": item.get("use_skill", False),
        })

    if not results:
        output = output_path or (run_dir / "anonymous_report.html")
        output.write_text("<html><body><h1>无有效产出物</h1></body></html>", encoding="utf-8")
        return output

    scheme_labels = []
    for i in range(len(results)):
        scheme_labels.append(f"方案{chr(65 + i)}")

    scheme_cards_html = []
    for idx, (scheme_label, result_item) in enumerate(zip(scheme_labels, results)):
        ws_dir = Path(result_item["workspace_dir"])
        cards_html = ""

        for art in result_item.get("result_files", []):
            rel_path = str(art.get("path") or "")
            art_path = ws_dir / "artifacts" / rel_path
            if not art_path.exists():
                continue

            ext = art_path.suffix.lower()
            mime_map = {
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".xls": "application/vnd.ms-excel",
                ".csv": "text/csv",
                ".md": "text/markdown",
                ".txt": "text/plain",
                ".json": "application/json",
                ".html": "text/html",
            }
            mime = mime_map.get(ext, "application/octet-stream")

            try:
                with open(art_path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
                data_uri = f"data:{mime};base64,{data}"
            except Exception:
                data_uri = "#"

            file_name = art_path.name

            if ext == ".md":
                text = _read_text_preview(art_path, {}, 8000)
                cards_html += f"""
                <div class="artifact">
                  <div class="artifact-title">
                    <div class="file-main"><strong>{html.escape(file_name)}</strong></div>
                    <div class="file-actions">
                      <a class="download" href="{data_uri}" download="{html.escape(file_name)}">下载</a>
                    </div>
                  </div>
                  <pre class="content-preview">{html.escape(text)}</pre>
                </div>
                """
            elif ext in {".xlsx", ".xls"}:
                rows = _xlsx_preview(art_path) if ext in {".xlsx", ".xls"} else []
                table_html = _render_table(rows) if rows else "<p>[表格无法预览，请下载]</p>"
                cards_html += f"""
                <div class="artifact">
                  <div class="artifact-title">
                    <div class="file-main"><strong>{html.escape(file_name)}</strong></div>
                    <div class="file-actions">
                      <a class="download" href="{data_uri}" download="{html.escape(file_name)}">下载</a>
                    </div>
                  </div>
                  <div class="sheet-wrap">{table_html}</div>
                </div>
                """
            else:
                text = _read_text_preview(art_path, {}, 2000)
                cards_html += f"""
                <div class="artifact">
                  <div class="artifact-title">
                    <div class="file-main"><strong>{html.escape(file_name)}</strong></div>
                    <div class="file-actions">
                      <a class="download" href="{data_uri}" download="{html.escape(file_name)}">下载</a>
                    </div>
                  </div>
                  <pre class="content-preview">{html.escape(text[:500] + '...' if len(text) > 500 else text)}</pre>
                </div>
                """

        scheme_cards_html.append(f"""
        <div class="scheme-card">
          <div class="scheme-head">
            <h3>{scheme_label}</h3>
          </div>
          <div class="scheme-body">{cards_html}</div>
        </div>
        """)

    css = r'''
    :root { --bg:#f8fafc; --panel:#fff; --ink:#1e293b; --muted:#64748b; --line:#e2e8f0; --primary:#2563eb; --green:#10b981; --green-soft:#ecfdf5; --green-border:#a7f3d0; --blue:#3b82f6; --blue-soft:#eff6ff; --blue-border:#bfdbfe; }
    *{box-sizing:border-box;-webkit-font-smoothing:antialiased} body{margin:0;background:var(--bg);color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.6}
    .page{width:100%;max-width:1280px;margin:0 auto;padding:clamp(16px,3vw,40px)}
    .hero{background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%);color:#fff;border-radius:12px;padding:32px;margin-bottom:24px}
    .hero h1{font-size:28px;font-weight:800;margin:0 0 8px} .hero p{color:#94a3b8;font-size:15px;margin:0}
    .task-box{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:24px;font-size:14px;color:#475569}
    .scheme-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(500px,100%),1fr));gap:20px}
    .scheme-card{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
    .scheme-head{display:flex;justify-content:space-between;align-items:center;padding:16px 20px;background:#f8fafc;border-bottom:1px solid var(--line)}
    .scheme-head h3{margin:0;font-size:18px;font-weight:800} .mode-tag{background:#e2e8f0;color:#475569;border-radius:99px;padding:4px 12px;font-size:12px;font-weight:700}
    .scheme-body{padding:16px;display:grid;gap:12px}
    .artifact{border:1px solid var(--line);border-radius:8px;overflow:hidden} .artifact-title{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 14px;background:#fff;border-bottom:1px solid var(--line);font-size:13px;font-weight:700}
    .file-main strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .file-actions{display:flex;align-items:center;gap:8px}
    .download{background:#fff;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:4px 10px;font-size:12px;font-weight:700;text-decoration:none}
    .download:hover{background:var(--primary);color:#fff;border-color:var(--primary)}
    pre.content-preview{margin:0;padding:14px;background:#f8fafc;font-size:13px;max-height:400px;overflow:auto;white-space:pre-wrap;word-break:break-word}
    .sheet-wrap{overflow:auto;max-height:400px} .sheet{width:100%;border-collapse:collapse;font-size:13px}
    .sheet th,.sheet td{border:1px solid var(--line);padding:8px 12px;text-align:left}
    .sheet th{background:#f8fafc;font-weight:700;color:var(--muted)}
    @media(max-width:768px){.scheme-list{grid-template-columns:1fr}}
    '''

    html_content = f"""<!DOCTYPE html>
<html lang='zh-CN'>
<head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>匿名评测报告 - {html.escape(str(manifest.get('evaluation_name', '')))}</title>
<style>{css}</style></head>
<body><main class='page'>
<header class='hero'><h1>方案矩阵</h1><p>请围绕着需求选择你觉得不错的方案吧</p></header>
<div class='task-box'><strong>评测任务：</strong>{html.escape(task_prompt)}</div>
<div class='scheme-list'>{''.join(scheme_cards_html)}</div>
</main></body></html>"""

    output = output_path or (run_dir / "anonymous_report.html")
    output.write_text(html_content, encoding="utf-8")
    return output
