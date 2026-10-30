"""report_template.py - 报告三层结构之「模板层」

R1：从 reporting.py 拆出全部 _render_* / _artifact_* 与内联 CSS/JS。
R2-R7：结论先行首屏、红绿用例清单、三个关键指标、内联预算都在本层实现。
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
from .report_payload import safe_read_text

LOGGER = logging.getLogger(__name__)


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

# R6/R7: 单文件内联预算（超过预算不再无条件 base64，避免单文件体积失控）
INLINE_BUDGET_BYTES = 20 * 1024 * 1024


def _inline_allowed(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size <= INLINE_BUDGET_BYTES
    except OSError:
        return False


def _artifact_href(item: dict[str, Any] | None, artifact: dict[str, Any], use_data_uri: bool = False) -> str:
    path = _artifact_abs_path(item, artifact)
    run_dir = Path(str((item or {}).get("run_dir") or ""))

    def quote_path(value: str) -> str:
        return quote(value.replace("\\", "/"), safe="/:#?&=%+~@!$,;'()*[]")

    try:
        if use_data_uri and _inline_allowed(path):
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

def _render_oversize_placeholder(artifact: dict[str, Any], path: Path, size: int) -> str:
    """R7: 超内联预算的大文件 → 纯 SVG 摘要占位（文件名/大小/前 N 行文本）。"""
    name = str(artifact.get("path") or artifact.get("name") or path.name)
    first_lines = ""
    if path.suffix.lower() in {".md", ".txt", ".csv", ".json", ".html", ".htm"}:
        try:
            first_lines = "\n".join(
                path.read_text(encoding="utf-8", errors="replace").splitlines()[:8]
            )
        except OSError:
            first_lines = ""
    size_mb = size / (1024 * 1024)
    text = (
        f"{name}（{size_mb:.1f} MB，超过单文件内联预算 "
        f"{INLINE_BUDGET_BYTES // (1024 * 1024)} MB，未内联）"
    )
    if first_lines:
        text += "\n前几行：\n" + first_lines
    return (
        "<div class='result-note'>"
        f"<strong>大文件摘要（未内联，报告仍可单文件拷走）</strong>"
        f"<pre style='margin:8px 0 0;white-space:pre-wrap;'>{html.escape(text)}</pre>"
        "<p style='margin:8px 0 0;'>完整文件请到本机 run 目录查看："
        f"<code>{html.escape(str(path))}</code></p></div>"
    )


def _render_artifact_preview(item: dict[str, Any] | None, artifact: dict[str, Any], primary: bool = False) -> str:
    path = _artifact_abs_path(item, artifact)
    try:
        oversize = path.exists() and path.stat().st_size > INLINE_BUDGET_BYTES
    except OSError:
        oversize = False
    if oversize and primary:
        return _render_oversize_placeholder(artifact, path, path.stat().st_size)
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
                f"<tr><td>{model}</td><td>第 {html.escape(str(d.get('repetition', '?')))} 轮</td>"
                f"<td>{float(d.get('with_value', 0.0)):.1f}</td><td>{float(d.get('without_value', 0.0)):.1f}</td>"
                f"<td class='{'pos' if float(d.get('delta', 0)) > 0 else ('neg' if float(d.get('delta', 0)) < 0 else '')}'><strong>{float(d.get('delta', 0.0)):+.2f}</strong></td></tr>"
            )
    table_html = ""
    if rows:
        table_html = (
            "<table style='width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px;'>"
            "<tr style='text-align:left;border-bottom:2px solid #e5e7eb;'><th>模型</th><th>第几轮</th><th>带 Skill</th><th>不带 Skill</th><th>差值</th></tr>"
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
            f"<tr><td>{html.escape(str(r.get('model', '?')))}</td><td>第 {html.escape(str(r.get('repetition', 1)))} 轮</td>"
            f"<td>round {r.get('round', 0)}</td><td>{r.get('judge_count', 0)}</td>"
            f"<td>{r.get('agree_pairs', 0)}/{r.get('comparable_pairs', 0)}</td>"
            f"<td>{f'{float(r_rate):.0%}' if r_rate is not None else '—'}</td></tr>"
        )
    agreement_html = ""
    if per_round_rows:
        agreement_html = (
            f"<p><strong>评委看法一致的比例（同一组里评委给出同样判断的比例）：</strong>整体均值 {rate_str}"
            f"（同判定 {ja.get('overall_agree_pairs', 0)} / 总可比 {ja.get('overall_comparable_pairs', 0)}）</p>"
            "<table style='width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px;'>"
            "<tr style='text-align:left;border-bottom:2px solid #e5e7eb;'><th>模型</th><th>第几轮</th><th>轮次</th><th>评委数</th><th>同判定/总可比</th><th>一致率</th></tr>"
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
    trigger_html = ("<p><strong>需要人工复核的记录（没法判定 / 分歧大 / 高风险）：</strong></p>" + "".join(trigger_rows)) if trigger_rows else \
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
        <div class='kpi'><span>对比了几组</span><strong>{n_valid} 组</strong><p>共 {n_total} 组实验，其中 {invalid_cnt} 次因基础设施故障没算进来</p></div>
        <div class='kpi good'><span>平均差值</span><strong>{mean_d:+.2f} 票</strong><p>带 skill 比不带 skill 平均多/少拿多少票</p></div>
        <div class='kpi blue'><span>波动大小</span><strong>{sd:.2f}</strong><p>数值越小，说明结果越稳定</p></div>
        <div class='kpi warn'><span>成对胜负分布</span><strong>{w_wins} 胜 / {wo_wins} 负 / {ties} 平</strong><p>带 Skill 相对不带 Skill 的单模型对比结果</p></div>
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
        <div class='kpi'><span>自动检查版本</span><strong>v{ver}</strong><p>固定版本号，换版本结果可比性会变</p></div>
        <div class='kpi good'><span>自动检查规则存档</span><strong>{rules_cnt} 条</strong><p>都是固定规则，不看模型发挥</p></div>
        <div class='kpi blue'><span>检查记录</span><strong>{records_cnt} 项</strong><p>每项都留了输入、实际输出和判断依据</p></div>
        <div class='kpi warn'><span>结果可复现</span><strong>纯规则计算，不靠大模型</strong><p>存档时间: {created_at[:19] if created_at else '已固定'}</p></div>
      </div>
    """



