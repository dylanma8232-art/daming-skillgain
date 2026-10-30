"""OpenCode 执行器 - OpenCode 宿主专用传输层（P2 自 engine.py 迁入）

本模块是 OpenCode 专属逻辑的唯一归属地：HTTP session 客户端、CLI 子进程、
attach 探测、JSON 事件流解析、OpenCode 模型发现全部收敛于此。
主链 (engine) 通过 BaseExecutor.execute_session / list_models 调用本执行器，
不得 import 本模块的宿主专用函数。
"""
from __future__ import annotations
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .base import BaseExecutor, ExecutionResult, SessionResult


def _get_opencode_auth_header() -> dict[str, str]:
    """从环境变量读取 OpenCode server 的 basic auth 配置。"""
    username = os.environ.get("OPENCODE_SERVER_USERNAME")
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if username and password:
        import base64
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {credentials}"}
    return {}


class OpencodeApiClient:
    """极小 OpenCode HTTP session 客户端，用于桌面/本地 server 模式。"""

    def __init__(self, base_url: str = "http://127.0.0.1:3000", timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auth_headers = _get_opencode_auth_header()

    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {"Content-Type": "application/json"}
        headers.update(self.auth_headers)
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenCode API {method} {path} failed: {e.code} {error_body}") from e

    def create_session(self, model: str, directory: str) -> str:
        resp = self._request("POST", "/api/sessions", {"model": model, "directory": directory})
        session_id = resp.get("id") or resp.get("session_id")
        if not session_id:
            raise RuntimeError(f"OpenCode API 未返回 session id: {resp}")
        return str(session_id)

    def send_message(self, session_id: str, message: str, files: list[str] | None = None) -> str:
        payload: dict[str, Any] = {"message": message}
        if files:
            payload["files"] = files
        resp = self._request("POST", f"/api/sessions/{session_id}/messages", payload)
        return str(resp.get("id") or resp.get("message_id") or "")

    def wait_for_completion(self, session_id: str, message_id: str, poll_interval: float = 2.0) -> dict:
        start = time.time()
        status_paths = []
        if message_id:
            status_paths.append(f"/api/sessions/{session_id}/messages/{message_id}")
        status_paths.append(f"/api/sessions/{session_id}")
        while time.time() - start < self.timeout:
            last_resp: dict[str, Any] = {}
            for path in status_paths:
                try:
                    last_resp = self._request("GET", path)
                    status = str(last_resp.get("status") or "").lower()
                    if status in {"completed", "finished", "success", "error", "failed"}:
                        return last_resp
                except Exception:
                    continue
            time.sleep(poll_interval)
        raise TimeoutError(f"Session {session_id} did not complete within {self.timeout}s")

    def get_transcript(self, session_id: str) -> str:
        try:
            resp = self._request("GET", f"/api/sessions/{session_id}")
            return json.dumps(resp.get("messages", resp.get("transcript", resp)), ensure_ascii=False, indent=2)
        except Exception:
            return ""


def detect_opencode_api_url() -> str:
    env_url = os.environ.get("OPENCODE_API_URL") or os.environ.get("OPENCODE_SERVER_URL")
    if env_url:
        return env_url
    auth_headers = _get_opencode_auth_header()
    for port in [3000, 3001, 8080, 8081, 60297, 56800, 56656]:
        url = f"http://127.0.0.1:{port}"
        try:
            headers = {"Content-Type": "application/json"}
            headers.update(auth_headers)
            req = urllib.request.Request(f"{url}/api/health", headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=2):
                return url
        except Exception:
            continue
    return "http://127.0.0.1:3000"


def detect_opencode_server_port() -> str | None:
    """自动检测当前运行的 opencode server 端口"""
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "opencode" in line and "serve" in line and "--port" in line:
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == "--port" and i + 1 < len(parts):
                        return parts[i + 1]
    except Exception:
        pass
    return None


def _resolve_opencode_cli_path() -> str:
    """定位 opencode CLI：OPENCODE_CLI_PATH 环境变量优先，其次 PATH，
    最后尝试 OpenCode 桌面版自带 CLI（macOS 常见安装位置）。"""
    import shutil
    env_path = os.environ.get("OPENCODE_CLI_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    if shutil.which("opencode"):
        return "opencode"
    desktop_cli = (
        Path.home() / "Library" / "Application Support" / "ai.opencode.desktop" / "cli"
    )
    if desktop_cli.exists():
        # 取版本号最大的目录
        candidates = sorted(
            (d for d in desktop_cli.iterdir() if d.is_dir()),
            key=lambda d: [int(x) if x.isdigit() else 0 for x in d.name.split(".")],
            reverse=True,
        )
        for d in candidates:
            for name in ("opencode-cli", "opencode"):
                binary = d / name
                if binary.exists() and os.access(str(binary), os.X_OK):
                    return str(binary)
    return "opencode"


def _detect_opencode_attach_url() -> str | None:
    env_url = os.environ.get("OPENCODE_ATTACH_URL") or os.environ.get("OPENCODE_API_URL") or os.environ.get("OPENCODE_SERVER_URL")
    if env_url:
        return env_url.rstrip("/")
    auth_headers = _get_opencode_auth_header()
    for port in [3000, 3001, 8080, 8081, 60297, 56800, 56656]:
        url = f"http://127.0.0.1:{port}"
        try:
            headers = {"Content-Type": "application/json"}
            headers.update(auth_headers)
            req = urllib.request.Request(f"{url}/api/health", headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=2):
                return url
        except Exception:
            continue
    port = detect_opencode_server_port()
    if port:
        return f"http://127.0.0.1:{port}"
    return None


def run_opencode_api_session(
    *,
    model: str,
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
    files: list[str] | None = None,
) -> tuple[int, str, str, str, dict[str, Any]]:
    """通过 OpenCode HTTP session API 执行（宿主专用，仅供本执行器内部使用）"""
    api_url = detect_opencode_api_url()
    client = OpencodeApiClient(base_url=api_url, timeout=timeout_seconds)
    session_id = client.create_session(model, str(cwd))
    message_id = client.send_message(session_id, prompt, files=files)
    result = client.wait_for_completion(session_id, message_id, poll_interval=3.0)
    transcript = client.get_transcript(session_id)
    status = str(result.get("status") or "completed").lower()
    ok = status in {"completed", "finished", "success"}
    stdout_text = f"API execution completed: {status}\nSession: {session_id}\nOpenCode API: {api_url}\n"
    usage = result.get("usage") if isinstance(result, dict) else None
    if usage:
        stdout_text += f"Token usage: {usage}\n"
    return (0 if ok else 1), stdout_text, "", transcript, (usage or {})


def run_opencode_cli(
    *,
    model: str,
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
    format_json: bool = True,
    dangerous_skip_permissions: bool = False,
    attach: bool = False,
    cli_path: str | None = None,
) -> tuple[int, str, str]:
    """通过 opencode CLI 子进程执行（宿主专用，仅供本执行器内部使用）"""
    env = os.environ.copy()
    env.pop("OPENCODE_SERVER_PASSWORD", None)
    env.pop("OPENCODE_SERVER_USERNAME", None)
    env.pop("OPENCODE_CLIENT", None)
    # opencode 2.x 服务模式以 PWD 环境变量定位工作目录（subprocess cwd 参数
    # 只改变进程 cwd，不会同步 PWD）；显式同步，保证任务投递到正确的工作区。
    env["PWD"] = str(cwd)
    cli = cli_path or _resolve_opencode_cli_path()

    def build_cmd(use_attach: bool) -> list[str]:
        cmd = [cli, "run"]
        if format_json:
            cmd.extend(["--format", "json"])
        cmd.extend(["--model", model])
        if dangerous_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if use_attach:
            attach_url = _detect_opencode_attach_url()
            if attach_url:
                cmd.extend(["--attach", attach_url, "--dir", str(cwd)])
        cmd.extend(["--", prompt])
        return cmd

    cmd = build_cmd(attach)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, env=env, cwd=str(cwd))
    if attach and proc.returncode != 0:
        fallback = subprocess.run(build_cmd(False), capture_output=True, text=True, timeout=timeout_seconds, env=env, cwd=str(cwd))
        if fallback.returncode == 0:
            fallback.stdout = (
                "[cli-attach fallback] attach failed; retried with local opencode run.\n"
                + fallback.stdout
            )
            return fallback.returncode, fallback.stdout, fallback.stderr
    return proc.returncode, proc.stdout, proc.stderr


def parse_opencode_json_events(raw_stdout: str) -> tuple[str, list[dict], dict[str, Any]]:
    """解析 opencode JSON 事件流。

    Returns:
        (text_output, events, usage_info)
    """
    text_parts = []
    events = []
    usage_info: dict[str, Any] = {}

    prompt_tokens_total = 0
    completion_tokens_total = 0
    total_tokens_total = 0
    step_count = 0

    for line in raw_stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)
            if event.get("type") == "text":
                part = event.get("part", {})
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            elif event.get("type") == "error":
                err = event.get("error", {})
                text_parts.append(f"[ERROR] {err.get('message', str(err))}")
            elif event.get("type") == "session":
                session_data = event.get("session", {})
                usage = session_data.get("usage", {})
                if usage:
                    usage_info = {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                        "api_call_count": 1,
                    }
            elif event.get("type") == "step_finish":
                tokens = event.get("part", {}).get("tokens", {})
                if tokens:
                    input_t = tokens.get("input", 0) or 0
                    output_t = tokens.get("output", 0) or 0
                    total_t = tokens.get("total", 0) or 0
                    prompt_tokens_total += input_t
                    completion_tokens_total += output_t
                    total_tokens_total += total_t
                    step_count += 1
        except json.JSONDecodeError:
            text_parts.append(line)

    if not usage_info and step_count > 0:
        usage_info = {
            "prompt_tokens": prompt_tokens_total,
            "completion_tokens": completion_tokens_total,
            "total_tokens": total_tokens_total,
            "api_call_count": step_count,
        }

    return "".join(text_parts), events, usage_info


