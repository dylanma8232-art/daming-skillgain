from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

from dataclasses import asdict

from .engine import (
    EvaluationEngine,
    run_from_plan,
    analyze_existing_run,
    generate_existing_run_report,
    get_opencode_models,
    load_config,
    test_model_connectivity,
    print_connectivity_report,
)
from .logger import configure_logging
from .models import EvaluationConfig, RunnerConfig, AnalysisConfig
from .paired_gain import DEFAULT_REPETITIONS
from .preflight_validators import run_preflight_validators
from .skill_analyzer import SkillAnalysis, SkillResource, analyze_skill
from .test_data_generator import generate_test_data
from .task_generator import generate_task_prompt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="daming-skillgain v7.0.1 - Skill 评测 CLI（想快速上手，请用 simple 子命令）"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- 新手入口：simple 问答式引导（推荐） ----
    simple_parser = subparsers.add_parser(
        "simple",
        help="【推荐新手】问答式引导，只答 4 个问题自动跑完评测",
        description="问答式引导评测：依次问被测 skill、任务、模型、评委，其余参数用默认值，自动串起 plan→run→evaluate→report。",
    )
    simple_parser.add_argument("--skill-path", default="", help="被测 skill 目录（可提前给，给了就跳过提问）")
    simple_parser.add_argument("--task-prompt", default="", help="让 skill 干什么活（可提前给，给了就跳过提问）")
    simple_parser.add_argument("--models", default="", help="用哪些模型测，逗号分隔（可提前给，给了就跳过提问）")
    simple_parser.add_argument("--voting-models", default="", help="评委模型，逗号分隔（可提前给，给了就跳过提问）")
    simple_parser.add_argument("--dry-run", action="store_true", help="演练模式：不真正启动模型，走一遍完整流程")
    simple_parser.add_argument("--auto", action="store_true", help="不停盘确认，一口气跑到底（默认每个阶段回车确认）")

    update_parser = subparsers.add_parser(
        "update-check",
        help="检查 GitHub 上是否有新版本（平时 CLI 启动会自动后台检查，无需手动跑）",
    )

    plan_parser = subparsers.add_parser("plan", help="生成评测计划（高级；新手请用 simple）")
    plan_parser.add_argument("--mode", choices=["explicit", "blind", "both"], default="explicit",
        help="怎么评：先匿名让评委打分再复现（默认 explicit，不用管）")
    plan_parser.add_argument("--init", action="store_true", help="输出需要收集的字段信息（agent 用 question 展示）")
    plan_parser.add_argument("--skill-path", required=True, help="测哪个 skill：skill 目录路径（必填）")
    plan_parser.add_argument("--skill-name", help="skill 名字（不填会自动从 SKILL.md 里读）")
    plan_parser.add_argument("--task-prompt", help="让它干什么活：测试任务描述（必填，除非你明确让工具自动编一个）")
    plan_parser.add_argument("--allow-generated-task-prompt", action="store_true",
        help="我没想好任务，让工具根据 skill 自动编一个")
    plan_parser.add_argument("--input-files", help="skill 干活需要的输入文件，逗号分隔")
    plan_parser.add_argument("--models", help="用哪些模型测，逗号分隔")
    plan_parser.add_argument("--voting-models", help="请谁来当评委（打分模型），逗号分隔，必填")
    plan_parser.add_argument("--summary-model", help="谁来写总结，默认用第一个评委")
    plan_parser.add_argument("--resources", help="收集到的资源列表 (JSON 格式: [{\"description\": \"...\", \"value\": \"...\"}])")
    plan_parser.add_argument("--generate", action="store_true", help="根据传入参数生成评测计划")
    plan_parser.add_argument("--parallelism", type=int, default=None, help="同时跑几个实验，默认自动（约 5），不用管")
    plan_parser.add_argument("--execution-mode", choices=["cli-attach", "cli", "api"], default="cli-attach",
        help="怎么调模型：默认 cli-attach 自动连本地服务，不用管")
    plan_parser.add_argument("--skip-connectivity-test", action="store_true",
        help="跳过开跑前的模型连通检查（不推荐）")
    plan_parser.add_argument("--repetitions", type=int, default=None,
        help="同一个实验重复跑几遍，默认 3 遍（跑得多结论更稳）")
    plan_parser.add_argument("--host", default=None,
        help="用哪个 Agent 工具跑实验 (opencode / claude-code / codex / script 等)，默认 opencode")
    plan_parser.add_argument("--host-config", default=None,
        help="宿主专属配置 (JSON，如 script 宿主: '{\"script\": \"/path/to/host.sh\"}')")

    run_parser = subparsers.add_parser("run", help="执行已有评测计划（高级；新手请用 simple）")
    run_parser.add_argument("--plan-dir", required=True, help="plan 生成的 run 目录路径")
    run_parser.add_argument("--dry-run", action="store_true", help="演练模式：不真正启动模型")
    run_parser.add_argument("--execution-mode", choices=["cli-attach", "cli", "api"], default=None,
        help="怎么调模型：默认沿用计划里的设置，不用管")
    run_parser.add_argument("--parallelism", type=int, default=None,
        help="同时跑几个实验，默认 5，不用管")
    run_parser.add_argument("--review-parallelism", type=int, default=None,
        help="评委同时打几份分，默认 4-6，不用管")
    run_parser.add_argument("--progress-interval", type=float, default=5.0,
        help="多久汇报一次进度（秒），默认 5，不用管")
    run_parser.add_argument("--host", default=None,
        help="用哪个 Agent 工具跑实验，默认沿用计划里的设置")
    run_parser.add_argument("--host-config", default=None,
        help="宿主专属配置 (JSON，如 script 宿主: '{\"script\": \"/path/to/host.sh\"}')")

    analyze_parser = subparsers.add_parser("analyze", help="重建已有 run 目录的汇总分析")
    analyze_parser.add_argument("--run-dir", required=True, help="已有 run 目录路径")

    report_parser = subparsers.add_parser("report", help="为已有 run 目录生成静态 HTML 对比报告")
    report_parser.add_argument("--run-dir", required=True, help="已有 run 目录路径")

    eval_parser = subparsers.add_parser("evaluate", help="对已有评测结果重跑评委打分")
    eval_parser.add_argument("--run-dir", required=True, help="评测 run 目录路径")
    eval_parser.add_argument("--voting-models", help="请谁来当评委（打分模型），逗号分隔")
    eval_parser.add_argument("--review-parallelism", type=int, default=None,
        help="评委同时打几份分，默认 4-6，不用管")

    pipeline_parser = subparsers.add_parser("pipeline", help="端到端评测（高级；4 个阶段停盘确认，新手请用 simple）")
    pipeline_parser.add_argument("--skill-path", required=True, help="测哪个 skill：skill 目录路径")
    pipeline_parser.add_argument("--skill-name", help="skill 名字（不填会自动从 SKILL.md 里读）")
    pipeline_parser.add_argument("--task-prompt", help="让它干什么活：测试任务描述")
    pipeline_parser.add_argument("--allow-generated-task-prompt", action="store_true", help="我没想好任务，让工具自动编一个")
    pipeline_parser.add_argument("--input-files", help="skill 干活需要的输入文件，逗号分隔")
    pipeline_parser.add_argument("--models", help="用哪些模型测，逗号分隔")
    pipeline_parser.add_argument("--voting-models", help="请谁来当评委（打分模型），逗号分隔")
    pipeline_parser.add_argument("--summary-model", help="谁来写总结，默认用第一个评委")
    pipeline_parser.add_argument("--resources", help="资源 JSON")
    pipeline_parser.add_argument("--execution-mode", choices=["cli-attach", "cli", "api"], default="cli-attach")
    pipeline_parser.add_argument("--host", default=None, help="用哪个 Agent 工具跑实验 (opencode / claude-code / codex / script 等)")
    pipeline_parser.add_argument("--host-config", default=None, help="宿主专属配置 (JSON，如 script 宿主: '{\"script\": \"/path/to/host.sh\"}')")
    pipeline_parser.add_argument("--parallelism", type=int, default=None, help="同时跑几个实验，默认自动，不用管")
    pipeline_parser.add_argument("--dry-run", action="store_true", help="演练模式：不真正启动模型")
    pipeline_parser.add_argument("--auto", action="store_true", help="不停盘确认，一口气跑到底（默认每个阶段回车确认）")
    pipeline_parser.add_argument("--repetitions", type=int, default=None,
        help="同一个实验重复跑几遍，默认 3 遍")

    verify_parser = subparsers.add_parser("verify-snapshot", help="复验快照文件（同一份产出重查结果是否一致）")
    verify_parser.add_argument("--snapshot", help="快照文件路径 (assertions_snapshot.json)")
    verify_parser.add_argument("--run-dir", help="包含快照文件的运行目录")

    return parser


