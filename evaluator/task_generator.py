from __future__ import annotations

from pathlib import Path

from .skill_analyzer import SkillAnalysis


def generate_task_prompt(analysis: SkillAnalysis, test_data_files: list[dict[str, str]], user_task: str = "", resources_json: str | None = None) -> str:
    """生成任务 prompt。

    只包含：用户需求 + 测试数据文件名。
    skill 流程信息在 .opencode/skills/ 里，模型会自动发现，不需要在这里重复。
    """
    contract = analysis.evaluation_contract or {}

    # 任务描述：用户实际需求，或自动生成
    if user_task:
        base_task = user_task
    else:
        base_task = contract.get("task_outline") or _generate_generic_task(analysis)
        # 如果生成了测试数据，在任务描述中引用实际文件名
        data_filenames = [Path(f["path"]).name for f in test_data_files if f["type"] != "task_description"]
        if data_filenames:
            files_text = "、".join(f"`{fn}`" for fn in data_filenames[:5])
            base_task += f"\n\n测试数据文件：{files_text}，文件位于当前工作目录，可直接读取。"

    parts = [base_task]

    # 执行模式提示
    if analysis.execution_mode == "interactive":
        parts.append("")
        parts.append("## 说明")
        parts.append("如果在执行过程中需要用户确认或输入，请基于合理判断自主做出决策。")

    # 资源状态
    if resources_json:
        try:
            import json
            resources = json.loads(resources_json)
            if resources:
                parts.append("")
                parts.append("## 可用资源")
                for r in resources:
                    name = r.get("name") or r.get("type") or "资源"
                    status = "已提供" if r.get("value") else ("已生成" if r.get("status") == "generated" else "使用默认值")
                    parts.append(f"- **{name}**: {status}")
        except Exception:
            pass

    return "\n".join(parts)


def _generate_generic_task(analysis: SkillAnalysis) -> str:
    """生成通用任务描述，引用 skill 的核心能力。"""
    if analysis.capability.core_actions:
        actions = "、".join(analysis.capability.core_actions[:4])
        return f"执行以下流程：{actions}，并保留完整执行记录和最终产出。"
    return f"完成 {analysis.skill_name} 设计目标所对应的完整工作流程。"


