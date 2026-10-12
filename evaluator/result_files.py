from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any


EVIDENCE_NAMES = {
    "summary.md",
    "execution_trace.md",
    "result_manifest.json",
    "result_selection.json",
    "metadata.json",
    "metrics.json",
}

EVIDENCE_KEYWORDS = (
    "summary",
    "trace",
    "log",
    "stderr",
    "stdout",
    "metadata",
    "metric",
    "manifest",
    "selection",
    "execution",
)

RESULT_KEYWORDS = (
    "final",
    "result",
    "deliverable",
    "output",
    "report",
    "analysis",
    "presentation",
    "deck",
    "dashboard",
    "报告",
    "分析",
    "演示",
    "最终",
    "结果",
)

TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".html",
    ".htm",
    ".csv",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".sql",
    ".yaml",
    ".yml",
}

OUTPUT_EXTENSIONS = {
    ".pptx": ("primary", "最终 PPT 演示文稿"),
    ".ppt": ("primary", "最终 PPT 演示文稿"),
    ".html": ("primary", "HTML 结果页面"),
    ".htm": ("primary", "HTML 结果页面"),
    ".md": ("primary", "Markdown 结果报告"),
    ".markdown": ("primary", "Markdown 结果报告"),
    ".xlsx": ("supporting", "可复核 Excel 结果文件"),
    ".xls": ("supporting", "可复核 Excel 结果文件"),
    ".csv": ("supporting", "CSV 结果文件"),
    ".docx": ("primary", "Word 文档"),
    ".doc": ("primary", "Word 文档"),
    ".pdf": ("primary", "PDF 结果文件"),
    ".json": ("supporting", "结构化结果文件"),
}


def infer_result_contract(
    task_prompt: str = "",
    target_skill: str = "",
    skill_path: str | Path | None = None,
) -> dict[str, Any]:
    """Infer the user-facing result files for this evaluation.

    Priority:
    1. Explicit user-requested delivery format in the task prompt.
    2. Declared output files/formats in the tested skill's SKILL.md.
    3. Known/default output contract from the target skill name.
    4. Broad fallback for report-like deliverables.
    """
    task = (task_prompt or "").lower()
    skill = (target_skill or "").lower()
    outputs: list[dict[str, Any]] = []

    def add(ext: str, role: str, label: str, source: str, required: bool = True) -> None:
        if not ext.startswith("."):
            ext = f".{ext}"
        for item in outputs:
            if item["ext"] == ext:
                if role == "primary":
                    item["role"] = "primary"
                    item["required"] = required
                return
        outputs.append({
            "ext": ext,
            "role": role,
            "label": label,
            "source": source,
            "required": required,
        })

    has_ppt = bool(re.search(r"\b(ppt|pptx|powerpoint|slides?|presentation)\b|幻灯片|演示文稿|做.*ppt|制作.*ppt|生成.*ppt", task))
    has_html_deck = bool(re.search(r"html|网页|交互式|网页版|转网页", task))
    output_words = r"(输出|导出|生成|产出|交付|保存|给我|需要|要|格式)"
    has_md = bool(re.search(rf"({output_words}.{{0,16}}(\bmd\b|markdown)|用\s*md|md\s*文件|markdown\s*格式)", task))
    has_xlsx = bool(re.search(rf"({output_words}.{{0,16}}(\bxlsx\b|\bxls\b|excel|spreadsheet|明细表|结果表|表格|工作簿)|(\bxlsx\b|\bxls\b|excel)\s*格式)", task))
    has_csv = bool(re.search(rf"({output_words}.{{0,16}}\bcsv\b|csv\s*格式)", task))
    has_pdf = bool(re.search(rf"({output_words}.{{0,16}}\bpdf\b|pdf\s*格式)", task))

    if has_html_deck:
        add(".html", "primary", "交互式网页演示", "user_task", True)
    elif has_ppt:
        add(".pptx", "primary", "最终 PPT 演示文稿", "user_task", True)

    if has_md:
        add(".md", "primary" if not outputs else "supporting", "Markdown 结果报告", "user_task", True)
    if has_xlsx:
        add(".xlsx", "primary" if not outputs else "supporting", "Excel 结果文件", "user_task", True)
    if has_csv:
        add(".csv", "primary" if not outputs else "supporting", "CSV 结果文件", "user_task", True)
    if has_pdf:
        add(".pdf", "primary" if not outputs else "supporting", "PDF 结果文件", "user_task", True)

    if not outputs:
        for ext, role, label in _declared_skill_outputs(skill_path):
            add(ext, role, label, "skill_contract", role == "primary")

    if not outputs:
        if "presentation" in skill or "ppt" in skill:
            add(".pptx", "primary", "最终 PPT 演示文稿", "skill_contract", True)
            add(".md", "supporting", "配套演讲稿", "skill_contract", False)
        elif "excel" in skill or "analyst" in skill:
            add(".md", "primary", "业务分析报告", "skill_contract", True)
            add(".xlsx", "supporting", "可复核 Excel 结果文件", "skill_contract", False)

    if not outputs:
        add(".md", "primary", "最终报告", "fallback", True)

    return {
        "outputs": outputs,
        "judge_policy": "评委只能评价 validated_result_files 中的实际结果文件内容；summary、trace、日志、脚本和中间文件只作为过程证据，不进入评分。",
    }


