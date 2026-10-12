from __future__ import annotations

import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


TEST_MARKERS = ("test", "sandbox", "mock", "demo", "dev", "staging", "local", "localhost", "127.0.0.1")


def run_preflight_validators(resource_items: list[dict[str, Any]], cwd: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    critical_failures: list[str] = []

    for item in resource_items:
        check = validate_resource_item(item, cwd)
        checks.append(check)
        if check["validator_status"] == "warning":
            warnings.append(f"{check['label']}：{check['summary']}")
        if check.get("hard_failure"):
            critical_failures.append(f"{check['label']}：{check['summary']}")

    summary = {
        "passed": sum(1 for item in checks if item["validator_status"] == "passed"),
        "warnings": sum(1 for item in checks if item["validator_status"] == "warning"),
        "failed": sum(1 for item in checks if item["validator_status"] == "failed"),
        "skipped": sum(1 for item in checks if item["validator_status"] == "skipped"),
        "critical_failures": len(critical_failures),
    }
    return {
        "checks": checks,
        "warnings": warnings,
        "critical_failures": critical_failures,
        "summary": summary,
    }


def validate_resource_item(item: dict[str, Any], cwd: Path) -> dict[str, Any]:
    resource_type = str(item.get("type") or "unknown")
    validator = {
        "file": _validate_file,
        "environment": _validate_environment,
        "browser": _validate_browser,
        "database": _validate_database,
        "api_key": _validate_api_access,
        "credential": _validate_sensitive_value,
        "account": _validate_sensitive_value,
        "user_confirmation": _validate_contextual_value,
        "business_context": _validate_contextual_value,
    }.get(resource_type, _validate_generic)
    return validator(item, cwd)


def _base_result(item: dict[str, Any], status: str, summary: str, evidence: list[str] | None = None, hard_failure: bool = False) -> dict[str, Any]:
    return {
        "name": item.get("name", ""),
        "label": item.get("label") or item.get("name") or item.get("type") or "resource",
        "type": item.get("type", "unknown"),
        "status": item.get("status", "missing"),
        "blocking": bool(item.get("blocking")),
        "validator_status": status,
        "summary": summary,
        "evidence": evidence or [],
        "hard_failure": hard_failure,
    }


def _validate_file(item: dict[str, Any], cwd: Path) -> dict[str, Any]:
    status = str(item.get("status", "missing"))
    resolved_paths = [str(path) for path in item.get("resolved_paths") or []]
    if status == "generated":
        return _base_result(item, "warning", "当前使用的是 synthetic/mock 文件，适合功能验证但不适合高可信结论。", resolved_paths[:3])
    if status in {"missing", "defaulted"} and not resolved_paths:
        return _base_result(item, "skipped", "未提供真实文件，未执行文件级验证。")
    if not resolved_paths:
        return _base_result(item, "failed", "未解析到可验证的文件路径。", hard_failure=bool(item.get("blocking")))

    evidence: list[str] = []
    readable_count = 0
    for raw_path in resolved_paths:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        if not candidate.exists():
            evidence.append(f"{candidate} 不存在")
            continue
        if not candidate.is_file():
            evidence.append(f"{candidate} 不是文件")
            continue
        size = candidate.stat().st_size
        if size <= 0:
            evidence.append(f"{candidate.name} 为空文件")
            continue
        try:
            with candidate.open("rb") as handle:
                handle.read(64)
            readable_count += 1
            evidence.append(f"{candidate.name} 可读，{size} bytes")
        except OSError as exc:
            evidence.append(f"{candidate.name} 无法读取: {exc}")

    if readable_count == len(resolved_paths):
        return _base_result(item, "passed", f"已验证 {readable_count} 个输入文件可读。", evidence[:4])
    if readable_count > 0:
        return _base_result(item, "warning", f"部分文件可读（{readable_count}/{len(resolved_paths)}）。", evidence[:4])
    return _base_result(item, "failed", "提供的文件均不可读或为空。", evidence[:4], hard_failure=bool(item.get("blocking")))


def _validate_environment(item: dict[str, Any], cwd: Path) -> dict[str, Any]:
    value = str(item.get("value") or "").strip()
    status = str(item.get("status", "missing"))
    if status in {"missing", "defaulted"} and not value:
        return _base_result(item, "skipped", "未提供明确环境命令，跳过本地环境探测。")

    commands = _extract_command_candidates(value)
    if not commands:
        return _base_result(item, "warning", "未从环境描述里解析到可探测命令。")

    evidence: list[str] = []
    found = 0
    version_hits = 0
    for command in commands[:5]:
        command_path = _resolve_command_path(command, cwd)
        if not command_path:
            evidence.append(f"{command} 未在 PATH 中找到")
            continue
        found += 1
        version_output = _probe_command_version(command_path)
        if version_output:
            version_hits += 1
            evidence.append(f"{Path(command_path).name}: {version_output[:80]}")
        else:
            evidence.append(f"{Path(command_path).name} 存在，但未拿到版本信息")

    if found == 0:
        return _base_result(item, "failed", "环境依赖命令未找到。", evidence[:4], hard_failure=bool(item.get("blocking")))
    if version_hits == found:
        return _base_result(item, "passed", f"已验证 {found} 个环境命令存在且可响应版本探测。", evidence[:4])
    return _base_result(item, "warning", f"环境命令存在（{found} 个），但部分版本探测未通过。", evidence[:4])


def _validate_browser(item: dict[str, Any], cwd: Path) -> dict[str, Any]:
    value = str(item.get("value") or "").strip()
    status = str(item.get("status", "missing"))
    if status in {"missing", "defaulted"} and not value:
        return _base_result(item, "skipped", "未提供浏览器环境细节，跳过浏览器 validator。")

    text = value.lower()
    evidence: list[str] = []
    executable_found = False
    for candidate in ("google-chrome", "chrome", "chromium", "chromium-browser", "open"):
        resolved = _resolve_command_path(candidate, cwd)
        if resolved:
            executable_found = True
            evidence.append(f"检测到浏览器启动命令: {Path(resolved).name}")
            break

    port_match = re.search(r"remote-debugging-port[=\s:]+(\d{2,5})", text)
    if not port_match:
        url_match = re.search(r"https?://(?:127\.0\.0\.1|localhost):(\d{2,5})", text)
        port_match = url_match

    if port_match:
        port = int(port_match.group(1))
        if _probe_local_port("127.0.0.1", port):
            evidence.append(f"本地调试端口 {port} 可连接")
        else:
            evidence.append(f"本地调试端口 {port} 当前不可连接")

    if executable_found and port_match and any("当前不可连接" not in item and "可连接" in item for item in evidence):
        return _base_result(item, "passed", "浏览器命令和调试端口都已探测到。", evidence[:4])
    if executable_found or port_match:
        return _base_result(item, "warning", "已识别浏览器环境线索，但远程调试或登录态仍需人工确认。", evidence[:4])
    return _base_result(item, "warning", "浏览器环境描述存在，但未识别到可验证的启动命令或调试端口。", evidence[:4])


def _validate_database(item: dict[str, Any], cwd: Path) -> dict[str, Any]:
    value = str(item.get("value") or "").strip()
    status = str(item.get("status", "missing"))
    if status in {"missing", "defaulted"} and not value:
        return _base_result(item, "skipped", "未提供数据库连接信息，跳过数据库 validator。")

    evidence: list[str] = []
    scheme, host, port, db_name = _parse_connection_target(value)
    if not scheme or not host:
        return _base_result(item, "failed", "数据库连接信息格式不完整，至少需要 scheme 和 host。", hard_failure=bool(item.get("blocking")))

    evidence.append(f"scheme={scheme}")
    evidence.append(f"host={host}")
    if port:
        evidence.append(f"port={port}")
    if db_name:
        evidence.append(f"db={db_name}")

    if _contains_test_marker(value):
        evidence.append("检测到 test/sandbox/local 标识")
    else:
        return _base_result(item, "warning", "连接信息可解析，但未检测到测试环境标识，建议确认不是正式库。", evidence[:5])

    if host in {"127.0.0.1", "localhost"} and port:
        if _probe_local_port(host, port):
            evidence.append("本地数据库端口可连接")
        else:
            evidence.append("本地数据库端口当前不可连接")
            return _base_result(item, "warning", "数据库配置像是测试环境，但本地端口当前不可连接。", evidence[:5])

    return _base_result(item, "passed", "数据库连接信息结构完整，且带有测试环境标识。", evidence[:5])


def _validate_api_access(item: dict[str, Any], cwd: Path) -> dict[str, Any]:
    value = str(item.get("value") or "").strip()
    status = str(item.get("status", "missing"))
    if status in {"missing", "defaulted"} and not value:
        return _base_result(item, "skipped", "未提供 API key 或 endpoint，跳过 API validator。")

    url_match = re.search(r"https?://[^\s,]+", value)
    if not url_match:
        if len(value) >= 8:
            summary = "已提供 API 凭证样式值，但未附带 endpoint；建议补充 sandbox endpoint。"
            return _base_result(item, "warning", summary, ["只检测到疑似 token/key，未检测到 URL"])
        return _base_result(item, "failed", "未解析到有效的 API endpoint 或凭证。", hard_failure=bool(item.get("blocking")))

    parsed = urlparse(url_match.group(0))
    evidence = [f"scheme={parsed.scheme}", f"host={parsed.netloc}"]
    if _contains_test_marker(url_match.group(0)):
        evidence.append("检测到 sandbox/test 标识")
        return _base_result(item, "passed", "API endpoint 结构有效，且看起来是测试环境。", evidence[:4])
    return _base_result(item, "warning", "API endpoint 结构有效，但未检测到 sandbox/test 标识。", evidence[:4])


def _validate_sensitive_value(item: dict[str, Any], cwd: Path) -> dict[str, Any]:
    value = str(item.get("value") or "").strip()
    status = str(item.get("status", "missing"))
    if status in {"missing", "defaulted"} and not value:
        return _base_result(item, "skipped", "未提供敏感资源，跳过脱敏字段校验。")
    if len(value) < 4:
        return _base_result(item, "warning", "已提供资源，但内容过短，建议确认是否是真实测试凭证。")
    if _contains_test_marker(value):
        return _base_result(item, "passed", "已提供脱敏测试凭证或测试账号描述。")
    return _base_result(item, "warning", "已提供敏感资源，但未体现 test/sandbox 标识，建议确认用途。")


def _validate_contextual_value(item: dict[str, Any], cwd: Path) -> dict[str, Any]:
    value = str(item.get("value") or "").strip()
    status = str(item.get("status", "missing"))
    if status == "provided" and value:
        return _base_result(item, "passed", "已提供真实上下文输入。")
    if status == "defaulted":
        return _base_result(item, "warning", "将使用默认假设，结果可信度会下降。")
    return _base_result(item, "skipped", "未提供上下文输入，等待后续任务侧补充。")


def _validate_generic(item: dict[str, Any], cwd: Path) -> dict[str, Any]:
    status = str(item.get("status", "missing"))
    if status == "provided":
        return _base_result(item, "passed", "资源已提供，但暂无专用 validator。")
    if status == "generated":
        return _base_result(item, "warning", "资源由系统生成，暂无专用 validator。")
    if status == "defaulted":
        return _base_result(item, "warning", "资源将依赖默认假设，暂无专用 validator。")
    return _base_result(item, "skipped", "资源未提供，且暂无专用 validator。")


def _extract_command_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for chunk in re.split(r"[\n,;]+", text):
        token = chunk.strip().split(" ")[0].strip()
        if not token or token.startswith("-"):
            continue
        token = token.strip("'\"")
        if token not in candidates:
            candidates.append(token)
    return candidates


def _resolve_command_path(command: str, cwd: Path) -> str | None:
    candidate = Path(command)
    if candidate.is_absolute() and candidate.exists():
        return str(candidate)
    if command.startswith("./"):
        local_candidate = (cwd / command).resolve()
        if local_candidate.exists():
            return str(local_candidate)
    return shutil.which(command)


def _probe_command_version(command_path: str) -> str:
    for flag in ("--version", "-V", "-v"):
        try:
            proc = subprocess.run(
                [command_path, flag],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except Exception:
            continue
        output = (proc.stdout or proc.stderr or "").strip()
        if output:
            return output.splitlines()[0]
    return ""


def _parse_connection_target(value: str) -> tuple[str, str, int | None, str]:
    if "://" in value:
        parsed = urlparse(value)
        db_name = (parsed.path or "").strip("/")
        return parsed.scheme, parsed.hostname or "", parsed.port, db_name

    host_match = re.search(r"([A-Za-z0-9_.-]+):(\d{2,5})", value)
    db_match = re.search(r"(?:db|database|schema)[=/:\s]+([A-Za-z0-9_.-]+)", value, re.IGNORECASE)
    if host_match:
        host = host_match.group(1)
        port = int(host_match.group(2))
        db_name = db_match.group(1) if db_match else ""
        return "unknown", host, port, db_name
    return "", "", None, ""


def _probe_local_port(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _contains_test_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in TEST_MARKERS)