def cmd_plan_init(skill_path: str) -> dict:
    detected = get_opencode_models()

    resources = []
    notes = []
    contract = {}
    if skill_path:
        try:
            analysis = analyze_skill(skill_path)
            resources = [
                {
                    "name": r.name,
                    "description": r.description,
                    "type": r.type,
                    "source": r.source,
                    "required": r.required,
                    "blocking": r.blocking,
                    "supports_auto_generation": r.supports_auto_generation,
                    "validation_hint": r.validation_hint,
                    "generation_hint": r.generation_hint,
                    "example": r.example,
                    "sensitivity": r.sensitivity,
                    "confidence": r.confidence,
                    "evidence": r.evidence,
                    "detection_reason": r.detection_reason,
                    "allow_auto_generation": False,
                    "value": "",
                }
                for r in analysis.required_resources
            ]
            notes = list(analysis.evaluation_notes)
            contract = dict(analysis.evaluation_contract)
        except Exception as e:
            print(f"⚠️ 分析 skill 失败: {e}")

    return {
        "action": "collect_evaluation_config",
        "guide": {
            "step1": {"action": "ask_skill_path", "description": "让用户提供被评测 skill 的目录路径"},
            "step2": {"action": "analyze_skill", "description": "读取 SKILL.md，分析 skill 能力、类型、所需资源"},
            "step3": {"action": "handle_resources", "description": "遍历 required_resources：先问用户有没有；没有则判断 AI 能否生成；不能生成则必须用户提供"},
            "step4": {"action": "handle_task_prompt", "description": "必须先询问用户本次评测需求；只有用户明确授权 evaluator 自行生成时，才根据 skill 能力自动生成"},
            "step5": {"action": "select_models", "description": "让用户选择参与评测的模型（可多选）", "options": detected},
            "step6": {"action": "select_voting_models", "description": "盲评为必选流程，让用户选择评委模型（可多选）", "options": detected},
            "step7": {"action": "select_summary_model", "description": "选择一个总结模型，基于评委评价生成结构化洞察结论", "options": detected},
            "step8": {"action": "generate_plan", "description": "调用 plan --generate 提交所有参数"},
        },
        "available_models": detected,
        "required_resources": resources,
        "task_prompt_policy": {
            "required": True,
            "ask_user_first": True,
            "can_auto_generate_if_user_allows": True,
        },
        "evaluation_notes": notes,
        "evaluation_contract": contract,
        "message": "请按步骤收集配置。盲评是必选流程，因此除了参与评测的模型外，也必须提前选好评委模型和总结模型。特别注意 step3：必须先补齐 blocking 资源，再进入 generate；generate 阶段会对文件、环境和部分连接信息做主动 validator 校验。",
    }


