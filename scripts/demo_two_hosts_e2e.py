#!/usr/bin/env python3
"""两宿主端到端演示（demo_two_hosts_e2e.py）

宿主 1: opencode —— 真实驱动（OpenCode 桌面版 CLI + alibaba-cn/qwen-flash 真实模型）
宿主 2: script —— mock 宿主（如实标注：真实 claude-code / codex CLI 不在本机 PATH，
        以契约脚本充当被驱动宿主验证调度链路；真实宿主待用户环境验证）

演示内容:
  1. Subject Adapter 契约四件事在两个宿主上各自贯通（注入→投递→收集→加载验证）
  2. with-skill / without-skill 配对执行 + 确定性断言
  3. 报告宿主适用边界标注

产物落盘: demo_p2_two_hosts/<host>/
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from evaluator.engine import (
    EvaluationEngine,
    run_from_plan,
    _classify_execution,
)
from evaluator.executors import create_executor
from evaluator.host_session import run_host_session, resolve_host
from evaluator.subject_adapter import get_subject_adapter, run_subject_contract
from evaluator.models import RunnerConfig
from evaluator.reporting import generate_run_report

DEMO_ROOT = REPO_ROOT / "runs" / "two-hosts-demo"
DEMO_ROOT.mkdir(parents=True, exist_ok=True)

MODEL_FAST = "alibaba-cn/qwen-flash"

SUMMARY = {"started_at": datetime.now().isoformat(), "hosts": {}}


def make_demo_skill(base: Path) -> Path:
    """被测 skill: 生成固定格式的 greeting.json（可被 exact_match 断言客观判定）"""
    skill = base / "greeting-skill"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: greeting-skill\n"
        "description: 生成 greeting.json，内容必须恰好是 {\"greeting\": \"hello from skill\"}\n"
        "---\n"
        "## What I do\n"
        "在当前工作目录创建 artifacts/greeting.json，文件内容必须恰好为（无多余字符）:\n"
        '{"greeting": "hello from skill"}\n'
        "## 要求\n"
        "- 必须创建 artifacts/ 目录并写入 greeting.json\n"
        "- 文件内容必须与上述 JSON 完全一致（一字不差）\n",
        encoding="utf-8",
    )
    return skill


def write_mock_host_script(path: Path) -> None:
    """mock 宿主脚本：行为模拟一个会读 skill 的 agent——with-skill 时按 skill 契约产出，
    without-skill 时产出不符合断言的内容（体现配对差异）。"""
    script = '''#!/usr/bin/env python3
import json, sys, pathlib
payload = json.loads(sys.stdin.read())
cwd = pathlib.Path(payload["cwd"])
prompt = payload.get("prompt") or ""
art = cwd / "artifacts"
art.mkdir(parents=True, exist_ok=True)
# mock 宿主按注入的 skill 目录判断是否"加载"了 skill
skill_loaded = (cwd / ".skills" / "greeting-skill" / "SKILL.md").exists()
if skill_loaded:
    content = '{"greeting": "hello from skill"}'
else:
    content = '{"greeting": "no skill here"}'
(art / "greeting.json").write_text(content, encoding="utf-8")
(art / "summary.md").write_text("# Mock Host\\nskill_loaded=" + str(skill_loaded) + "\\n", encoding="utf-8")
print(json.dumps({
    "exit_code": 0,
    "stdout": "mock host done\\n",
    "stderr": "",
    "transcript": "# Transcript\\nmock host executed\\n",
    "text": "任务完成",
    "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28, "api_call_count": 1},
}, ensure_ascii=False))
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def make_engine(host: str, workspace_root: Path, skill_path: Path, extra: dict | None = None) -> EvaluationConfig:
    from evaluator.models import EvaluationConfig
    kwargs = dict(
        evaluation_name=f"two-hosts-{host}",
        task_prompt='创建 artifacts/greeting.json，内容恰好为 {"greeting": "hello from skill"}',
        workspace_root=str(workspace_root),
        models=[MODEL_FAST if host == "opencode" else "mock-model-a"],
        runners=[RunnerConfig(name=host, command_template=f"{host} run --model {{model}}")],
        blind_review_models=[MODEL_FAST if host == "opencode" else "mock-model-a"],
        target_skill="greeting-skill",
        target_skill_path=str(skill_path),
        host=host,
        execution_mode="cli-attach" if host == "opencode" else "cli",
        use_skill_modes=[False, True],
        repetitions=1,
        max_parallelism=1,
        runner_timeout_seconds=300,
    )
    if host == "script":
        kwargs["host_config"] = extra or {}
    return EvaluationConfig(**kwargs)


def run_subject_demo(host: str, executor, skill_path: Path, base: Path) -> dict:
    """Subject Adapter 契约四件事演示（with-skill 与 without-skill 各一次）"""
    adapter = get_subject_adapter(host)
    out = {"host": host, "pairs": []}
    for use_skill in (True, False):
        ws = base / f"subject-contract-{host}-{'with' if use_skill else 'without'}"
        result = run_subject_contract(
            host=host,
            executor=executor,
            skill_path=skill_path,
            skill_name="greeting-skill",
            model=MODEL_FAST if host == "opencode" else "mock-model-a",
            prompt=(
                '创建 artifacts/greeting.json，内容恰好为 {"greeting": "hello from skill"}。'
                "工作目录已给定，直接在当前目录写文件。"
            ),
            workspace_dir=ws,
            use_skill=use_skill,
            timeout_seconds=300,
        )
        greeting = ws / "artifacts" / "greeting.json"
        content = greeting.read_text(encoding="utf-8").strip() if greeting.exists() else ""
        out["pairs"].append({
            "use_skill": use_skill,
            "workspace": str(ws),
            "loaded_evidence": result["loaded_evidence"],
            "artifacts": result["artifacts"],
            "greeting_content": content,
            "assertion_exact_match": content == '{"greeting": "hello from skill"}',
            "session_exit_code": result["session"]["exit_code"],
        })
    return out


def run_full_pipeline(host: str, base: Path, skill_path: Path, host_config: dict | None = None) -> dict:
    """完整评测链路演示: plan -> run_from_plan -> manifest -> 报告（宿主边界）"""
    base.mkdir(parents=True, exist_ok=True)
    cfg = make_engine(host, base / "runs", skill_path, host_config)
    (base / "eval-config.json").write_text("{}", encoding="utf-8")
    engine = EvaluationEngine(cfg, base / "eval-config.json")
    plan_result = engine.plan_only()
    run_dir = Path(plan_result["run_dir"])

    result_dir = run_from_plan(run_dir, dry_run=False, skip_blind_review=True)
    manifest = json.loads((result_dir / "run_manifest.json").read_text(encoding="utf-8"))

    # 报告生成（含宿主适用边界）
    report_path = generate_run_report(run_dir)

    results_summary = []
    for r in manifest.get("results", []):
        greeting = Path(r["workspace_dir"]) / "artifacts" / "greeting.json"
        content = greeting.read_text(encoding="utf-8").strip() if greeting.exists() else ""
        results_summary.append({
            "runner": r["runner_name"],
            "model": r["model"],
            "use_skill": r["use_skill"],
            "success": r["success"],
            "status": r["status"],
            "skill_loaded": r.get("skill_loaded"),
            "greeting_content": content,
            "assertion_exact_match": content == '{"greeting": "hello from skill"}',
            "loading_evidence": r.get("loading_evidence", {}),
        })
    return {
        "run_dir": str(result_dir),
        "manifest_host": manifest.get("host"),
        "results": results_summary,
        "report": str(report_path),
    }


def main() -> int:
    demo_base = DEMO_ROOT
    demo_base.mkdir(parents=True, exist_ok=True)
    skill_path = make_demo_skill(demo_base / "demo-skill-src")

    # ---------- 宿主 1: opencode（真实驱动） ----------
    print("=" * 70)
    print("宿主 1: opencode（真实驱动: OpenCode 桌面 CLI + alibaba-cn/qwen-flash）")
    print("=" * 70)
    try:
        oc_exec = resolve_host("opencode", "cli-attach")
        print(f"CLI: {oc_exec.config.get('cli_path') if hasattr(oc_exec, 'config') else 'auto'}")
        subject = run_subject_demo("opencode", oc_exec, skill_path, demo_base / "opencode")
        SUMMARY["hosts"]["opencode"] = {
            "mode": "真实驱动（OpenCode 桌面版 CLI 2.0.24 + alibaba-cn/qwen-flash 真实模型）",
            "subject_adapter": subject,
        }
        for pair in subject["pairs"]:
            print(f"  use_skill={pair['use_skill']}: loaded={pair['loaded_evidence']['loaded']}, "
                  f"greeting={pair['greeting_content'][:60]!r}, 断言={pair['assertion_exact_match']}")
    except Exception as e:
        print(f"  ❌ opencode Subject Adapter 演示失败: {type(e).__name__}: {e}")
        SUMMARY["hosts"]["opencode"] = {"mode": "真实驱动", "error": f"{type(e).__name__}: {e}"}

    try:
        pipeline = run_full_pipeline("opencode", demo_base / "opencode-pipeline", skill_path)
        SUMMARY["hosts"]["opencode"]["pipeline"] = pipeline
        for r in pipeline["results"]:
            print(f"  pipeline: runner={r['runner']}, model={r['model']}, use_skill={r['use_skill']}, "
                  f"success={r['success']}, status={r['status']}, 断言={r['assertion_exact_match']}")
        print(f"  报告: {pipeline['report']}")
    except Exception as e:
        print(f"  ❌ opencode pipeline 失败: {type(e).__name__}: {e}")
        SUMMARY["hosts"]["opencode"]["pipeline_error"] = f"{type(e).__name__}: {e}"

    # ---------- 宿主 2: script（mock 宿主，如实标注） ----------
    print("=" * 70)
    print("宿主 2: script（mock 宿主——真实 claude-code/codex CLI 不在本机 PATH，如实标注）")
    print("=" * 70)
    script_path = demo_base / "mock_host_script.py"
    write_mock_host_script(script_path)
    script_exec = create_executor({"type": "script", "script": str(script_path)})
    subject = run_subject_demo("script", script_exec, skill_path, demo_base / "script")
    SUMMARY["hosts"]["script"] = {
        "mode": "mock 宿主（契约脚本充当被驱动宿主；真实 claude-code/codex CLI 不在本机 PATH，待用户环境验证）",
        "subject_adapter": subject,
    }
    for pair in subject["pairs"]:
        print(f"  use_skill={pair['use_skill']}: loaded={pair['loaded_evidence']['loaded']}, "
              f"greeting={pair['greeting_content'][:60]!r}, 断言={pair['assertion_exact_match']}")

    pipeline = run_full_pipeline("script", demo_base / "script-pipeline", skill_path,
                                 host_config={"script": str(script_path)})
    SUMMARY["hosts"]["script"]["pipeline"] = pipeline
    for r in pipeline["results"]:
        print(f"  pipeline: runner={r['runner']}, model={r['model']}, use_skill={r['use_skill']}, "
              f"success={r['success']}, status={r['status']}, 断言={r['assertion_exact_match']}")
    print(f"  报告: {pipeline['report']}")

    SUMMARY["finished_at"] = datetime.now().isoformat()
    out_path = DEMO_ROOT / "two_hosts_demo_summary.json"
    out_path.write_text(json.dumps(SUMMARY, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n演示摘要已落盘: {out_path}")

    # 演示判定标准（两宿主经 Subject Adapter 端到端跑通）:
    #   1. 两宿主 Subject Adapter 四契约均贯通（with-skill: 注入+加载验证+投递+产物收集）
    #   2. 防假对照：without-skill 工作区加载验证必须为 False（隔离证据）
    #   3. 完整 pipeline 在两宿主上执行成功、断言引擎真实判定、宿主边界报告生成
    # 注: without-skill 的产物内容是否命中断言取决于任务对 skill 的依赖度，
    #     本 demo 任务措辞已包含精确答案（对 baseline 友好），不作为判定项；
    #     skill 增益 Δ 的统计结论需充分样本量（repetitions>=3），本 demo 不声称增益。
    ok = True
    notes = []
    for host, data in SUMMARY["hosts"].items():
        subj = data.get("subject_adapter") or {}
        pairs = subj.get("pairs", [])
        if len(pairs) != 2:
            ok = False
            notes.append(f"{host}: subject pairs 不完整")
            continue
        with_p, without_p = pairs
        if not (with_p["loaded_evidence"]["loaded"] and with_p["assertion_exact_match"]):
            ok = False
            notes.append(f"{host}: with-skill 加载验证/断言未通过")
        if without_p["loaded_evidence"]["loaded"]:
            ok = False  # 防假对照：without 工作区不应报告 skill 已加载
            notes.append(f"{host}: without-skill 隔离证据异常")
    for host in SUMMARY["hosts"]:
        results = (SUMMARY["hosts"][host].get("pipeline") or {}).get("results", [])
        if len(results) != 2:
            ok = False
            notes.append(f"{host}: pipeline 结果不完整")
            continue
        for r in results:
            if not r["success"]:
                ok = False
                notes.append(f"{host}: pipeline 执行失败 use_skill={r['use_skill']}")
            if r["use_skill"] and not r["assertion_exact_match"]:
                ok = False
                notes.append(f"{host}: with-skill 断言未通过")
        if not (SUMMARY["hosts"][host].get("pipeline") or {}).get("report"):
            ok = False
            notes.append(f"{host}: 宿主边界报告未生成")
    print("\n" + ("✅ 两宿主端到端演示全部通过" if ok else "⚠️ 演示存在未通过项: " + "; ".join(notes)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