# ==========================================================================
# R2-R5: 结论先行首屏四件套（渲染层）
# ==========================================================================
def _render_first_screen_hero(first: dict[str, Any], payload: dict[str, Any]) -> str:
    verdict = str(first.get("verdict") or "inconclusive")
    tone = {
        "skill_effective": "var(--green)",
        "negative_migration": "#dc2626",
        "inconclusive": "#64748b",
    }.get(verdict, "#64748b")
    target_skill = html.escape(str(payload.get("target_skill") or "未指定"))
    tested_models = payload.get("tested_models", [])
    tested_line = "、".join(str(m) for m in tested_models) if tested_models else "暂无"
    return f"""
    <header class='hero'>
      <div class='hero-top'>
        <div>
          <div class='section-note' style='color:#94a3b8;'>被测 Skill：{target_skill} · 测试模型：{html.escape(tested_line)}</div>
          <h1 style='font-size:clamp(26px,4vw,38px);line-height:1.25;margin:8px 0 10px;'>
            {html.escape(str(first.get("headline") or ""))}
          </h1>
          <p style='margin:0;font-size:15px;line-height:1.75;color:#cbd5e1;max-width:760px;'>
            {html.escape(str(first.get("detail") or ""))}
          </p>
        </div>
        <div class='verdict-badge' style='background:{tone};'>{html.escape(str(first.get("verdict_label") or ""))}</div>
      </div>
    </header>
    """


def _render_first_screen_kpis(first: dict[str, Any]) -> str:
    cards = []
    for kpi in first.get("kpis") or []:
        tone = kpi.get("tone") or ""
        cls = "kpi" + (f" {tone}" if tone in {"good", "warn"} else "")
        cards.append(
            f"<div class='{cls}'><span>{html.escape(str(kpi.get('label', '')))}</span>"
            f"<strong>{html.escape(str(kpi.get('value', '')))}</strong>"
            f"<p>{html.escape(str(kpi.get('hint', '')))}</p></div>"
        )
    return f"<div class='kpi-grid'>{''.join(cards)}</div>"


