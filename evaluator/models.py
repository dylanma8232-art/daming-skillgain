from __future__ import annotations

import logging

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .paired_gain import DEFAULT_REPETITIONS

LOGGER = logging.getLogger(__name__)


@dataclass
class RunnerConfig:
    name: str
    command_template: str
    enabled: bool = True
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class AnalysisConfig:
    success_keywords: list[str] = field(default_factory=list)
    artifact_expectations: list[str] = field(default_factory=list)


@dataclass
class EvaluationConfig:
    evaluation_name: str
    task_prompt: str
    workspace_root: str
    models: list[str]
    runners: list[RunnerConfig]
    blind_review_models: list[str] = field(default_factory=list)
    summary_model: str = ""  # 负责生成洞察结论的模型
    input_files: list[str] = field(default_factory=list)
    requires_test_data: bool = False
    attach_input_files: bool = True
    max_parallelism: int = 1
    use_skill_modes: list[bool] = field(default_factory=lambda: [False])
    runner_timeout_seconds: int = 600
    execution_mode: str = "cli-attach"
    host: str = ""  # P2: 被驱动的宿主（opencode / claude-code / codex / script...）；空则按 execution_mode 默认映射
    host_config: dict[str, Any] = field(default_factory=dict)  # P2: 宿主执行器专属配置（如 script 的宿主脚本路径）
    max_retries: int = 2  # 基础设施异常 (invalid) 最大重试次数
    repetitions: int = 3  # P1: 每组 (skill, model, mode) 重复次数，默认 3
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    target_skill: str = ""
    target_skill_path: str = ""
    resource_requirements: list[dict[str, Any]] = field(default_factory=list)
    preflight: dict[str, Any] = field(default_factory=dict)
    evaluation_contract: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvaluationConfig":
        runners = [RunnerConfig(**item) for item in raw.get("runners", [])]
        analysis = AnalysisConfig(**raw.get("analysis", {}))
        return cls(
            evaluation_name=raw["evaluation_name"],
            target_skill=raw.get("target_skill", ""),
            target_skill_path=raw.get("target_skill_path", ""),
            task_prompt=raw["task_prompt"],
            workspace_root=raw["workspace_root"],
            models=list(raw.get("models", [])),
            blind_review_models=list(raw.get("blind_review_models", [])),
            summary_model=raw.get("summary_model", ""),
            runners=runners,
            input_files=list(raw.get("input_files", [])),
            requires_test_data=bool(raw.get("requires_test_data", False)),
            attach_input_files=bool(raw.get("attach_input_files", True)),
            max_parallelism=max(1, int(raw.get("max_parallelism", 1))),
            use_skill_modes=list(raw.get("use_skill_modes", [False])),
            runner_timeout_seconds=int(raw.get("runner_timeout_seconds", 600)),
            execution_mode=raw.get("execution_mode", "cli-attach"),
            host=str(raw.get("host", "") or ""),
            host_config=dict(raw.get("host_config", {}) or {}),
            max_retries=int(raw.get("max_retries", 2)),
            repetitions=max(1, int(raw.get("repetitions", DEFAULT_REPETITIONS))),
            analysis=analysis,
            resource_requirements=list(raw.get("resource_requirements", [])),
            preflight=dict(raw.get("preflight", {})),
            evaluation_contract=dict(raw.get("evaluation_contract", {})),
        )

    def validate(self, config_dir: Path) -> None:
        if not self.evaluation_name.strip():
            raise ValueError("evaluation_name 不能为空")
        if not self.task_prompt.strip():
            raise ValueError("task_prompt 不能为空")
        if not self.models:
            raise ValueError("models 至少需要一个基础模型")
        if not self.blind_review_models:
            raise ValueError("blind_review_models 至少需要一个评委模型")
        if self.max_parallelism < 1:
            raise ValueError("max_parallelism 必须大于等于 1")
        if not self.use_skill_modes:
            raise ValueError("use_skill_modes 至少需要一个取值")
        if self.repetitions < 1:
            raise ValueError("repetitions 必须 >= 1")
        if self.repetitions < 3:
            LOGGER.warning("repetitions=%s < 3: 配对样本量不足，统计结论大概率落在 inconclusive 区间", self.repetitions)
        if not any(runner.enabled for runner in self.runners):
            raise ValueError("至少需要一个启用的 runner")
        if any(self.use_skill_modes):
            if not self.target_skill.strip():
                raise ValueError("启用 skill 模式时 target_skill 不能为空")
            target_path = (config_dir / self.target_skill_path).resolve()
            if not target_path.exists():
                raise ValueError(f"target_skill_path 不存在: {target_path}")
        blocking_issues = self.preflight.get("blocking_issues", [])
        if blocking_issues:
            raise ValueError("preflight 存在阻塞问题，无法执行评测")
        for input_file in self.input_files:
            resolved_file = (config_dir / input_file).resolve()
            if not resolved_file.exists():
                raise ValueError(f"input_files 中的文件不存在: {resolved_file}")
        for runner in self.runners:
            if runner.enabled and not runner.command_template.strip():
                raise ValueError(f"runner {runner.name} 的 command_template 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentPlan:
    runner_name: str
    model: str
    use_skill: bool
    repetition: int = 1  # P1: 重复轮次（同任务/同输入/同条件）
    prompt_text: str = ""
    input_files: list[Path] = field(default_factory=list)
    workspace_dir: Path = Path(".")
    artifacts_dir: Path = Path("./artifacts")
    prompt_file: Path = Path("prompt.md")
    transcript_file: Path = Path("transcript.md")
    stdout_file: Path = Path("stdout.txt")
    stderr_file: Path = Path("stderr.txt")
    metadata_file: Path = Path("metadata.json")
    command_file: Path = Path("command.txt")


@dataclass
class ExecutionMetrics:
    start_time: str | None = None
    end_time: str | None = None
    wall_clock_seconds: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    api_call_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {}
        if self.start_time is not None:
            result["start_time"] = self.start_time
        if self.end_time is not None:
            result["end_time"] = self.end_time
        if self.wall_clock_seconds is not None:
            result["wall_clock_seconds"] = self.wall_clock_seconds
        if self.prompt_tokens is not None:
            result["prompt_tokens"] = self.prompt_tokens
        if self.completion_tokens is not None:
            result["completion_tokens"] = self.completion_tokens
        if self.total_tokens is not None:
            result["total_tokens"] = self.total_tokens
        if self.api_call_count is not None:
            result["api_call_count"] = self.api_call_count
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ExecutionMetrics":
        return cls(
            start_time=raw.get("start_time"),
            end_time=raw.get("end_time"),
            wall_clock_seconds=raw.get("wall_clock_seconds"),
            prompt_tokens=raw.get("prompt_tokens"),
            completion_tokens=raw.get("completion_tokens"),
            total_tokens=raw.get("total_tokens"),
            api_call_count=raw.get("api_call_count"),
        )



@dataclass
class ExperimentResult:
    runner_name: str
    model: str
    use_skill: bool
    exit_code: int | None
    success: bool
    status: str
    failure_reason: str | None
    workspace_dir: str
    stdout_file: str
    stderr_file: str
    transcript_file: str
    artifacts: list[str]
    analysis: dict[str, Any]
    repetition: int = 1  # P1: 重复轮次
    execution_metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    is_valid: bool = True  # 区分 invalid (基础设施失败不计入分母) 与 failure (能力不足计入分母)
    skill_loaded: bool = False  # 隔离验证证据: with-skill 须为 True，without-skill 须为 False
    loading_evidence: dict[str, Any] = field(default_factory=dict)
    retry_attempts: list[dict[str, Any]] = field(default_factory=list)