class OpencodeExecutor(BaseExecutor):
    """OpenCode 宿主执行器

    capabilities:
    - file_ops: agent 宿主可在工作目录内读写文件
    - skill_injection: workspace-skills-dir（.opencode/skills/ 约定）
    - transcript: CLI JSON 事件流 / API transcript
    """

    host_name = "opencode"
    default_cli_path = "opencode"
    capabilities = {
        "file_ops": True,
        "skill_injection": "workspace-skills-dir",
        "transcript": True,
    }

    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        self.cli_path = self.config.get("cli_path", "opencode")
        self.server_url = self.config.get("server_url", "")
        self.execution_mode = self.config.get("execution_mode", "cli-attach")
        self.timeout = self.config.get("timeout", 1800)

    def execute(self, model: str, prompt: str, **kwargs) -> ExecutionResult:
        """通过 opencode CLI 执行"""
        cwd = kwargs.get("cwd", os.getcwd())
        timeout = kwargs.get("timeout", self.timeout)

        cmd = [self.cli_path, "run", "--format", "json", "--model", model, "--", prompt]

        if self.execution_mode == "cli-attach" and self.server_url:
            cmd.extend(["--attach", self.server_url])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=self._get_env()
            )

            return ExecutionResult(
                success=proc.returncode == 0,
                output=proc.stdout,
                error=proc.stderr,
                exit_code=proc.returncode
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                output="",
                error=f"执行超时（{timeout}秒）",
                exit_code=-1
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                exit_code=-1
            )

    def execute_session(
        self,
        *,
        model: str,
        prompt: str,
        cwd: Any = None,
        timeout_seconds: int = 600,
        execution_mode: str = "cli-attach",
        format_json: bool = True,
        dangerous_skip_permissions: bool = False,
        files: list[str] | None = None,
        max_tokens: int = 4096,
    ) -> SessionResult:
        """统一会话契约：按 execution_mode 分发到 OpenCode API session 或 CLI。"""
        cwd = Path(cwd) if cwd else Path.cwd()
        if execution_mode == "api":
            exit_code, stdout_text, stderr_text, transcript_text, usage_info = run_opencode_api_session(
                model=model,
                prompt=prompt,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                files=files,
            )
            text, _, _ = parse_opencode_json_events(transcript_text) if transcript_text else ("", [], {})
            return SessionResult(
                exit_code=exit_code, stdout=stdout_text, stderr=stderr_text,
                transcript=transcript_text, usage=usage_info or {},
                text=text or stdout_text,
            )
        exit_code, stdout_text, stderr_text = run_opencode_cli(
            model=model,
            prompt=prompt,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            format_json=format_json,
            dangerous_skip_permissions=dangerous_skip_permissions,
            attach=execution_mode == "cli-attach",
        )
        transcript_text, _, usage_info = parse_opencode_json_events(stdout_text)
        return SessionResult(
            exit_code=exit_code, stdout=stdout_text, stderr=stderr_text,
            transcript=transcript_text, usage=usage_info or {},
            text=transcript_text or stdout_text,
        )

    def check_connectivity(self, model: str) -> bool:
        """检查模型连通性"""
        result = self.execute(model, "回答一个字：好", timeout=30)
        return result.success and result.output.strip()

    def list_models(self) -> list[str]:
        """列出可用模型（opencode 域内发现逻辑）"""
        return get_opencode_models()

    def validate_config(self) -> tuple[bool, str]:
        """验证配置"""
        # 检查 CLI 是否存在
        try:
            proc = subprocess.run(
                [self.cli_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if proc.returncode != 0:
                return False, f"opencode CLI 无法执行: {proc.stderr}"
        except FileNotFoundError:
            return False, f"找不到 opencode CLI: {self.cli_path}"

        return True, ""

    def _get_env(self) -> dict[str, str]:
        """获取环境变量"""
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        # 添加认证信息
        username = self.config.get("username") or os.environ.get("OPENCODE_SERVER_USERNAME")
        password = self.config.get("password") or os.environ.get("OPENCODE_SERVER_PASSWORD")

        if username:
            env["OPENCODE_SERVER_USERNAME"] = username
        if password:
            env["OPENCODE_SERVER_PASSWORD"] = password

        return env


def get_opencode_models() -> list[str]:
    """opencode 域内模型发现（GUI 配置 -> CLI -> 配置文件）"""
    import subprocess
    import json as _json
    import os
    cli = _resolve_opencode_cli_path()
    gui_config_paths = [
        Path.home() / "Library" / "Application Support" / "ai.opencode.desktop" / "opencode.global.dat",
    ]
    for gui_config in gui_config_paths:
        if gui_config.exists():
            try:
                content = _json.loads(gui_config.read_text(encoding="utf-8"))
                model_str = content.get("model", "{}")
                model_config = _json.loads(model_str) if isinstance(model_str, str) else model_str
                user_models = model_config.get("user", [])
                enabled = []
                for m in user_models:
                    visibility = m.get("visibility", "show")
                    model_id = m.get("modelID", "")
                    provider = m.get("providerID", "")
                    if visibility != "hide" and model_id and provider:
                        enabled.append(f"{provider}/{model_id}")
                if enabled:
                    return enabled
            except Exception:
                continue

    tried = []
    for cmd in [[cli, "models"]]:
        tried.append(" ".join(cmd))
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                models = []
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("-"):
                        if "/" in line:
                            models.append(line.split()[0])
                        elif line.replace("-", "").replace("_", "").replace(".", "").isalnum():
                            models.append(line.split()[0])
                if models:
                    return models
        except Exception:
            continue

    config_paths = [
        Path.home() / ".opencode" / "config.json",
        Path.home() / ".config" / "opencode" / "config.json",
        Path.home() / ".config" / "opencode" / "opencode.json",
    ]
    for config_path in config_paths:
        if config_path.exists():
            try:
                content = _json.loads(config_path.read_text())
                models = content.get("models", [])
                if isinstance(models, list) and models:
                    return models
                providers = content.get("providers", {})
                if isinstance(providers, dict):
                    found = []
                    for pmodels in providers.values():
                        if isinstance(pmodels, list):
                            found.extend(pmodels)
                    if found:
                        return found
            except Exception:
                continue

    return []