def _declared_skill_outputs(skill_path: str | Path | None) -> list[tuple[str, str, str]]:
    if not skill_path:
        return []
    root = Path(skill_path)
    skill_md = root / "SKILL.md"
    if not skill_md.exists() and root.exists():
        for candidate in root.rglob("SKILL.md"):
            skill_md = candidate
            break
    if not skill_md.exists():
        return []

    content = skill_md.read_text(encoding="utf-8", errors="replace").lower()
    declared: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for raw_ext in re.findall(r"\.(pptx?|xlsx|xls|csv|md|markdown|html?|docx?|pdf|json)\b", content):
        ext = "." + raw_ext.lstrip(".").lower()
        if ext == ".ppt":
            ext = ".pptx"
        if ext == ".htm":
            ext = ".html"
        if ext in seen:
            continue
        role, label = OUTPUT_EXTENSIONS.get(ext, ("supporting", f"{ext} 结果文件"))
        declared.append((ext, role, label))
        seen.add(ext)

    if any(ext == ".pptx" for ext, _role, _label in declared):
        declared = [
            (ext, ("primary" if ext == ".pptx" else role), label)
            for ext, role, label in declared
        ]
    elif any(ext in {".md", ".markdown"} for ext, _role, _label in declared):
        declared = [
            (ext, ("primary" if ext in {".md", ".markdown"} else role), label)
            for ext, role, label in declared
        ]
    elif declared:
        first_ext, _first_role, first_label = declared[0]
        declared[0] = (first_ext, "primary", first_label)
    return declared


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _norm_artifact_path(raw: Any) -> str:
    text = str(raw or "").strip()
    if text.startswith("./"):
        text = text[2:]
    if text.startswith("artifacts/"):
        text = text[len("artifacts/"):]
    return text


def _artifact_files(artifacts_dir: Path) -> list[Path]:
    if not artifacts_dir.exists():
        return []
    return sorted(p for p in artifacts_dir.rglob("*") if p.is_file() and p.name != ".DS_Store")


def _manifest_candidates(artifacts_dir: Path) -> dict[str, dict[str, Any]]:
    manifest = _load_json(artifacts_dir / "result_manifest.json")
    candidates: dict[str, dict[str, Any]] = {}
    for item in manifest.get("result_files", []) if isinstance(manifest, dict) else []:
        if not isinstance(item, dict):
            continue
        rel = _norm_artifact_path(item.get("path"))
        if not rel:
            continue
        candidates[rel] = item
    return candidates


def _is_process_file(path: Path) -> bool:
    rel = str(path).lower()
    name = path.name.lower()
    if name in EVIDENCE_NAMES:
        return True
    return any(keyword in rel for keyword in EVIDENCE_KEYWORDS)


def _is_parseable(path: Path) -> tuple[bool, str]:
    ext = path.suffix.lower()
    try:
        if path.stat().st_size <= 0:
            return False, "文件为空"
    except Exception:
        return False, "无法读取文件大小"

    if ext in {".pptx", ".xlsx", ".docx"}:
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
            if ext == ".pptx" and not any(n.startswith("ppt/slides/slide") for n in names):
                return False, "PPTX 中未发现 slide"
            if ext == ".xlsx" and not any(n.startswith("xl/worksheets/sheet") for n in names):
                return False, "XLSX 中未发现 worksheet"
            return True, ""
        except Exception:
            return False, f"{ext} 文件无法解析"

    if ext in TEXT_EXTENSIONS:
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if len(text) < 4:
                return False, "文本内容过短"
            return True, ""
        except Exception:
            return False, "文本文件无法读取"

    return True, ""


def _score_candidate(path: Path, expected: dict[str, Any], manifest_item: dict[str, Any] | None) -> tuple[int, int, str]:
    rel = str(path).lower()
    name = path.name.lower()
    score = 0
    if manifest_item:
        score += 80
    if path.suffix.lower() == expected["ext"]:
        score += 60
    if any(keyword in rel for keyword in RESULT_KEYWORDS):
        score += 30
    if name in {"report.md", "final_report.md", "analysis_report.md", "presentation.pptx", "deck.pptx", "index.html"}:
        score += 35
    if any(k in name for k in ("march_", "april_", "may_", "june_", "july_", "august_", "september_", "october_", "november_", "december_", "3月", "4月")):
        score -= 10
    if expected.get("role") == "primary" and any(k in rel for k in ("final", "最终", "report", "报告", "presentation", "ppt")):
        score += 15
    if _is_process_file(path):
        score -= 120
    try:
        size = path.stat().st_size
    except Exception:
        size = 0
    return (-score, -size, name)


