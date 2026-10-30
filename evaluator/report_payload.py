"""report_payload.py - 报告三层结构之「payload 组装（纯数据）」

R1：从 reporting.py 拆出。只做数据组装，不产出任何 HTML。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
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

def _dedupe_nonempty(values: list[Any], limit: int = 3) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _compact_text(str(value or "").strip(), 220)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result

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



# ==========================================================================
# R2/R4/R5: 首屏 payload（纯数据，结论先行）
# ==========================================================================
_VERDICT_LABELS = {
    "skill_effective": ("明确有效", "带这个 skill 明显更好，建议开启"),
    "negative_migration": ("明确有害", "带这个 skill 反而更差，建议关闭"),
    "inconclusive": ("没法下结论", "这次没测出明显区别"),
}


def _compact_text(text: str, limit: int = 200) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def build_first_screen_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """把报告 payload 收敛成首屏四件套所需的纯数据。

    返回：
    - verdict: 总判定（任一模型明确有害则整体判「有害」，全部有效判「有效」，否则没法下结论）
    - headline: 一句话结论
    - kpis: 三个关键指标（结论分布 / 评委总票 / 平均差值）
    - models: 每模型有/无对比卡片数据
    - cases: 红绿用例清单（逐条）
    """
    groups = payload.get("grouped_results") or []
    p1 = payload.get("p1") or {}
    pair_gains = p1.get("pair_gains") or []
    stats = payload.get("statistics") or {}

    effective = sum(1 for pg in pair_gains if pg.get("verdict") == "skill_effective")
    harmful = sum(1 for pg in pair_gains if pg.get("verdict") == "negative_migration")
    unclear = max(len(pair_gains) - effective - harmful, 0)
    if not pair_gains:
        unclear = len(groups)

    if harmful:
        verdict = "negative_migration"
    elif effective and not harmful:
        verdict = "skill_effective"
    elif pair_gains:
        verdict = "inconclusive"
    else:
        verdict = "inconclusive"

    label, default_headline = _VERDICT_LABELS.get(verdict, (verdict, ""))
    # R5：主结论固定一句话（大字），补充说明最多两句（小字）
    headline = default_headline or build_plain_summary(pair_gains, groups)
    detail = build_plain_summary(pair_gains, groups)

    total_with = sum(int(g.get("with_votes", 0) or 0) for g in groups)
    total_without = sum(int(g.get("without_votes", 0) or 0) for g in groups)
    mean_delta = float(stats.get("mean_delta", 0.0) or 0.0)
    n_valid = int(stats.get("sample_size_valid", 0) or 0)
    invalid = int(stats.get("invalid_runs_count", 0) or 0)

    kpis = [
        {
            "key": "verdicts",
            "label": "结论分布",
            "value": f"有效 {effective} / 有害 {harmful} / 没结论 {unclear}",
            "hint": f"共 {len(groups)} 个模型各测了一遍「带」和「不带」",
            "tone": "good" if verdict == "skill_effective" else ("warn" if verdict == "negative_migration" else ""),
        },
        {
            "key": "votes",
            "label": "评委总票",
            "value": f"带 {total_with} : 不带 {total_without}",
            "hint": "评委匿名打分，不知道哪份是带 skill 的",
            "tone": "good" if total_with > total_without else ("warn" if total_without > total_with else ""),
        },
        {
            "key": "delta",
            "label": "平均差值",
            "value": f"{mean_delta:+.1f} 票",
            "hint": f"对比了 {n_valid} 组；基础设施故障 {invalid} 次未计入",
            "tone": "good" if mean_delta > 0 else ("warn" if mean_delta < 0 else ""),
        },
    ]

    models: list[dict[str, Any]] = []
    for group in groups:
        model = str(group.get("model", "?"))
        pg = next((item for item in pair_gains if item.get("model") == model), {})
        model_verdict = pg.get("verdict") or "inconclusive"
        model_label = _VERDICT_LABELS.get(model_verdict, (model_verdict, ""))[0]
        plain = verdict_to_plain_language(
            model_verdict,
            model=model,
            n=int(pg.get("n_paired", 0) or 0),
            mean_delta=float(pg.get("mean_delta", 0.0) or 0.0),
            primary_metric=str(pg.get("primary_metric") or "assertion"),
        )
        with_payload = group.get("with_skill") or {}
        without_payload = group.get("without_skill") or {}
        models.append(
            {
                "model": model,
                "verdict": model_verdict,
                "verdict_label": model_label,
                "plain": plain,
                "with_votes": int(group.get("with_votes", 0) or 0),
                "without_votes": int(group.get("without_votes", 0) or 0),
                "with_artifacts": len(with_payload.get("artifacts") or []) if isinstance(with_payload, dict) else 0,
                "without_artifacts": len(without_payload.get("artifacts") or []) if isinstance(without_payload, dict) else 0,
                "n_paired": int(pg.get("n_paired", 0) or 0),
                "mean_delta": float(pg.get("mean_delta", 0.0) or 0.0),
            }
        )

    cases: list[dict[str, Any]] = []
    snapshot = payload.get("assertions_snapshot") or {}
    for record in snapshot.get("records") or []:
        if not isinstance(record, dict):
            continue
        model = str(record.get("model", "?"))
        mode = "带 Skill" if record.get("use_skill") else "不带 Skill"
        repetition = record.get("repetition", "?")
        for result in record.get("results") or []:
            if not isinstance(result, dict):
                continue
            passed = bool(result.get("passed"))
            reason = str(result.get("message") or result.get("error") or "").strip()
            if not reason:
                reason = "自动检查通过" if passed else "自动检查未通过"
            cases.append(
                {
                    "model": model,
                    "mode": mode,
                    "repetition": repetition,
                    "name": str(result.get("name") or "未命名检查"),
                    "passed": passed,
                    "reason": _compact_text(to_plain_language(reason), 140),
                }
            )

    return {
        "verdict": verdict,
        "verdict_label": label,
        "headline": headline,
        "detail": detail,
        "kpis": kpis,
        "models": models,
        "cases": cases,
        "cases_passed": sum(1 for c in cases if c["passed"]),
        "cases_failed": sum(1 for c in cases if not c["passed"]),
    }


def check_report_self_contained(html_text: str) -> list[str]:
    """R8 自检：报告必须自包含（无外链 / 无外部 link / 无外部 script src）。"""
    problems: list[str] = []
    lowered = (html_text or "").lower()
    for marker in ("<link", "src=\"http://", "src='http://", "src=\"https://", "src='https://"):
        if marker in lowered:
            problems.append(f"发现外部引用: {marker}")
    for match in re.finditer(r"""https?://([^\s"'<>]+)""", html_text or ""):
        url = match.group(0)
        # 允许注释/文本里出现的说明性 URL 仅限 svg 命名空间
        if "www.w3.org" in url:
            continue
        problems.append(f"发现外链: {url}")
    return sorted(set(problems))
