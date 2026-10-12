"""版本自检（update_checker）测试——三场景 + 不阻塞主流程（超时）验证。

覆盖：
1. 有新版：GitHub 返回更高 tag → 返回提示信息，含版本与 URL
2. 无新版：返回更低/相同 tag → 返回 None
3. 网络失败（urllib 抛异常 / 超时）→ 静默返回 None，绝不抛异常
4. 缓存：24h 内第二次检查不发请求
5. 占位仓库默认静默跳过
6. CLI 启动（maybe_check_update_async）不阻塞主流程
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

from evaluator import update_checker as uc
from evaluator.update_checker import (
    check_for_update,
    fetch_latest_release,
    format_update_notice,
    is_newer_version,
    maybe_check_update,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """每个用例独立缓存文件 + 确保不受真实环境变量影响。"""
    monkeypatch.setenv("DAMING_SKILLGAIN_UPDATE_CACHE", str(tmp_path / "last_check"))
    monkeypatch.delenv("DAMING_SKILLGAIN_REPO", raising=False)
    monkeypatch.delenv("DAMING_SKILLGAIN_SKIP_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(uc, "UPDATE_REPO", "owner/example")


def _mock_urlopen(monkeypatch, payload: dict | None, exc: Exception | None = None):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload or {}).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        if exc is not None:
            raise exc
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _payload(tag: str) -> dict:
    return {"tag_name": tag, "html_url": f"https://github.com/owner/example/releases/tag/{tag}"}


# ---- 版本比较 ----------------------------------------------------------------


def test_is_newer_version_basic():
    assert is_newer_version("1.1.0", "1.0")
    assert is_newer_version("v1.0.1", "1.0.0")
    assert not is_newer_version("1.0", "1.0.0")
    assert not is_newer_version("0.9", "1.0")
    assert not is_newer_version("", "1.0")
    assert not is_newer_version("not-a-version", "1.0")


# ---- 场景 1：有新版 -----------------------------------------------------------


def test_new_version_available(monkeypatch):
    _mock_urlopen(monkeypatch, _payload("1.1.0"))
    info = check_for_update("1.0", now=1000.0)
    assert info is not None
    assert info["version"] == "1.1.0"
    assert "releases/tag/1.1.0" in info["url"]


def test_notice_text(monkeypatch):
    notice = format_update_notice({"version": "1.1.0", "url": "https://example.com/r"})
    assert "1.1.0" in notice
    assert "git pull" in notice
    assert "https://example.com/r" in notice


# ---- 场景 2：无新版 -----------------------------------------------------------


def test_no_new_version(monkeypatch):
    _mock_urlopen(monkeypatch, _payload("0.9.0"))
    assert check_for_update("1.0", now=1000.0) is None
    _mock_urlopen(monkeypatch, _payload("7.0.1"))
    assert check_for_update("1.0", now=1000.0) is None


# ---- 场景 3：网络失败静默 -------------------------------------------------------


def test_network_error_silent(monkeypatch):
    _mock_urlopen(monkeypatch, None, exc=urllib.error.URLError("no network"))
    assert check_for_update("1.0", now=1000.0) is None  # 不抛异常
    assert maybe_check_update("1.0") is None


def test_timeout_silent(monkeypatch):
    def slow_urlopen(req, timeout=None):
        time.sleep(min(timeout or 0, 0.2))
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", slow_urlopen)
    start = time.monotonic()
    assert check_for_update("1.0", timeout=0.1, now=1000.0) is None
    assert time.monotonic() - start < 1.0  # 超时受 timeout 参数约束，不悬挂


def test_http_error_silent(monkeypatch):
    _mock_urlopen(monkeypatch, None, exc=urllib.error.HTTPError("", 404, "not found", None, None))
    assert check_for_update("1.0", now=1000.0) is None


def test_bad_payload_silent(monkeypatch):
    _mock_urlopen(monkeypatch, {"unexpected": True})
    assert check_for_update("1.0", now=1000.0) is None


# ---- 缓存行为 -----------------------------------------------------------------


def test_cache_within_24h_no_second_request(monkeypatch):
    calls = []

    def counting_urlopen(req, timeout=None):
        calls.append(1)
        raise urllib.error.URLError("should only be called once if cached")

    payload_calls = []

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(_payload("1.1.0")).encode()

    def urlopen(req, timeout=None):
        payload_calls.append(1)
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    info = check_for_update("1.0", now=1000.0)
    assert info is not None

    # 换成「只要被请求就抛错」：缓存期内不应再发请求
    monkeypatch.setattr(urllib.request, "urlopen", counting_urlopen)
    again = check_for_update("1.0", now=1000.0 + 3600)  # 1h 后，仍在 24h 内
    assert again is not None  # 复用缓存的 new_version 提示
    assert calls == []  # 未发请求

    # 超过 24h：重新请求（换成正常返回 mock）
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    third = check_for_update("1.0", now=1000.0 + 25 * 3600)
    assert third is not None
    assert len(payload_calls) == 2  # 第一次 + 超期后这一次；缓存期内那一次没发请求


# ---- 占位仓库 / 开关 -----------------------------------------------------------


def test_placeholder_repo_skipped(monkeypatch):
    monkeypatch.setattr(uc, "UPDATE_REPO", "daming/daming-skillgain")

    def must_not_call(req, timeout=None):
        raise AssertionError("placeholder repo must not hit network")

    monkeypatch.setattr(urllib.request, "urlopen", must_not_call)
    assert fetch_latest_release() is None
    assert check_for_update("1.0", now=1000.0) is None


def test_skip_env_disables_check(monkeypatch):
    monkeypatch.setenv("DAMING_SKILLGAIN_SKIP_UPDATE_CHECK", "1")
    assert maybe_check_update("1.0") is None


# ---- 不阻塞主流程 --------------------------------------------------------------


def test_cli_async_check_does_not_block_or_crash(monkeypatch, capsys):
    """后台线程版：主流程立即返回；网络失败静默；提示走 stderr 不污染 stdout。"""
    monkeypatch.setenv("DAMING_SKILLGAIN_UPDATE_CACHE", "/nonexistent-dir-xyz/last_check")

    _mock_urlopen(monkeypatch, _payload("9.9.9"))
    start = time.monotonic()
    uc.maybe_check_update_async("1.0")
    elapsed = time.monotonic() - start
    assert elapsed < 0.5  # 主流程未阻塞（网络在线程里）

    # 给线程一点时间完成，然后验证 stderr 提示（注意 readouterr 每次调用会清空，需累积）
    deadline = time.monotonic() + 3
    err_acc = ""
    out_acc = ""
    while time.monotonic() < deadline and "9.9.9" not in err_acc:
        captured = capsys.readouterr()
        err_acc += captured.err
        out_acc += captured.out
        time.sleep(0.05)
    assert "9.9.9" in err_acc
    assert "git pull" in err_acc
    assert out_acc == ""


def test_async_check_network_failure_silent(monkeypatch, capsys):
    _mock_urlopen(monkeypatch, None, exc=urllib.error.URLError("offline"))
    uc.maybe_check_update_async("1.0")
    time.sleep(0.5)
    captured = capsys.readouterr()
    assert captured.err == ""  # 静默，无报错输出