def _select_for_expected(
    files: list[Path],
    artifacts_dir: Path,
    expected: dict[str, Any],
    manifest_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    ext = expected["ext"]
    ext_matches = [f for f in files if f.suffix.lower() == ext and not _is_process_file(f)]
    manifest_matches = []
    for rel, item in manifest_map.items():
        path = artifacts_dir / rel
        if path.exists() and path.suffix.lower() == ext and not _is_process_file(path):
            manifest_matches.append(path)
    candidates = sorted(set(ext_matches + manifest_matches), key=lambda p: _score_candidate(p, expected, manifest_map.get(str(p.relative_to(artifacts_dir)))))
    for path in candidates:
        ok, reason = _is_parseable(path)
        if not ok:
            continue
        rel = str(path.relative_to(artifacts_dir))
        manifest_item = manifest_map.get(rel, {})
        return {
            "name": path.name,
            "path": rel,
            "size": path.stat().st_size,
            "ext": ext,
            "role": expected.get("role", "primary"),
            "label": expected.get("label", "结果文件"),
            "source": expected.get("source", ""),
            "required": bool(expected.get("required", True)),
            "confidence": 0.95 if manifest_item else 0.82,
            "selection_reason": manifest_item.get("description") or manifest_item.get("reason") or f"匹配预期结果格式 {ext}，且通过存在性/非空/可解析校验。",
        }
    return None


def select_result_files(
    workspace_dir: str | Path,
    task_prompt: str = "",
    target_skill: str = "",
) -> dict[str, Any]:
    workspace = Path(workspace_dir)
    artifacts_dir = workspace / "artifacts"
    metadata = _load_json(workspace / "metadata.json")
    if not target_skill:
        target_skill = str(metadata.get("target_skill", "") or "")
    skill_path = metadata.get("target_skill_path", "")

    contract = infer_result_contract(task_prompt, target_skill, skill_path)
    files = _artifact_files(artifacts_dir)
    manifest_map = _manifest_candidates(artifacts_dir)

    selected: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    used_paths: set[str] = set()

    for expected in contract["outputs"]:
        chosen = _select_for_expected(files, artifacts_dir, expected, manifest_map)
        if chosen:
            selected.append(chosen)
            used_paths.add(chosen["path"])
        elif expected.get("required", True):
            missing.append({
                "ext": expected["ext"],
                "role": expected.get("role", "primary"),
                "label": expected.get("label", "结果文件"),
                "reason": f"未找到用户需求/skill契约要求的 {expected['ext']} 结果文件。",
            })

    if not selected and files and not missing:
        fallback_expected = {"ext": "", "role": "primary", "label": "兜底识别结果文件", "source": "fallback", "required": True}
        fallback_candidates = [f for f in files if not _is_process_file(f)]
        fallback_candidates = sorted(
            fallback_candidates,
            key=lambda p: (
                0 if p.suffix.lower() in {".pptx", ".xlsx", ".html", ".pdf", ".md", ".csv"} else 1,
                _score_candidate(p, fallback_expected, manifest_map.get(str(p.relative_to(artifacts_dir)))),
            ),
        )
        for path in fallback_candidates:
            ok, _reason = _is_parseable(path)
            if ok:
                rel = str(path.relative_to(artifacts_dir))
                selected.append({
                    "name": path.name,
                    "path": rel,
                    "size": path.stat().st_size,
                    "ext": path.suffix.lower(),
                    "role": "fallback_result",
                    "label": "兜底识别结果文件",
                    "source": "fallback",
                    "required": False,
                    "confidence": 0.45,
                    "selection_reason": "未找到契约要求格式，兜底选择非过程文件中最像最终交付物的文件。",
                })
                used_paths.add(rel)
                break

    evidence = []
    for path in files:
        rel = str(path.relative_to(artifacts_dir))
        if rel in used_paths:
            continue
        evidence.append({
            "name": path.name,
            "path": rel,
            "size": path.stat().st_size,
            "type": "process_evidence" if _is_process_file(path) else "supporting_artifact",
        })

    confidence = max((float(item.get("confidence", 0)) for item in selected), default=0.0)
    status = "ok" if selected and not missing else ("partial" if selected else "missing")
    result = {
        "status": status,
        "result_contract": contract,
        "validated_result_files": selected,
        "missing_required_outputs": missing,
        "evidence_files": evidence,
        "selection_confidence": confidence,
        "selection_reason": (
            "已按用户需求和 skill 输出契约定位结果文件。"
            if selected else
            "未找到可验证的结果文件；评委应视为未交付要求的最终产物。"
        ),
    }
    return result


def write_result_selection(
    workspace_dir: str | Path,
    task_prompt: str = "",
    target_skill: str = "",
) -> dict[str, Any]:
    selection = select_result_files(workspace_dir, task_prompt, target_skill)
    artifacts_dir = Path(workspace_dir) / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "result_selection.json").write_text(
        json.dumps(selection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return selection
