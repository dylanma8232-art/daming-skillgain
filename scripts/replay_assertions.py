#!/usr/bin/env python3
"""断言判定重放与验证脚本 (Replay Assertions)

读取包含 assertions_snapshot.json 与 experiments/ 产物的评测运行目录，
使用当前断言引擎重放判定，输出逐条对比与配对增益 Δ 变化。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保导入当前仓库的 evaluator 模块
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from evaluator.assertions import AssertionEngine, AssertionRule, ASSERTION_ENGINE_VERSION


def replay_evaluation(run_dir: Path) -> dict:
    snapshot_path = run_dir / "assertions_snapshot.json"
    if not snapshot_path.exists():
        raise FileNotFoundError(f"未找到断言快照文件: {snapshot_path}")

    with open(snapshot_path, "r", encoding="utf-8") as f:
        snap_data = json.load(f)

    rules = [AssertionRule.from_dict(r) for r in snap_data["rules_snapshot"]]
    engine = AssertionEngine(version=ASSERTION_ENGINE_VERSION)

    exp_dirs = {d.name: d for d in (run_dir / "experiments").iterdir() if d.is_dir()} if (run_dir / "experiments").exists() else {}
    records = snap_data.get("records", [])

    print(f"================================================================================")
    print(f"📊 评测数据断言重放：{run_dir.name}")
    print(f"快照引擎原版本: {snap_data.get('engine_version')}  ->  当前重放引擎版本: {engine.version}")
    print(f"规则总数: {len(rules)}  |  记录总数: {len(records)}")
    print(f"================================================================================\n")

    replayed_records = []
    models_paired = {}

    for idx, rec in enumerate(records):
        model = rec["model"]
        use_skill = rec["use_skill"]
        rep = rec["repetition"]
        mode = "with-skill" if use_skill else "without-skill"

        m_clean = model.replace("/", "_")
        matched_dir = None
        for name, p in exp_dirs.items():
            if m_clean in name and mode in name and f"rep{rep}" in name:
                matched_dir = p
                break

        # 从工作区采集产物内容
        artifacts = {}
        if matched_dir and (matched_dir / "artifacts").exists():
            for art_f in (matched_dir / "artifacts").iterdir():
                if art_f.is_file() and art_f.stat().st_size <= 500 * 1024:
                    try:
                        artifacts[f"artifacts/{art_f.name}"] = art_f.read_text("utf-8", errors="replace")
                    except Exception:
                        pass

        old_res_list = rec.get("results", [])
        old_sdi = next((r.get("score") for r in old_res_list if r.get("name") == "structured_data_integrity"), None)
        old_trace = next((r.get("score") for r in old_res_list if r.get("name") == "execution_trace"), None)

        stdout_text = rec.get("output_text", "")
        suite_res = engine.evaluate_suite(rules, output_text=stdout_text, artifacts=artifacts)

        new_sdi = next((r.score for r in suite_res.results if r.name == "structured_data_integrity"), None)
        new_trace = next((r.score for r in suite_res.results if r.name == "execution_trace"), None)

        record_info = {
            "index": idx,
            "model": model,
            "mode": mode,
            "repetition": rep,
            "old_sdi": old_sdi,
            "new_sdi": new_sdi,
            "old_trace": old_trace,
            "new_trace": new_trace,
            "new_score": suite_res.score,
        }
        replayed_records.append(record_info)

        if model not in models_paired:
            models_paired[model] = {"with": {}, "without": {}}
        target_side = models_paired[model]["with"] if use_skill else models_paired[model]["without"]
        target_side[rep] = suite_res.score

        print(f"[{idx:02d}] {model:28s} {mode:13s} rep{rep}: SDI({old_sdi} -> {new_sdi}) | Trace({old_trace} -> {new_trace}) | 总分={suite_res.score:.1f}")

    print(f"\n================================================================================")
    print(f"📈 模型配对 Skill Gain (Δ = with - without) 重放结果汇总")
    print(f"================================================================================")

    delta_summary = {}
    for model, data in models_paired.items():
        deltas = []
        print(f"\n模型: {model}")
        for r in sorted(set(list(data["with"].keys()) + list(data["without"].keys()))):
            w = data["with"].get(r)
            wo = data["without"].get(r)
            if w is not None and wo is not None:
                d = w - wo
                deltas.append(d)
                print(f"  Repetition {r}: With={w:.1f} vs Without={wo:.1f} -> Δ = {d:+.2f}")
        mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
        delta_summary[model] = {"deltas": deltas, "mean_delta": mean_delta}
        print(f"  ==> 平均增益差值 (Mean Skill Gain Δ): {mean_delta:+.2f}")

    return {
        "engine_version": engine.version,
        "replayed_records": replayed_records,
        "delta_summary": delta_summary,
    }


def main():
    parser = argparse.ArgumentParser(description="评测数据断言重放与校验工具")
    parser.add_argument("--run-dir", type=Path, required=True, help="评测运行目录 (包含 assertions_snapshot.json)")
    args = parser.parse_args()

    if not args.run_dir.exists():
        print(f"错误: 评测目录不存在: {args.run_dir}", file=sys.stderr)
        sys.exit(1)

    replay_evaluation(args.run_dir)


if __name__ == "__main__":
    main()