def _render_case_checklist(first: dict[str, Any], limit: int = 12) -> str:
    cases = first.get("cases") or []
    if not cases:
        return "<p class='small-reason'>这次没有自动检查的逐条记录（可能未生成规则快照）。</p>"
    passed = int(first.get("cases_passed", 0))
    failed = int(first.get("cases_failed", 0))
    head = (
        f"<p class='section-note' style='margin:0 0 10px;'>"
        f"自动检查 {len(cases)} 条：<strong style='color:#047857;'>通过 {passed} 条</strong> · "
        f"<strong style='color:#b91c1c;'>失败 {failed} 条</strong></p>"
    )
    items = []
    for case in cases[:limit]:
        ok = bool(case.get("passed"))
        icon = "✓" if ok else "✗"
        color = "#047857" if ok else "#b91c1c"
        bg = "var(--green-soft)" if ok else "#fef2f2"
        border = "var(--green-border)" if ok else "#fecaca"
        items.append(
            f"<li style='list-style:none;padding:8px 12px;border:1px solid {border};"
            f"background:{bg};border-radius:8px;font-size:13px;line-height:1.55;'>"
            f"<strong style='color:{color};'>{icon} {html.escape(str(case.get('name', '')))}</strong>"
            f"<span class='section-note'> · {html.escape(str(case.get('model', '')))}"
            f" · {html.escape(str(case.get('mode', '')))} · 第 {html.escape(str(case.get('repetition', '?')))} 轮</span>"
            f"<div style='margin-top:2px;color:#334155;'>{html.escape(str(case.get('reason', '')))}</div></li>"
        )
    more = ""
    if len(cases) > limit:
        more = f"<p class='small-reason'>还有 {len(cases) - limit} 条，展开下方「按模型看结果」或附录查看。</p>"
    return head + "<ul style='display:grid;gap:8px;padding:0;margin:0;'>" + "".join(items) + "</ul>" + more


def _render_compare_cards(first: dict[str, Any]) -> str:
    models = first.get("models") or []
    if not models:
        return "<p class='small-reason'>没有可对比的模型结果。</p>"
    cards = []
    for item in models:
        tone = {
            "skill_effective": "green",
            "negative_migration": "red",
            "inconclusive": "gray",
        }.get(str(item.get("verdict")), "gray")
        with_votes = int(item.get("with_votes", 0) or 0)
        without_votes = int(item.get("without_votes", 0) or 0)
        total_votes = with_votes + without_votes
        pct = (with_votes / total_votes * 100) if total_votes else 0
        cards.append(
            f"""
      <div class='compare-card'>
        <div class='compare-head'>
          <h3>{html.escape(str(item.get('model', '?')))}</h3>
          <span class='pill {tone}'>{html.escape(str(item.get('verdict_label', '')))}</span>
        </div>
        <div class='compare-grid'>
          <div class='compare-side'>
            <strong style='font-size:13px;color:#047857;'>带 Skill</strong>
            <p style='margin:4px 0 0;font-size:20px;font-weight:800;'>{with_votes} 票</p>
            <span class='section-note'>主交付物 {int(item.get('with_artifacts', 0) or 0)} 个</span>
          </div>
          <div class='compare-side'>
            <strong style='font-size:13px;color:#1d4ed8;'>不带 Skill</strong>
            <p style='margin:4px 0 0;font-size:20px;font-weight:800;'>{without_votes} 票</p>
            <span class='section-note'>主交付物 {int(item.get('without_artifacts', 0) or 0)} 个</span>
          </div>
        </div>
        <div style='padding:0 14px 14px;'>
          <div class='vote-bar' style='margin-top:8px;'><div class='vote-with' style='width:{pct:.0f}%;'>带 {with_votes}</div><div class='vote-without' style='width:{100 - pct:.0f}%;'>不带 {without_votes}</div></div>
          <p class='plain-note' style='margin-top:10px;'>{html.escape(str(item.get('plain', '')))}</p>
        </div>
      </div>
      """
        )
    return "".join(cards)
