"""版本自检（update checker）——每次被任何 AI / CLI 调用时，检查 GitHub 上是否有最新版本。

设计约束（P4 硬性要求）：
- 绝不阻塞主流程：网络失败 / 超时（默认 3s）一律静默跳过，不抛异常、不打扰用户。
- 每 24h 最多真正请求一次：检查结果缓存在 ~/.cache/daming-skillgain/last_check。
- 有新版时只打一行白话提示，不输出多余日志。
- owner/repo 未定（用户建仓前）时默认 'dylanma8232-art/daming-skillgain'（已建仓），
  可通过环境变量 DAMING_SKILLGAIN_REPO 或配置项覆盖；建仓后改这一处即可。

公开口径：对外版本统一为 PUBLIC_VERSION = "1.0"（README 徽章同口径），
内部 __version__ 与之保持一致。
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

# ---- 可配置项 -----------------------------------------------------------------

# GitHub 仓库（owner/repo）。默认占位：用户建仓后通过环境变量或直接改这里。
UPDATE_REPO = "dylanma8232-art/daming-skillgain"
ENV_UPDATE_REPO = "DAMING_SKILLGAIN_REPO"

# 完全关闭更新检查的开关（测试 / 离线环境用）。
ENV_SKIP_UPDATE_CHECK = "DAMING_SKILLGAIN_SKIP_UPDATE_CHECK"

# 网络超时（秒）。超过即静默放弃，绝不阻塞主流程。
DEFAULT_TIMEOUT_SECONDS = 3.0

# 检查间隔：每 24h 最多请求一次。
CHECK_INTERVAL_SECONDS = 24 * 3600

# 对外宣传版本（README 徽章 / 用户提示口径）。
PUBLIC_VERSION = "1.0"

# 缓存位置。
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "daming-skillgain"
DEFAULT_CACHE_FILE = DEFAULT_CACHE_DIR / "last_check"


# ---- 版本比较 -----------------------------------------------------------------

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*)")


def _parse_version(text: str) -> tuple[int, ...] | None:
    """从 'v1.2.3' / '1.2.3' / 'daming-skillgain v1.2' 等提取版本号元组。"""
    if not text:
        return None
    m = _VERSION_RE.search(text.strip().lstrip("vV"))
    if not m:
        return None
    try:
        return tuple(int(part) for part in m.group(1).split("."))
    except ValueError:
        return None


def is_newer_version(latest: str, current: str) -> bool:
    """latest 是否比 current 更新（缺省 / 无法解析一律视为不更新，宁可不提示）。"""
    a = _parse_version(latest)
    b = _parse_version(current)
    if a is None or b is None:
        return False
    return a > b


# ---- 缓存 ---------------------------------------------------------------------


def _cache_file() -> Path:
    override = os.environ.get("DAMING_SKILLGAIN_UPDATE_CACHE")
    return Path(override) if override else DEFAULT_CACHE_FILE


def _read_cache() -> dict:
    try:
        return json.loads(_cache_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(payload: dict) -> None:
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # 缓存写失败不影响主流程


def _cache_is_fresh(cache: dict, now: float) -> bool:
    last = cache.get("last_check")
    return isinstance(last, (int, float)) and (now - last) < CHECK_INTERVAL_SECONDS


# ---- GitHub API ---------------------------------------------------------------


def _repo_slug() -> str:
    return os.environ.get(ENV_UPDATE_REPO, UPDATE_REPO).strip("/")


def fetch_latest_release(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict | None:
    """调 GitHub releases/latest。任何失败（网络 / 超时 / 404 / 解析）返回 None，绝不抛异常。"""
    slug = _repo_slug()
    if not slug or slug.count("/") != 1 or _is_placeholder_repo():
        # 占位仓库未建 / 配置非法时直接静默跳过，避免无谓请求。
        return None
    url = f"https://api.github.com/repos/{slug}/releases/latest"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "daming-skillgain-update-checker",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    tag = data.get("tag_name") or data.get("name") or ""
    html_url = data.get("html_url") or f"https://github.com/{slug}/releases"
    if not tag:
        return None
    return {"version": tag, "url": html_url}


def _is_placeholder_repo() -> bool:
    return _repo_slug() == "dylanma8232-art/daming-skillgain"


# ---- 检查与提示 ----------------------------------------------------------------


def check_for_update(
    current_version: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    now: float | None = None,
    force: bool = False,
) -> dict | None:
    """检查是否有新版本。返回 {'version', 'url'} 或 None；所有异常静默。

    - 每 24h 内已有缓存则不发请求（force=True 强制检查，供 update-check 子命令用）。
    - 网络失败 / 超时静默返回 None。
    """
    if os.environ.get(ENV_SKIP_UPDATE_CHECK):
        return None
    import time

    now = time.time() if now is None else now
    cache = _read_cache()
    if not force and _cache_is_fresh(cache, now):
        # 缓存期内：若上次已发现新版本，直接复用缓存提示，不发请求。
        cached_new = cache.get("new_version")
        if cached_new and is_newer_version(str(cached_new), current_version):
            return {"version": str(cached_new), "url": cache.get("url", "")}
        return None

    latest = fetch_latest_release(timeout=timeout)
    payload = {"last_check": now}
    result: dict | None = None
    if latest and is_newer_version(latest["version"], current_version):
        payload["new_version"] = latest["version"]
        payload["url"] = latest["url"]
        result = latest
    _write_cache(payload)
    return result


def format_update_notice(info: dict, current_display: str = PUBLIC_VERSION) -> str:
    return (
        f"📌 有新版本 {info.get('version', '')} 可用（当前 {current_display}），"
        f"运行 git pull 更新；查看更新内容：{info.get('url', '')}"
    )


def maybe_check_update(current_version: str | None = None, force: bool = False) -> str | None:
    """同步版检查入口：返回提示行文本（无更新 / 失败时为 None）。供 update-check 子命令与测试用。

    默认以 PUBLIC_VERSION（1.0，对外发布口径）参与比对：GitHub release tag
    跟随版本号（v1.0, v1.1, ...），内部 __version__ 与之保持一致。
    """
    if current_version is None:
        current_version = PUBLIC_VERSION
    info = check_for_update(current_version, force=force)
    if info:
        return format_update_notice(info)
    return None


def maybe_check_update_async(current_version: str | None = None) -> None:
    """后台线程版检查入口（CLI main 启动时用）：绝不阻塞、绝不抛异常。

    检查在 daemon 线程中进行；有新版时打一行白话提示到 stderr（避免混入 JSON 输出）。
    """
    if os.environ.get(ENV_SKIP_UPDATE_CHECK):
        return

    def _worker() -> None:
        try:
            notice = maybe_check_update(current_version)
            if notice:
                print(notice, file=sys.stderr)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True, name="daming-update-check").start()