def _extract_skill_name_from_path(skill_path: str) -> str:
    skill_md = Path(skill_path) / "SKILL.md"
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8")
        match = re.search(r'^name:\s*(.+)$', content, re.MULTILINE)
        if match:
            return match.group(1).strip()
    return Path(skill_path).name


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resource_label(resource: SkillResource) -> str:
    return resource.name or resource.type or resource.description[:20]


def _find_matching_resource_entry(resource: SkillResource, entries: list[dict], index: int) -> dict:
    if index < len(entries) and isinstance(entries[index], dict):
        entry = entries[index]
        if entry.get("type") == resource.type or entry.get("name") == resource.name:
            return entry

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == resource.name:
            return entry
        if entry.get("type") == resource.type and entry.get("description") == resource.description:
            return entry
        if entry.get("description") == resource.description:
            return entry
    return {}


def _extract_existing_paths(value: Any, cwd: Path) -> list[str]:
    candidates: list[str] = []
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",") if item.strip()]
    else:
        raw_items = []

    for raw in raw_items:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if candidate.exists():
            candidates.append(str(candidate.resolve()))
    return candidates


def _should_generate_test_data(
    analysis: SkillAnalysis,
    resource_entries: list[dict],
    cwd: Path,
    fallback_input_files: list[str],
) -> bool:
    if fallback_input_files:
        return False

    for index, resource in enumerate(analysis.required_resources):
        if resource.type != "file" or not resource.supports_auto_generation:
            continue
        provided = _find_matching_resource_entry(resource, resource_entries, index)
        value = provided.get("value", "")
        if _extract_existing_paths(value, cwd):
            continue
        allow_auto_generation = bool(provided.get("allow_auto_generation"))
        if isinstance(value, str) and value.strip().lower() in {"auto", "generate", "生成", "ai生成"}:
            allow_auto_generation = True
        if not allow_auto_generation:
            continue
        return True
    return False


def _resource_allows_auto_generation(resource: SkillResource, entry: dict, cwd: Path) -> bool:
    if resource.type != "file" or not resource.supports_auto_generation:
        return False
    value = entry.get("value", "")
    if _extract_existing_paths(value, cwd):
        return False
    if bool(entry.get("allow_auto_generation")):
        return True
    if isinstance(value, str) and value.strip().lower() in {"auto", "generate", "生成", "ai生成"}:
        return True
    return False


def _has_resource_value(resource: SkillResource, entry: dict, cwd: Path) -> bool:
    value = entry.get("value", "")
    if resource.type == "file":
        return bool(_extract_existing_paths(value, cwd))
    if isinstance(value, str):
        return bool(value.strip())
    return value not in (None, "", [], {})


def _build_plan_questions(
    analysis: SkillAnalysis,
    resource_entries: list[dict],
    cwd: Path,
    fallback_input_files: list[str],
    task_prompt: str,
    allow_generated_task: bool,
) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []

    if not task_prompt.strip() and not allow_generated_task:
        questions.append({
            "id": "task_prompt",
            "type": "text",
            "required": True,
            "question": "这次想让这个 skill 干什么活？一句话描述就行。",
            "why": "所有模型要在同一个任务下比拼，不能由评测器替你编任务；你要是没想好，也可以明确授权我根据 skill 自动编一个。",
            "example": "请分析销售 CSV，找出销售额趋势、异常订单和可执行建议，并输出分析报告。",
        })

    for index, resource in enumerate(analysis.required_resources):
        entry = _find_matching_resource_entry(resource, resource_entries, index)
        if resource.type == "file":
            if fallback_input_files or _has_resource_value(resource, entry, cwd):
                continue
            if _resource_allows_auto_generation(resource, entry, cwd):
                continue
            question = {
                "id": resource.name or f"resource_{index}",
                "type": "file",
                "required": True,
                "resource_type": resource.type,
                "question": "这个 skill 干活需要输入文件。有现成的就给路径；没有的话我帮你造一份测试数据，报告里会标注是造的，行吗？",
                "why": resource.validation_hint or resource.description,
                "accepted_answers": [
                    "真实测试文件路径，例如 /path/to/sales.csv",
                    "没有真实数据的话，在 resources JSON 里设 allow_auto_generation=true，我帮你造",
                ],
                "can_auto_generate": bool(resource.supports_auto_generation),
                "example": resource.example,
            }
            questions.append(question)
            continue

        if resource.type in {"business_context", "user_confirmation"}:
            continue

        if resource.blocking and not _has_resource_value(resource, entry, cwd):
            questions.append({
                "id": resource.name or f"resource_{index}",
                "type": "secret" if resource.sensitivity == "high" else "text",
                "required": True,
                "resource_type": resource.type,
                "question": f"这个 skill 干活需要「{resource.name or resource.type}」这类信息（账号、钥匙之类的只能你给，我不能编）。请提供一份测试用的。",
                "why": resource.validation_hint or resource.description,
                "can_auto_generate": False,
                "example": resource.example,
            })

    return questions


