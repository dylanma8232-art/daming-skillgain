"""基础执行器接口 - 所有执行器都继承这个类

P2 通用化: 在原有 execute() 抽象之上新增统一的会话执行契约 execute_session()。
主链 (engine) 只依赖 BaseExecutor.execute_session / capabilities，不再直接调用任何
宿主专用传输函数。每个宿主 executor 通过 capabilities 显式声明支持的能力，
不支持的能力由 require_capability 显式报错（禁止静默降级）。
"""
from __future__ import annotations
import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---- HostProtocol 统一错误语义（见 references/host-protocol.md §5） -------------
STDERR_TRUNCATE = 500  # 所有宿主 stderr 统一截断长度
PROBE_TIMEOUT_SECONDS = 10  # --version 探活超时
ERROR_RETRYABLE = "retryable"  # 网络抖动 / 超时 / 临时不可用 → 可重试
ERROR_FATAL = "fatal"  # CLI 缺失 / 配置错误 / 能力不支持 → 立即失败


class UnsupportedCapabilityError(RuntimeError):
    """宿主不支持所请求的能力时显式抛出（而非静默降级）。"""


@dataclass
class ProbeResult:
    """宿主探活结果（HostProtocol 三件套之 ③）。"""
    ok: bool
    message: str = ""
    version: str = ""
    command: tuple[str, ...] = ()


@dataclass
class HostInvocation:
    """宿主非交互调用形态（HostProtocol 三件套之 ①）。

    argv: 实际命令行
    stdin: 通过标准输入投递的 prompt（None 表示 prompt 走 argv）
    prompt_channel: "stdin" | "argv"，显式声明以规避 ARG_MAX（见协议 §2.1）
    """
    argv: list[str]
    stdin: str | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    prompt_channel: str = "stdin"


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
    default_cli_path: str = ""
    version_args: tuple[str, ...] = ("--version",)
    # 最近一次会话的宿主会话 id（支持 resume 的宿主会填充）
    last_session_id: str = ""

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}

    # ---- HostProtocol 三件套契约 -------------------------------------------
    def cli_command(self) -> str:
        """宿主 CLI 可执行文件路径（可由 config.cli_path 覆盖）。"""
        return str(self.config.get("cli_path") or self.default_cli_path)

    def probe(self, timeout: int = PROBE_TIMEOUT_SECONDS) -> ProbeResult:
        """③ 探活：零成本 `--version`，rc=0 判可用（协议 §2.3 第一级）。"""
        command = (self.cli_command(),) + tuple(self.version_args)
        try:
            proc = subprocess.run(
                list(command), capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError:
            return ProbeResult(
                ok=False,
                message=f"找不到 {self.host_name} CLI: {self.cli_command()}",
                command=command,
            )
        except subprocess.TimeoutExpired:
            return ProbeResult(
                ok=False,
                message=f"{self.host_name} CLI 探活超时（{timeout}s）",
                command=command,
            )
        version = (proc.stdout or proc.stderr).strip().splitlines()
        version_text = version[0].strip() if version else ""
        if proc.returncode != 0:
            return ProbeResult(
                ok=False,
                message=truncate_stderr(proc.stderr or proc.stdout),
                version=version_text,
                command=command,
            )
        return ProbeResult(ok=True, version=version_text, command=command)

    def build_invocation(
        self,
        *,
        model: str,
        prompt: str,
        cwd: Any = None,
        format_json: bool = False,
        dangerous_skip_permissions: bool = False,
        files: list[str] | None = None,
    ) -> HostInvocation:
        """① 非交互调用形态：子类实现，不得在此静默降级。"""
        raise UnsupportedCapabilityError(
            f"宿主 '{self.host_name}' 未实现 build_invocation（HostProtocol 三件套之一）"
        )

    def parse_output(
        self,
        *,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> SessionResult:
        """② 输出解析器：兜底为 stdout 原样，usage 显式声明「未采集」。"""
        return SessionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            transcript=stdout,
            usage={},
            text=stdout,
        )

    def run_cli_session(
        self,
        *,
        model: str,
        prompt: str,
        cwd: Any = None,
        timeout_seconds: int = 600,
        format_json: bool = False,
        dangerous_skip_permissions: bool = False,
        files: list[str] | None = None,
    ) -> SessionResult:
        """CLI 型宿主的统一执行流水线：build_invocation → 子进程 → parse_output。"""
        invocation = self.build_invocation(
            model=model,
            prompt=prompt,
            cwd=cwd,
            format_json=format_json,
            dangerous_skip_permissions=dangerous_skip_permissions,
            files=files,
        )
        env = os.environ.copy()
        env.update(invocation.env)
        proc = subprocess.run(
            invocation.argv,
            input=invocation.stdin,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=invocation.cwd or (str(cwd) if cwd else None),
            env=env,
        )
        return self.parse_output(
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            exit_code=proc.returncode,
        )

    @classmethod
    def classify_error(cls, exit_code: int, stderr: str, timed_out: bool = False) -> str:
        """统一错误语义：timed_out / 网络类 → retryable，其余非零 → fatal（协议 §5）。"""
        if timed_out:
            return ERROR_RETRYABLE
        if exit_code == 124:
            return ERROR_RETRYABLE
        text = (stderr or "").lower()
        retryable_markers = (
            "timeout", "timed out", "rate limit", "429", "502", "503", "504",
            "connection reset", "connection refused", "network", "temporarily unavailable",
            "econnreset", "etimedout",
        )
        if any(marker in text for marker in retryable_markers):
            return ERROR_RETRYABLE
        return ERROR_FATAL

    # ---- skill 注入边界（协议 §4）-----------------------------------------
    def inject_skill(self, skill_path: Any, workspace_dir: Any, skill_name: str) -> dict[str, Any]:
        """按宿主约定注入被测 skill。

        明确边界：注入/清理的实现唯一归属 evaluator/subject_adapter；executor 只做
        委派，保证「声明（capabilities.skill_injection）」与「实现」只有一个来源。
        """
        from ..subject_adapter import get_subject_adapter

        self.require_capability("skill_injection", purpose="被测 skill 注入")
        return get_subject_adapter(self.host_name).inject_skill(
            skill_path, Path(workspace_dir), skill_name
        )

    def cleanup_skill(self, workspace_dir: Any, skill_name: str) -> dict[str, Any]:
        """无需 skill 的对照模式：清理注入痕迹，防假对照（委派 Subject Adapter）。"""
        from ..subject_adapter import get_subject_adapter

        return get_subject_adapter(self.host_name).cleanup_skill(
            Path(workspace_dir), skill_name
        )

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
        """验证配置是否有效（默认走 --version 探活，未安装明确报错）。

        Returns:
            tuple[bool, str]: (是否有效, 错误信息)
        """
        if not self.cli_command():
            return True, ""
        probe = self.probe()
        if probe.ok:
            return True, ""
        return False, f"{self.host_name} 宿主不可用: {probe.message}"


def truncate_stderr(text: str, limit: int = STDERR_TRUNCATE) -> str:
    """统一 stderr 截断（协议 §5：一律 500 字符）。"""
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "…"


def augment_prompt_with_files(prompt: str, files: list[str] | None) -> str:
    """无原生附件参数的宿主：把 files 显式写进 prompt（协议 §2.1）。"""
    if not files:
        return prompt
    lines = ["\n\n可用输入文件（相对/绝对路径，位于工作区内）:"]
    lines.extend(f"- {path}" for path in files)
    return prompt + "\n".join(lines)
