"""reporting.py - 报告渲染入口（三层结构之「渲染入口」）

R1：payload 组装在 report_payload.py，模板在 report_template.py。
本模块只保留渲染入口与匿名报告，并向后兼容旧调用方（re-export）。
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path
from typing import Any

from .io_utils import write_text
from .result_files import select_result_files

LOGGER = logging.getLogger(__name__)


from .report_payload import (  # noqa: F401  (向后兼容 re-export)
    build_first_screen_payload,
    check_report_self_contained,
    load_blind_vote_data,
    _summarize_vote_reasons,
    safe_read_text,
    safe_read_json,
    clean_console_text,
    safe_len,
    flatten_key_metrics,
    summarize_metrics,
    _resolve_workspace_file,
    build_result_payload,
    assign_anonymous_ids,
    build_group_payload,
    _load_insight_data,
    build_report_payload,
    _dedupe_nonempty,
    _load_p1_payload,
)
from .report_template import (  # noqa: F401  (向后兼容 re-export)
    _render_case_checklist,
    _render_compare_cards,
    _render_first_screen_hero,
    _render_first_screen_kpis,
    _compact,
    _list_text,
    _safe_int,
    _format_seconds,
    _format_tokens,
    _artifact_abs_path,
    _artifact_lookup,
    _hydrate_artifact,
    _artifact_href,
    _artifact_kind,
    _artifact_priority,
    _split_artifacts,
    _read_text_preview,
    _xlsx_preview,
    _csv_preview,
    _pptx_preview,
    _render_table,
    _render_slide_deck,
    _render_artifact_preview,
    _render_artifact_stack,
    _render_diagnostics,
    _mode_reason,
    _fallback_mode_review,
    _mode_review_text,
    _group_verdict,
    _report_css,
    _report_js,
    _verdict_pill,
    _render_plain_summary_section,
    _extract_scheme_evals,
    _side_judge_eval_html,
    _render_model_side,
    _process_evidence_artifacts,
    _render_process_evidence,
    _render_model_result_sections,
    _render_primary_delivery_preview,
    _render_p1_negative_alerts,
    _render_p1_pair_details,
    _render_statistics_section,
    _render_snapshot_section,
)


def render_report_html(payload: dict[str, Any]) -> str:
    """R2：结论先行首屏（verdict 大字 → 三指标 → 红绿清单 → 有/无对比卡片），明细折叠。"""
    first = build_first_screen_payload(payload)
    model_result_html = _render_model_result_sections(payload)
    stats_html = _render_statistics_section(payload.get("statistics"))
    snapshot_html = _render_snapshot_section(payload.get("assertions_snapshot"))
    p1_payload = payload.get("p1")
    p1_alerts_html = _render_p1_negative_alerts(p1_payload)
    p1_details_html = _render_p1_pair_details(p1_payload)

    # ② 负迁移警报：只在真的检出负迁移时展示，且排在对比卡片之前
    neg_alert_block = (
        f"<section class='section' style='border:2px solid #dc2626;'>"
        f"<div class='section-head'><h2>⚠️ 这些模型上带 skill 反而更差</h2>"
        f"<span class='section-note'>建议在这些模型上关闭</span></div>{p1_alerts_html}</section>"
        if p1_alerts_html else ""
    )

    host_boundary = payload.get("host_boundary") or {}
    tested_hosts = "、".join(host_boundary.get("tested_hosts", []) or []) or "未记录"
    host_line = (
        "<p class='section-note' style='margin:14px 0 0;'>"
        f"本次使用的 AI 工具：<strong>{html.escape(tested_hosts)}</strong>。"
        f"{html.escape(str(host_boundary.get('statement', '')))}</p>"
    )

    return f"""<!DOCTYPE html>
<html lang='zh-CN'>
<head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Skill 评测报告 - {html.escape(str(payload.get('evaluation_name', '')))}</title><style>{_report_css()}</style></head>
<body><main class='page'>
{_render_first_screen_hero(first, payload)}
<section class='section'>{_render_first_screen_kpis(first)}{host_line}</section>
{neg_alert_block}
<section class='section'><div class='section-head'><h2>✅❌ 自动检查结果（逐条）</h2><span class='section-note'>绿色=通过，红色=没通过；每条都说人话原因</span></div>{_render_case_checklist(first)}</section>
<section class='section'><div class='section-head'><h2>带 Skill vs 不带 Skill</h2><span class='section-note'>每个模型一张卡：评委票数 + 一句人话结论</span></div>{_render_compare_cards(first)}</section>
<details class='tech-section'><summary>按模型看完整结果（评委评价、交付物对照、诊断）</summary><div class='tech-section-body'>
<section class='section'>{model_result_html}</section>
</div></details>
<details class='tech-section'><summary>配对明细与统计口径（点开看完整数据）</summary><div class='tech-section-body'>
<section class='section'><div class='section-head'><h2>配对明细与评委打分一致性</h2><span class='section-note'>每对任务 × 每轮投票的差值、评委看法一致的比例与需要人工复核的记录</span></div>{p1_details_html}</section>
<section class='section'><div class='section-head'><h2>统计口径明细（复核用）</h2><span class='section-note'>对比了几组、平均差值、波动大小等，仅作复核</span></div>{stats_html}</section>
</div></details>
<details class='tech-section'><summary>附录（自动检查规则存档 / 证据链）</summary><div class='tech-section-body'>
<section class='section'><div class='section-head'><h2>自动检查规则存档</h2><span class='section-note'>同一份产出重查多少遍结果都一样，可复验</span></div>{snapshot_html}</section>
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