def _parse_host_config(raw: str | None) -> dict[str, Any] | None:
    """解析 --host-config JSON；空/非法时返回 None（非法时报错由调用方处理）。"""
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _task_generation_allowed(resources_json: str | None, allow_generated_task_prompt: bool) -> bool:
    if allow_generated_task_prompt:
        return True
    if not resources_json:
        return False
    try:
        raw_resources = json.loads(resources_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(raw_resources, list):
        return False
    return any(
        isinstance(item, dict) and bool(item.get("allow_generated_task_prompt"))
        for item in raw_resources
    )


def _generated_data_files(generated_files: list[dict[str, str]]) -> list[dict[str, str]]:
    data_suffixes = {".csv", ".tsv", ".xlsx", ".xls", ".json", ".xml", ".sql", ".db", ".sqlite", ".pdf", ".docx"}
    return [
        item
        for item in generated_files
        if item.get("type") != "task_description" and Path(item.get("path", "")).suffix.lower() in data_suffixes
    ]


def _build_preflight(
    analysis: SkillAnalysis,
    resource_entries: list[dict],
    generated_files: list[dict[str, str]],
    cwd: Path,
    fallback_input_files: list[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    normalized: list[dict[str, Any]] = []
    blocking_issues: list[str] = []
    warnings: list[str] = []
    input_files: list[str] = []

    for index, resource in enumerate(analysis.required_resources):
        provided = _find_matching_resource_entry(resource, resource_entries, index)
        value = provided.get("value", "")
        status = "missing"
        resolved_paths: list[str] = []
        fallback_paths = list(fallback_input_files or [])

        if resource.type == "file":
            resolved_paths = _extract_existing_paths(value, cwd)
            if resolved_paths:
                status = "provided"
                input_files.extend(resolved_paths)
            elif fallback_paths:
                resolved_paths = fallback_paths
                status = "provided"
                input_files.extend(fallback_paths)
            elif resource.supports_auto_generation and generated_files:
                status = "generated"
        else:
            if isinstance(value, str) and value.strip():
                status = "provided"
            elif not resource.blocking:
                status = "defaulted"

        item = {
            "name": resource.name,
            "label": _resource_label(resource),
            "type": resource.type,
            "description": resource.description,
            "source": resource.source,
            "value": value,
            "status": status,
            "required": resource.required,
            "blocking": resource.blocking,
            "supports_auto_generation": resource.supports_auto_generation,
            "validation_hint": resource.validation_hint,
            "generation_hint": resource.generation_hint,
            "sensitivity": resource.sensitivity,
            "example": resource.example,
            "confidence": resource.confidence,
            "evidence": resource.evidence,
            "detection_reason": resource.detection_reason,
            "allow_auto_generation": bool(provided.get("allow_auto_generation")),
            "resolved_paths": resolved_paths,
        }
        normalized.append(item)

        if status == "missing" and (resource.blocking or resource.type == "file"):
            blocking_issues.append(f"{_resource_label(resource)} 缺失：{resource.validation_hint or resource.description}")
        elif status == "generated":
            warnings.append(f"{_resource_label(resource)} 用的是生成的测试数据（不是真实数据），最终报告会标注。")
        elif status == "defaulted":
            if resource.type == "file" and resource.supports_auto_generation:
                warnings.append(
                    f"{_resource_label(resource)} 未提供真实文件，也未明确授权自动生成；评测将不附加该类测试数据。"
                )
            else:
                warnings.append(f"{_resource_label(resource)} 未提供真实信息，评测将使用默认假设，可信度会降低。")

    validator_report = run_preflight_validators(normalized, cwd)
    for item, check in zip(normalized, validator_report["checks"]):
        item["validator_status"] = check["validator_status"]
        item["validator_summary"] = check["summary"]
        item["validator_evidence"] = check["evidence"]
        item["validator_hard_failure"] = check.get("hard_failure", False)

    blocking_issues.extend(validator_report["critical_failures"])
    warnings.extend(validator_report["warnings"])
    blocking_issues = list(dict.fromkeys(blocking_issues))
    warnings = list(dict.fromkeys(warnings))

    preflight = {
        "ready": not blocking_issues,
        "validated": validator_report["summary"]["critical_failures"] == 0,
        "blocking_issues": blocking_issues,
        "critical_failures": validator_report["critical_failures"],
        "warnings": warnings,
        "resource_items": normalized,
        "validator_checks": validator_report["checks"],
        "validator_summary": validator_report["summary"],
        "uses_synthetic_data": any(item["status"] == "generated" for item in normalized),
        "uses_default_assumptions": any(item["status"] == "defaulted" for item in normalized),
    }
    return preflight, input_files


def cmd_plan_generate(
    skill_name: str,
    skill_path: str,
    task_prompt: str,
    input_files: list[str],
    models: list[str],
    voting_models: list[str] | None = None,
    summary_model: str | None = None,
    resources_json: str | None = None,
    parallelism: int | None = None,
    execution_mode: str = "cli-attach",
    skip_connectivity_test: bool = False,
    allow_generated_task_prompt: bool = False,
    repetitions: int | None = None,
    host: str | None = None,
    host_config: dict[str, Any] | None = None,
) -> dict:
    if not skill_path or not skill_path.strip():
        return {"error": "还没告诉我要测哪个 skill。请给我 skill 目录的路径。"}
    if not models:
        return {"error": "models 不能为空"}
    if not voting_models:
        return {"error": "voting_models 不能为空，盲评是必选流程，请至少提供一个评委模型"}
    if not summary_model:
        return {"error": "summary_model 不能为空，总结模型负责基于评委评价生成结构化洞察结论"}

    resolved_skill_path = Path(skill_path)
    if not resolved_skill_path.is_absolute():
        resolved_skill_path = Path.cwd() / resolved_skill_path
    if not resolved_skill_path.exists():
        return {"error": f"skill_path 不存在: {resolved_skill_path}"}
    resolved_skill_path_str = str(resolved_skill_path.resolve())

    if not skill_name or not skill_name.strip():
        skill_name = _extract_skill_name_from_path(resolved_skill_path_str)

    resolved_input_files = []
    for raw_path in input_files or []:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        if not candidate.exists():
            return {"error": f"input_file 不存在: {candidate}"}
        resolved_input_files.append(str(candidate.resolve()))

    safe_name = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", skill_name).strip("-")
    config_dir = Path.cwd()

    skill_analysis = analyze_skill(resolved_skill_path_str)
    resource_data = []
    if resources_json:
        try:
            resource_data = json.loads(resources_json)
        except json.JSONDecodeError:
            return {"error": "resources JSON 格式错误"}
        if not isinstance(resource_data, list):
            return {"error": "resources JSON 必须是数组"}

    allow_generated_task = _task_generation_allowed(resources_json, allow_generated_task_prompt)
    questions = _build_plan_questions(
        skill_analysis,
        resource_data,
        config_dir,
        resolved_input_files,
        task_prompt or "",
        allow_generated_task,
    )
    if questions:
        return {
            "error": "needs_user_input",
            "action": "ask_user",
            "message": "生成评测计划前必须补充用户输入。请先向用户确认评测需求和所需资源，不要静默默认生成。",
            "questions": questions,
            "required_resources": [asdict(r) for r in skill_analysis.required_resources],
            "task_prompt_policy": {
                "required": True,
                "ask_user_first": True,
                "can_auto_generate_if_user_allows": True,
                "allow_generated_task_prompt": allow_generated_task,
            },
            "resource_policy": {
                "file": "有真实文件就给路径；没有的话用户点头（allow_auto_generation=true）我就帮你造一份测试数据，报告里会标注是造的。",
                "credential_account_api": "账号、凭证、API key、数据库连接等不能由 AI 生成，只能使用用户提供的测试信息。",
            },
        }

    # 模型连通性测试：确保所有选中的模型（参与模型 + 评委模型 + 总结模型）均可正常调用。
    # 资源 gate 通过后再测，避免在缺需求/缺数据时浪费调用。
    if not skip_connectivity_test:
        all_test_models = list(set(models + voting_models + ([summary_model] if summary_model else [])))
        print(f"\n🔗 开始模型连通性测试 ({len(all_test_models)} 个模型, mode={execution_mode}, host={host or '默认'})...")
        connectivity = test_model_connectivity(all_test_models, execution_mode=execution_mode, timeout=60, host=host)
        print_connectivity_report(connectivity)

        if not connectivity["all_ok"]:
            failed_list = ", ".join(connectivity["failed"].keys())
            return {
                "error": "connectivity_test_failed",
                "message": f"这几个模型现在联系不上: {failed_list}。检查模型配置后再试一次。",
                "connectivity_report": connectivity,
            }
    else:
        print("\n⚠️  跳过模型连通性测试（不推荐）")

    generated_dir = (config_dir / "_generated_test_data" / safe_name).resolve()
    generated_dir.mkdir(parents=True, exist_ok=True)
    generated_files: list[dict[str, str]] = []
    readme_path = generated_dir / "README.md"
    should_generate_data = _should_generate_test_data(skill_analysis, resource_data, config_dir, resolved_input_files)
    if should_generate_data:
        generated_files, readme_path = generate_test_data(skill_analysis, generated_dir)
        if not _generated_data_files(generated_files):
            return {
                "error": "test_data_generation_failed",
                "message": "你说可以造测试数据，但这次没造出来能用的文件，先停下来了。检查 skill 的输入要求后再试。",
                "generated_files": generated_files,
            }
    preflight, resource_input_files = _build_preflight(
        skill_analysis,
        resource_data,
        generated_files,
        config_dir,
        fallback_input_files=resolved_input_files,
    )
    if not preflight["ready"]:
        return {
            "error": "preflight_failed",
            "message": "开跑前的体检没通过：还缺必要信息。下面列了缺什么，补齐后再跑。",
            "blocking_issues": preflight["blocking_issues"],
            "warnings": preflight["warnings"],
            "required_resources": preflight["resource_items"],
        }

    all_input_files: list[str] = []
    seen_inputs = set()
    for path in resolved_input_files + resource_input_files + [f["path"] for f in generated_files]:
        if path in seen_inputs:
            continue
        seen_inputs.add(path)
        all_input_files.append(path)

    if preflight["resource_items"]:
        config_file = generated_dir / "skill_resources.json"
        config_file.write_text(
            json.dumps({"resources": preflight["resource_items"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        all_input_files.append(str(config_file))

    preflight_path = generated_dir / "preflight.json"
    preflight_path.write_text(json.dumps(preflight, ensure_ascii=False, indent=2), encoding="utf-8")
    all_input_files.append(str(preflight_path))

    # 评测合同和评分规范只保存在评测器运行目录中，不复制到模型工作区
    # 避免模型读到评测标准后"表演"出期望行为

    if not task_prompt:
        task_prompt = generate_task_prompt(skill_analysis, generated_files, resources_json=resources_json)

    config = EvaluationConfig(
        evaluation_name=f"{safe_name}-evaluation",
        target_skill=skill_name,
        target_skill_path=resolved_skill_path_str,
        task_prompt=task_prompt,
        workspace_root="./runs",
        models=models,
        blind_review_models=voting_models,
        summary_model=summary_model or "",
        runners=[RunnerConfig(
            name=host or "opencode",
            enabled=True,
            # P2: command_template 保留为人类可读的展示形态；实际执行经统一宿主调度层分发
            command_template=(host or "opencode") + " run --format json --model {model} -- {prompt_arg}",
            env={},
        )],
        input_files=all_input_files,
        requires_test_data=False,
        attach_input_files=True,
        max_parallelism=parallelism if parallelism else min(5, len(models) * 2),
        use_skill_modes=[False, True],
        repetitions=max(1, int(repetitions or DEFAULT_REPETITIONS)),
        runner_timeout_seconds=1800,
        execution_mode=execution_mode,
        host=host or "",
        host_config=dict(host_config or {}),
        analysis=AnalysisConfig(artifact_expectations=[skill_analysis.skill_name]),
        resource_requirements=[asdict(r) for r in skill_analysis.required_resources],
        preflight=preflight,
        evaluation_contract=skill_analysis.evaluation_contract,
    )

    engine = EvaluationEngine(config, config_dir / "eval-config.json")

    analysis_path = generated_dir / "skill_analysis.json"
    analysis_path.write_text(json.dumps(skill_analysis.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    result = engine.plan_only()

    run_dir = Path(result["run_dir"])

    # 评测合同写入评测器运行目录（模型不可见）
    contract_path = run_dir / "evaluation_contract.json"
    contract_path.write_text(json.dumps(skill_analysis.evaluation_contract, ensure_ascii=False, indent=2), encoding="utf-8")
    result["blind_review"] = {"voting_models": voting_models or []}

    result["skill_analysis"] = {
        "skill_name": skill_analysis.skill_name,
        "estimated_task_type": skill_analysis.estimated_task_type,
        "execution_mode": skill_analysis.execution_mode,
        "capability_summary": skill_analysis.capability.summary,
        "core_actions": skill_analysis.capability.core_actions[:5],
        "tools_required": skill_analysis.tools_required,
        "evaluation_notes": skill_analysis.evaluation_notes,
        "evaluation_contract": skill_analysis.evaluation_contract,
        "required_resources": [
            {
                "type": r.type,
                "source": r.source,
                "description": r.description[:50],
                "blocking": r.blocking,
                "supports_auto_generation": r.supports_auto_generation,
                "confidence": r.confidence,
                "evidence": r.evidence[:80],
            }
            for r in skill_analysis.required_resources
        ],
        "generated_files": generated_files,
        "readme": str(readme_path) if readme_path.exists() else "",
        "analysis_file": str(analysis_path),
        "contract_file": str(contract_path),
    }
    result["preflight"] = preflight

    return result


def _ask(prompt: str, default: str = "") -> str:
    """提问并读取一行输入；直接回车返回默认值。"""
    suffix = f"（直接回车用默认：{default}）" if default else "（必答）"
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = default
    return answer or default


def cmd_simple(args: argparse.Namespace) -> dict:
    """simple 引导模式：问答式收集 4 个必答项，自动串起 plan→run→evaluate→report。

    4 个必答项：
    1. 测哪个 skill（--skill-path）
    2. 让它干什么活（--task-prompt）
    3. 用哪些模型测（--models）
    4. 请谁当评委（--voting-models）
    其余参数全部用合理默认值。pipeline 保留给高级用户。
    """
    from .workflow import SkillEvaluationWorkflow

    print("\n" + "=" * 60)
    print("🎯 Skill 评测引导模式（simple）")
    print("只需要回答 4 个问题，其余全部用默认值，自动跑完评测。")
    print("=" * 60)

    skill_path = (args.skill_path or "").strip()
    if not skill_path:
        skill_path = _ask("\n1/4 测哪个 skill？给我 skill 目录的路径", "")
    else:
        print(f"\n1/4 测哪个 skill？已指定：{skill_path}")
    if not skill_path:
        return {"error": "needs_user_input", "message": "没拿到 skill 目录路径，评测没法开始。请重新运行并提供 skill 目录。"}

    task_prompt = (args.task_prompt or "").strip()
    if not task_prompt:
        task_prompt = _ask("\n2/4 让它干什么活？一句话描述你想让这个 skill 完成的任务", "")
    else:
        print(f"\n2/4 让它干什么活？已指定：{task_prompt[:80]}")
    if not task_prompt:
        return {"error": "needs_user_input", "message": "没拿到任务描述。评测需要知道让 skill 干什么活。请重新运行并描述任务。"}

    models_answer = (args.models or "").strip()
    detected = get_opencode_models()
    default_models = ",".join(detected[:2]) if detected else ""
    if not models_answer:
        models_answer = _ask("\n3/4 用哪些模型测？逗号分隔，比如 gpt-5,claude-4", default_models)
    models = [m.strip() for m in models_answer.split(",") if m.strip()]
    if not models:
        return {"error": "needs_user_input", "message": "没拿到要测的模型。请重新运行并至少指定一个模型。"}
    print(f"\n3/4 用哪些模型测？已选：{', '.join(models)}")

    voting_answer = (args.voting_models or "").strip()
    default_voting = models[0]
    if not voting_answer:
        voting_answer = _ask("\n4/4 请谁当评委？逗号分隔（回车用第一个被测模型兼任）", default_voting)
    voting_models = [m.strip() for m in voting_answer.split(",") if m.strip()]
    if not voting_models:
        voting_models = [models[0]]
    print(f"\n4/4 请谁当评委？已选：{', '.join(voting_models)}")

    print("\n✅ 问题问完了。接下来会自动：生成计划 → 跑实验 → 评委打分 → 生成报告。")
    if not args.auto and not args.dry_run:
        try:
            input("   回车开始，输入 q 退出：")
        except (EOFError, KeyboardInterrupt):
            pass

    wf = SkillEvaluationWorkflow()
    config = {
        "skill_path": skill_path,
        "skill_name": "",
        "task_prompt": task_prompt,
        "allow_generated_task_prompt": False,
        "input_files": [],
        "models": models,
        "voting_models": voting_models,
        "summary_model": voting_models[0],
        "resources_json": None,
        "execution_mode": "cli-attach",
        "host": None,
        "parallelism": None,
        "dry_run": bool(args.dry_run),
        "auto": bool(args.auto) or bool(args.dry_run),
        "repetitions": None,
    }
    result = wf.run(config)
    if result.get("success"):
        print(f"\n🎉 评测完成！报告在这里：{result.get('report_path', '')}")
        print("   用浏览器打开就能看：第一屏有总结和带/不带 skill 的实际产出对照。")
    return result


def main() -> None:
    # 版本自检：后台线程检查 GitHub 新版本（每 24h 最多一次，超时 3s 静默，绝不阻塞主流程）。
    # 有新版提示打在 stderr，避免混入子命令的 JSON 输出。
    try:
        from .update_checker import maybe_check_update_async
        maybe_check_update_async()
    except Exception:
        pass

    configure_logging()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "update-check":
        from .update_checker import maybe_check_update
        notice = maybe_check_update(force=True)
        result = {"checked": True, "update_available": bool(notice), "notice": notice or ""}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "simple":
        result = cmd_simple(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "plan":
        if args.init:
            skill_path = args.skill_path or ""
            result = cmd_plan_init(skill_path)
        elif args.generate:
            input_files = []
            if args.input_files:
                input_files = [f.strip() for f in args.input_files.split(",") if f.strip()]
            models = []
            if args.models:
                models = [m.strip() for m in args.models.split(",") if m.strip()]
            voting_models = []
            if args.voting_models:
                voting_models = [m.strip() for m in args.voting_models.split(",") if m.strip()]
            result = cmd_plan_generate(
                skill_name=args.skill_name or "",
                skill_path=args.skill_path or "",
                task_prompt=args.task_prompt or "",
                input_files=input_files,
                models=models,
                voting_models=voting_models,
                summary_model=args.summary_model,
                resources_json=args.resources,
                parallelism=args.parallelism,
                execution_mode=args.execution_mode,
                skip_connectivity_test=args.skip_connectivity_test,
                allow_generated_task_prompt=args.allow_generated_task_prompt,
                repetitions=getattr(args, "repetitions", None),
                host=getattr(args, "host", None),
                host_config=_parse_host_config(getattr(args, "host_config", None)),
            )
        else:
            print("❌ plan 命令需要 --init 或 --generate 参数", file=sys.stderr)
            result = {"error": "missing --init or --generate flag"}

    elif args.command == "run":
        plan_dir = Path(args.plan_dir).resolve()
        plan_json = plan_dir / "plan.json"
        if not plan_json.exists():
            print(f"❌ 找不到 plan.json，请先执行 plan 命令: {plan_json}", file=sys.stderr)
            result = {"error": "plan.json not found"}
        else:
            if args.execution_mode or getattr(args, "host", None):
                config_snapshot = plan_dir / "config.snapshot.json"
                if config_snapshot.exists():
                    config_data = json.loads(config_snapshot.read_text(encoding="utf-8"))
                    if args.execution_mode:
                        config_data["execution_mode"] = args.execution_mode
                    if getattr(args, "host", None):
                        config_data["host"] = args.host
                    host_cfg = _parse_host_config(getattr(args, "host_config", None))
                    if host_cfg:
                        config_data["host_config"] = host_cfg
                    config_snapshot.write_text(json.dumps(config_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result = {"run_dir": str(run_from_plan(
                plan_dir,
                dry_run=args.dry_run,
                parallelism=args.parallelism,
                review_parallelism=args.review_parallelism,
                progress_interval=args.progress_interval,
            ))}

    elif args.command == "analyze":
        result = analyze_existing_run(Path(args.run_dir).resolve())

    elif args.command == "report":
        result = generate_existing_run_report(Path(args.run_dir).resolve())

    elif args.command == "evaluate":
        run_dir = Path(args.run_dir).resolve()
        detected_models = get_opencode_models()
        if not detected_models:
            manifest_path = run_dir / "run_manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text())
                for r in manifest.get("results", []):
                    model = r.get("model", "")
                    if model and model not in detected_models:
                        detected_models.append(model)

        if not detected_models:
            print("❌ 联系不上任何模型。请确认 opencode 已安装并能用。")
            result = {"error": "no_models_found"}
        elif args.voting_models:
            voting_models = [m.strip() for m in args.voting_models.split(",") if m.strip()]
            if not voting_models:
                result = {"error": "还没告诉我要请哪些评委。请指定至少一个打分模型。"}
            else:
                print(f"\n✅ 已选择 {len(voting_models)} 个评审模型:")
                for m in voting_models:
                    print(f"   - {m}")
                from .engine import run_blind_vote_phase
                result = run_blind_vote_phase(
                    run_dir,
                    voting_models=voting_models,
                    parallelism=args.review_parallelism,
                )
        else:
            # P3: 旧交互分支签名错配已移除（弃用清单）；盲评为必选流程，
            # 请显式传入 --voting-models（逗号分隔），或使用 pipeline 端到端流程。
            result = {
                "action": "select_voting_models",
                "available_models": detected_models,
                "message": "评委打分是必选流程。请用 --voting-models 指定评委模型（逗号分隔）后重试。",
            }

    elif args.command == "pipeline":
        from .workflow import SkillEvaluationWorkflow
        input_files = [f.strip() for f in args.input_files.split(",") if f.strip()] if args.input_files else []
        models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else []
        voting_models = [m.strip() for m in args.voting_models.split(",") if m.strip()] if args.voting_models else []
        detected_models = get_opencode_models()
        if not models:
            models = detected_models[:2] if detected_models else ["default-model"]
        if not voting_models:
            voting_models = [models[0]]
        summary_model = args.summary_model or voting_models[0]
        wf = SkillEvaluationWorkflow()
        config = {
            "skill_path": args.skill_path,
            "skill_name": args.skill_name or "",
            "task_prompt": args.task_prompt or "",
            "allow_generated_task_prompt": args.allow_generated_task_prompt,
            "input_files": input_files,
            "models": models,
            "voting_models": voting_models,
            "summary_model": summary_model,
            "resources_json": args.resources,
            "execution_mode": args.execution_mode,
            "host": getattr(args, "host", None),
            "host_config": _parse_host_config(getattr(args, "host_config", None)),
            "parallelism": args.parallelism,
            "dry_run": args.dry_run,
            "auto": args.auto,
            "repetitions": getattr(args, "repetitions", None),
        }
        result = wf.run(config)

    elif args.command == "verify-snapshot":
        from .assertions import verify_assertions_snapshot_file
        from .paired_gain import verify_p1_judgment_snapshot
        target_path = None
        if args.snapshot:
            target_path = Path(args.snapshot)
        elif args.run_dir:
            # 优先复验 P1 判定快照，其次是 P0 断言快照
            p1_candidate = Path(args.run_dir) / "p1" / "judgment_snapshot.json"
            assertion_candidate = Path(args.run_dir) / "assertions_snapshot.json"
            if p1_candidate.exists():
                target_path = p1_candidate
            else:
                target_path = assertion_candidate
        if not target_path or not target_path.exists():
            result = {"error": f"找不到快照文件: {target_path}"}
        elif target_path.name == "judgment_snapshot.json":
            result = verify_p1_judgment_snapshot(json.loads(target_path.read_text(encoding="utf-8")))
        else:
            result = verify_assertions_snapshot_file(target_path)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
