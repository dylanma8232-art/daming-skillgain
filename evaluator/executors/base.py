"""基础执行器接口 - 所有执行器都继承这个类

P2 通用化: 在原有 execute() 抽象之上新增统一的会话执行契约 execute_session()。
主链 (engine) 只依赖 BaseExecutor.execute_session / capabilities，不再直接调用任何
宿主专用传输函数。每个宿主 executor 通过 capabilities 显式声明支持的能力，
不支持的能力由 require_capability 显式报错（禁止静默降级）。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class UnsupportedCapabilityError(RuntimeError):
    """宿主不支持所请求的能力时显式抛出（而非静默降级）。"""


@dataclass
class ExecutionResult:
    """执行结果标准化"""
    success: bool
    output: str
    error: str = ""
    exit_code: int = 0
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SessionResult:
    """宿主会话执行结果标准化（P2 统一契约）。

    与 engine 主链历史五元组 (exit_code, stdout, stderr, transcript, usage) 对齐:
    - exit_code: 进程/会话退出码
    - stdout: 原始标准输出
    - stderr: 原始标准错误
    - transcript: 可读的会话过程记录（无则回退 stdout）
    - usage: token 用量信息（宿主支持时）
    - text: 从输出中提取的模型文本回复（评委/总结路径使用；无则回退 stdout）
    """
    exit_code: int
    stdout: str
    stderr: str
    transcript: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    def combined_text(self) -> str:
        """评委解析使用的合并文本（text 优先，缺失时回退原始输出）。"""
        parts = [self.text or self.stdout]
        if self.transcript and self.transcript not in parts:
            parts.append(self.transcript)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(p for p in parts if p)


class BaseExecutor(ABC):
    """执行器基类

    类属性:
    - host_name: 宿主标识（写入 manifest / 报告宿主适用边界）
    - capabilities: 声明支持的能力。已知能力键:
        * file_ops: 宿主能否在工作目录中读写文件（产物型评测任务的必要条件）
        * skill_injection: skill 注入方式（如 "workspace-skills-dir" / "claude-skills-dir" /
          "agents-md" / "script-skills-dir"）；不支持则必须显式报错
        * transcript: 能否产出会话过程记录
    """

    host_name: str = "unknown"
    capabilities: dict[str, Any] = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}

    @abstractmethod
    def execute(self, model: str, prompt: str, **kwargs) -> ExecutionResult:
        """执行模型调用（旧接口，保留兼容）

        Args:
            model: 模型名称
            prompt: 提示词
            **kwargs: 其他参数

        Returns:
            ExecutionResult: 标准化执行结果
        """
        raise NotImplementedError

    def execute_session(
        self,
        *,
        model: str,
        prompt: str,
        cwd: Any = None,
        timeout_seconds: int = 600,
        execution_mode: str = "cli",
        format_json: bool = False,
        dangerous_skip_permissions: bool = False,
        files: list[str] | None = None,
        max_tokens: int = 4096,
    ) -> SessionResult:
        """统一会话执行契约（P2 主链唯一调用入口）

        主链（实验执行、盲评评委、总结模型）一律通过该方法调用宿主，
        不得绕过本契约直接调用宿主专用函数。

        Args:
            model: 模型名称
            prompt: 任务提示词
            cwd: 工作目录（agent 宿主在其中执行任务）
            timeout_seconds: 超时秒数
            execution_mode: 宿主域内执行模式（如 opencode 的 cli/cli-attach/api）
            format_json: 是否请求结构化输出
            dangerous_skip_permissions: 自动化环境下跳过交互确认
            files: 附加文件路径（支持宿主）
            max_tokens: API 型宿主的最大生成 token 数

        Returns:
            SessionResult: 标准化会话结果

        Raises:
            UnsupportedCapabilityError: 宿主不支持会话执行
            subprocess.TimeoutExpired: 执行超时（与旧主链行为一致，由上层归一化）
        """
        raise UnsupportedCapabilityError(
            f"宿主 '{self.host_name}' 不支持会话执行 (execute_session)"
        )

    def supports(self, capability: str) -> bool:
        """查询是否声明支持某能力"""
        value = self.capabilities.get(capability)
        return bool(value)

    def require_capability(self, capability: str, purpose: str = "") -> None:
        """显式能力预检：不支持即报错，绝不静默降级。

        Raises:
            UnsupportedCapabilityError: 附带宿主名、能力名与用途说明
        """
        if not self.supports(capability):
            purpose_text = f"（用途: {purpose}）" if purpose else ""
            raise UnsupportedCapabilityError(
                f"宿主 '{self.host_name}' 不支持能力 '{capability}'{purpose_text}。"
                f"该宿主声明的能力: {sorted(self.capabilities.keys())}。"
                f"请更换宿主或在配置中显式选择支持该能力的执行器。"
            )

    @abstractmethod
    def check_connectivity(self, model: str) -> bool:
        """检查模型连通性

        Args:
            model: 模型名称

        Returns:
            bool: 是否可连通
        """
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[str]:
        """列出可用模型

        Returns:
            list[str]: 可用模型列表
        """
        raise NotImplementedError

    def validate_config(self) -> tuple[bool, str]:
        """验证配置是否有效

        Returns:
            tuple[bool, str]: (是否有效, 错误信息)
        """
        return True, ""
