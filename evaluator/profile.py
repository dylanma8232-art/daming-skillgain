"""EvalProfile - 一次性可复用评测 profile（改造项 A6）

一份 JSON 承载全部评测参数，所有入口（cli simple/pipeline/plan、sdk、mcp）共用：
    {
      "skill_path": "./my-skill",
      "task_prompt": "把这份调研做成 PPT",
      "models": ["gpt-5"],
      "voting_models": ["gpt-5"],
      "summary_model": "gpt-5",
      "resources": [{"description": "账号", "value": "...", "allow_auto_generation": false}],
      "host": "cursor-agent",
      "host_config": {},
      "repetitions": 3
    }

设计约束：
- profile 只是「一次性回答」，不取消三条硬纪律：需求必问 / 敏感资源不生成 / inconclusive
  不强下结论。`resources` 只承载用户已提供的答案，未提供的 blocking 资源仍由
  `_build_plan_questions` 触发 `needs_user_input`。
- Python 3.9 语法红线：保留 `from __future__ import annotations`。
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROFILE_FILENAME = "eval-config.json"

# profile 支持的字段（与 PlanGenerateConfig 对齐；未知字段只告警不报错）
PROFILE_FIELDS: tuple[str, ...] = (
    "skill_path",
    "skill_name",
    "task_prompt",
    "allow_generated_task_prompt",
    "input_files",
    "models",
    "voting_models",
    "summary_model",
    "resources",
    "host",
    "host_config",
    "execution_mode",
    "repetitions",
    "parallelism",
    "skip_connectivity_test",
    "connectivity_timeout",
    "non_interactive",
    "auto",
    "dry_run",
    "output_dir",
)


class ProfileError(ValueError):
    """profile 文件缺失 / 格式错误 / 字段类型不合法。"""


@dataclass
class EvalProfile:
    """可复用评测 profile。"""

    skill_path: str = ""
    skill_name: str = ""
    task_prompt: str = ""
    allow_generated_task_prompt: bool = False
    input_files: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    voting_models: list[str] = field(default_factory=list)
    summary_model: str = ""
    resources: list[dict[str, Any]] = field(default_factory=list)
    host: str = ""
    host_config: dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "cli-attach"
    repetitions: int | None = None
    parallelism: int | None = None
    skip_connectivity_test: bool = False
    connectivity_timeout: int = 60
    non_interactive: bool = False
    auto: bool = True
    dry_run: bool = False
    output_dir: str = ""
    source_path: str = ""
    warnings: list[str] = field(default_factory=list)

    # ---- 载入 / 导出 ------------------------------------------------------
    @classmethod
    def from_dict(cls, raw: dict[str, Any], source_path: str = "") -> "EvalProfile":
        if not isinstance(raw, dict):
            raise ProfileError("profile 必须是 JSON 对象")

        warnings: list[str] = []
        unknown = sorted(set(raw) - set(PROFILE_FIELDS))
        if unknown:
            warnings.append(f"忽略未知字段: {', '.join(unknown)}")

        def _str(key: str) -> str:
            value = raw.get(key)
            return str(value) if value is not None else ""

        def _str_list(key: str) -> list[str]:
            value = raw.get(key)
            if value is None:
                return []
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            raise ProfileError(f"profile 字段 {key} 必须是字符串数组")

        def _int_or_none(key: str) -> int | None:
            value = raw.get(key)
            if value is None or value == "":
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ProfileError(f"profile 字段 {key} 必须是整数")

        resources_raw = raw.get("resources") or []
        if not isinstance(resources_raw, list):
            raise ProfileError("profile 字段 resources 必须是数组")
        resources: list[dict[str, Any]] = []
        for item in resources_raw:
            if not isinstance(item, dict):
                raise ProfileError("resources 数组元素必须是对象")
            resources.append(dict(item))

        host_config = raw.get("host_config") or {}
        if isinstance(host_config, str):
            try:
                host_config = json.loads(host_config)
            except json.JSONDecodeError:
                raise ProfileError("profile 字段 host_config 不是合法 JSON")
        if not isinstance(host_config, dict):
            raise ProfileError("profile 字段 host_config 必须是对象")

        profile = cls(
            skill_path=_str("skill_path"),
            skill_name=_str("skill_name"),
            task_prompt=_str("task_prompt"),
            allow_generated_task_prompt=bool(raw.get("allow_generated_task_prompt", False)),
            input_files=_str_list("input_files"),
            models=_str_list("models"),
            voting_models=_str_list("voting_models"),
            summary_model=_str("summary_model"),
            resources=resources,
            host=_str("host"),
            host_config=dict(host_config),
            execution_mode=_str("execution_mode") or "cli-attach",
            repetitions=_int_or_none("repetitions"),
            parallelism=_int_or_none("parallelism"),
            skip_connectivity_test=bool(raw.get("skip_connectivity_test", False)),
            connectivity_timeout=int(raw.get("connectivity_timeout", 60) or 60),
            non_interactive=bool(raw.get("non_interactive", False)),
            auto=bool(raw.get("auto", True)),
            dry_run=bool(raw.get("dry_run", False)),
            output_dir=_str("output_dir"),
            source_path=source_path,
            warnings=warnings,
        )
        profile.apply_defaults()
        return profile

    def apply_defaults(self) -> None:
        """可自动推断的默认值（协议 §2.2：summary/voting 不再硬卡死）。"""
        if not self.voting_models and self.models:
            self.voting_models = [self.models[0]]
            self.warnings.append(f"voting_models 未提供，默认使用第一个被测模型: {self.models[0]}")
        if not self.summary_model:
            if self.voting_models:
                self.summary_model = self.voting_models[0]
                self.warnings.append(f"summary_model 未提供，默认使用第一个评委: {self.summary_model}")
            elif self.models:
                self.summary_model = self.models[0]
                self.warnings.append(f"summary_model 未提供，默认使用第一个被测模型: {self.summary_model}")

    # ---- 转换 ------------------------------------------------------------
    def resources_json(self) -> str | None:
        return json.dumps(self.resources, ensure_ascii=False) if self.resources else None

    def to_plan_kwargs(self) -> dict[str, Any]:
        """转换为 PlanGenerateConfig 的构造参数。"""
        return {
            "skill_name": self.skill_name,
            "skill_path": self.skill_path,
            "task_prompt": self.task_prompt,
            "input_files": list(self.input_files),
            "models": list(self.models),
            "voting_models": list(self.voting_models),
            "summary_model": self.summary_model,
            "resources_json": self.resources_json(),
            "parallelism": self.parallelism,
            "execution_mode": self.execution_mode,
            "skip_connectivity_test": self.skip_connectivity_test,
            "allow_generated_task_prompt": self.allow_generated_task_prompt,
            "repetitions": self.repetitions,
            "host": self.host or None,
            "host_config": dict(self.host_config) or None,
            "connectivity_timeout": self.connectivity_timeout,
        }

    def to_workflow_config(self, **overrides: Any) -> dict[str, Any]:
        """转换为 SkillEvaluationWorkflow.run(config) 的配置字典。"""
        config: dict[str, Any] = {
            "skill_path": self.skill_path,
            "skill_name": self.skill_name,
            "task_prompt": self.task_prompt,
            "allow_generated_task_prompt": self.allow_generated_task_prompt,
            "input_files": list(self.input_files),
            "models": list(self.models),
            "voting_models": list(self.voting_models),
            "summary_model": self.summary_model,
            "resources_json": self.resources_json(),
            "execution_mode": self.execution_mode,
            "host": self.host or None,
            "host_config": dict(self.host_config) or None,
            "parallelism": self.parallelism,
            "dry_run": self.dry_run,
            "auto": self.auto,
            "non_interactive": self.non_interactive,
            "repetitions": self.repetitions,
            "skip_connectivity_test": self.skip_connectivity_test,
            "connectivity_timeout": self.connectivity_timeout,
        }
        config.update(overrides)
        return config

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_path": self.skill_path,
            "skill_name": self.skill_name,
            "task_prompt": self.task_prompt,
            "allow_generated_task_prompt": self.allow_generated_task_prompt,
            "input_files": list(self.input_files),
            "models": list(self.models),
            "voting_models": list(self.voting_models),
            "summary_model": self.summary_model,
            "resources": [dict(item) for item in self.resources],
            "host": self.host,
            "host_config": dict(self.host_config),
            "execution_mode": self.execution_mode,
            "repetitions": self.repetitions,
            "parallelism": self.parallelism,
            "skip_connectivity_test": self.skip_connectivity_test,
            "connectivity_timeout": self.connectivity_timeout,
            "non_interactive": self.non_interactive,
            "auto": self.auto,
            "dry_run": self.dry_run,
            "output_dir": self.output_dir,
        }

    def save(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    # ---- 预检 ------------------------------------------------------------
    def validate(self) -> list[str]:
        """返回缺失的必填项（结构化 needs_user_input 用，不自行生成）。"""
        missing: list[str] = []
        if not self.skill_path.strip():
            missing.append("skill_path")
        if not self.task_prompt.strip() and not self.allow_generated_task_prompt:
            missing.append("task_prompt")
        if not self.models:
            missing.append("models")
        return missing


def load_profile(path: Path | str) -> EvalProfile:
    """从 JSON 文件载入 profile。"""
    target = Path(path)
    if not target.exists():
        raise ProfileError(f"profile 文件不存在: {target}")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"profile 不是合法 JSON: {target} ({exc})")
    return EvalProfile.from_dict(raw, source_path=str(target))


def profile_to_config(profile: EvalProfile, **overrides: Any) -> dict[str, Any]:
    """profile → 主链配置字典（workflow / engine 消费）。"""
    return profile.to_workflow_config(**overrides)


def find_default_profile(cwd: Path | str | None = None) -> Path | None:
    """查找默认 profile（当前目录 eval-config.json）。"""
    base = Path(cwd or Path.cwd())
    candidate = base / PROFILE_FILENAME
    return candidate if candidate.exists() else None
