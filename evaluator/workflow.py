"""标准化评测工作流 - 任何 agent 都能执行"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

from .executors import create_executor, BaseExecutor, ExecutionResult
from .skill_analyzer import analyze_skill
from .models import EvaluationConfig


def default_stage_confirm(
    stage_num: int, stage_name: str, stage_summary: str, auto: bool = False
) -> bool:
    """渐进式批路默认停盘确认函数 (用户硬性要求: 默认4阶段停盘等待回车确认，--auto一键到底)"""
    if auto:
        return True

    print("\n" + "=" * 60, flush=True)
    print(f"⏸️  [阶段 {stage_num}/4 完成: {stage_name}]", flush=True)
    if stage_summary:
        print(f"   {stage_summary}", flush=True)
    print("   👉 按 [Enter] 继续执行下一阶段，或输入 'q' / 'abort' 退出：", flush=True)
    print("=" * 60, flush=True)

    try:
        user_input = input().strip().lower()
        if user_input in ("q", "quit", "abort", "exit"):
            print(f"🛑 已退出评测流程（在阶段 {stage_num}: {stage_name}）", flush=True)
            return False
        return True
    except (EOFError, KeyboardInterrupt):
        return True


class SkillEvaluationWorkflow:
    """标准化的评测流程"""

    def __init__(self, executor: BaseExecutor = None, executor_config: dict = None):
        """初始化

        Args:
            executor: 执行器实例
            executor_config: 执行器配置（如果没有提供 executor）
        """
        if executor:
            self.executor = executor
        elif executor_config:
            self.executor = create_executor(executor_config)
        else:
            # 默认使用 opencode
            self.executor = create_executor({"type": "opencode"})

    def run(self, config: dict[str, Any]) -> dict[str, Any]:
        """执行完整评测流程

        Args:
            config: 评测配置
                - skill_path: 被评测 skill 目录路径
                - task_prompt: 评测任务描述
                - models: 参与评测的模型列表
                - voting_models: 评委模型列表
                - summary_model: 总结模型
                - input_files: 输入文件列表（可选）
                - execution_mode: 执行模式（可选）

        Returns:
            dict: 评测结果
                - success: 是否成功
                - run_dir: 运行目录
                - summary: 评测结论
                - report_path: 报告路径
                - error: 错误信息（如果失败）
        """
        try:
            auto = bool(config.get("auto", False))
            confirm_fn = config.get("confirm_func") or default_stage_confirm

            # 步骤 1：验证配置
            self._validate_config(config)

            # 步骤 2：分析 skill
            skill_analysis = self._analyze_skill(config["skill_path"])

            # 步骤 3：验证资源
            self._validate_resources(skill_analysis, config)

            # 步骤 4：生成评测计划
            plan_dir = self._generate_plan(config)
            if not confirm_fn(
                1,
                "评测计划生成完毕",
                f"计划已保存到 {plan_dir}。下一步：按矩阵开跑实验（每个模型各带/不带 skill 跑一遍）。",
                auto=auto,
            ):
                return {
                    "success": False,
                    "aborted": True,
                    "stage": 1,
                    "run_dir": str(plan_dir),
                }

            # 步骤 5：执行实验
            self._execute_experiments(plan_dir, config)
            if not confirm_fn(
                2,
                "实验全部跑完",
                "所有实验都执行完了，自动检查也逐项核过了。下一步：请评委匿名打分（评委不知道哪份是带 skill 的）。",
                auto=auto,
            ):
                return {
                    "success": False,
                    "aborted": True,
                    "stage": 2,
                    "run_dir": str(plan_dir),
                }

            # 步骤 6：盲评
            self._blind_vote(plan_dir, config)
            if not confirm_fn(
                3,
                "评委打分完成",
                "评委打分和打分理由都存好了。下一步：汇总结论并生成 HTML 报告。",
                auto=auto,
            ):
                return {
                    "success": False,
                    "aborted": True,
                    "stage": 3,
                    "run_dir": str(plan_dir),
                }

            # 步骤 7：生成结论
            summary = self._generate_summary(plan_dir, config)

            # 步骤 8：报告生成前确认
            if not confirm_fn(
                4,
                "准备生成最终报告",
                "结论已提炼完。下一步：渲染 HTML 报告（第一屏有总结和实际产出对照）。",
                auto=auto,
            ):
                return {
                    "success": False,
                    "aborted": True,
                    "stage": 4,
                    "run_dir": str(plan_dir),
                }

            report_path = self._generate_report(plan_dir)

            return {
                "success": True,
                "run_dir": str(plan_dir),
                "summary": summary,
                "report_path": str(report_path),
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _validate_config(self, config: dict[str, Any]) -> None:
        """验证配置"""
        required = [
            "skill_path",
            "task_prompt",
            "models",
            "voting_models",
            "summary_model",
        ]
        for key in required:
            if key not in config:
                raise ValueError(f"缺少必需参数: {key}")

        # 验证执行器 (dry-run 或跳过连通性测试时不阻塞环境)
        if not config.get("dry_run") and not config.get("skip_connectivity_test"):
            valid, error = self.executor.validate_config()
            if not valid:
                raise ValueError(f"执行器配置无效: {error}")

    def _analyze_skill(self, skill_path: str) -> dict[str, Any]:
        """分析 skill"""
        analysis = analyze_skill(skill_path)
        return analysis.to_dict()

    def _validate_resources(self, skill_analysis: dict, config: dict) -> None:
        """验证资源"""
        # TODO: 实现资源验证
        pass

    def _generate_plan(self, config: dict) -> Path:
        """生成评测计划"""
        from .cli import cmd_plan_generate, PlanGenerateConfig

        plan_config = PlanGenerateConfig(
            skill_name=config.get("skill_name", ""),
            skill_path=config["skill_path"],
            task_prompt=config["task_prompt"],
            input_files=config.get("input_files", []),
            models=config["models"],
            voting_models=config["voting_models"],
            summary_model=config["summary_model"],
            resources_json=config.get("resources_json"),
            parallelism=config.get("parallelism"),
            execution_mode=config.get("execution_mode", "cli-attach"),
            skip_connectivity_test=config.get("skip_connectivity_test", False)
            or bool(config.get("dry_run")),
            allow_generated_task_prompt=config.get(
                "allow_generated_task_prompt", False
            ),
            repetitions=config.get("repetitions"),
            host=config.get("host"),
            host_config=config.get("host_config"),
        )
        result = cmd_plan_generate(plan_config)

        if "error" in result:
            raise RuntimeError(f"生成评测计划失败: {result['error']}")

        return Path(result["run_dir"])

    def _execute_experiments(self, plan_dir: Path, config: dict) -> None:
        """执行实验"""
        from .engine import run_from_plan

        run_from_plan(
            plan_dir,
            dry_run=config.get("dry_run", False),
            parallelism=config.get("parallelism"),
            progress_interval=config.get("progress_interval", 5),
            skip_blind_review=True,
        )

    def _blind_vote(self, plan_dir: Path, config: dict) -> None:
        """盲评（P2: 经统一宿主调度层分发到配置的宿主；dry-run 跳过真实模型调用）"""
        if config.get("dry_run"):
            print("\n🧪 演练模式：跳过评委打分（dry-run 不调用真实模型）", flush=True)
            return
        from .engine import run_blind_vote_phase

        run_blind_vote_phase(
            plan_dir,
            voting_models=config["voting_models"],
            parallelism=config.get("review_parallelism"),
            host=config.get("host"),
        )

    def _generate_summary(self, plan_dir: Path, config: dict) -> dict:
        """生成结论（P2: 经统一宿主调度层分发到配置的宿主；dry-run 跳过真实模型调用）"""
        if config.get("dry_run"):
            return {
                "dry_run": True,
                "overall": "演练模式：跳过总结模型调用",
                "overall_reason": "dry-run 不调用真实模型",
            }
        from .engine import run_summary_stage

        summary = run_summary_stage(
            plan_dir,
            summary_model=config["summary_model"],
            execution_mode=config.get("execution_mode", "cli-attach"),
            host=config.get("host"),
        )

        return summary

    def _generate_report(self, plan_dir: Path) -> Path:
        """生成报告"""
        from .engine import generate_existing_run_report

        result = generate_existing_run_report(plan_dir)
        return Path(result["report_path"])
