from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from evaluator import __version__
from evaluator.demo import build_demo_payload, generate_demo_report
from evaluator.doctor import run_doctor
from evaluator.report_payload import check_report_self_contained
from evaluator.update_checker import PUBLIC_VERSION, _is_placeholder_repo


def test_demo_report_covers_three_verdicts_and_is_self_contained(tmp_path: Path):
    out = tmp_path / "demo.html"
    res = generate_demo_report(output_path=out, open_browser=False)

    assert res["success"] is True
    assert res["demo"] is True
    assert Path(res["report_path"]) == out.resolve()
    assert out.exists() and out.stat().st_size > 1000

    html_text = out.read_text(encoding="utf-8")
    assert check_report_self_contained(html_text) == []
    assert "claude-sonnet-4" in html_text
    assert "gpt-4.1-mini" in html_text
    assert "deepseek-v3" in html_text
    # Negative transfer alert should be rendered for gpt-4.1-mini
    assert "这些模型上带 skill 反而更差" in html_text

    # First-screen plain-language check
    folded_start = html_text.index("<details")
    first_screen = re.sub(r"<style>.*?</style>", "", html_text[:folded_start], flags=re.S)
    first_screen = re.sub(r"<script>.*?</script>", "", first_screen, flags=re.S)
    for term in ("Δ", "σ²", "N=", "rep", "With/Without", "断言", "置信区间", "宿主"):
        assert term not in first_screen, term


def test_demo_payload_structure():
    payload = build_demo_payload()
    verdicts = {g["verdict"] for g in payload["p1"]["pair_gains"]}
    assert verdicts == {"skill_effective", "skill_harmful", "inconclusive"}


def test_doctor_does_not_leak_secret_values(monkeypatch):
    secret = "sk-super-secret-value-12345"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    report = run_doctor()

    assert "python" in report
    assert "core_dependencies" in report
    assert "hosts" in report
    assert "OPENAI_API_KEY" in report["configured_api_key_envs"]
    assert secret not in json.dumps(report, ensure_ascii=False)


def test_update_checker_real_repo_is_not_placeholder(monkeypatch):
    monkeypatch.delenv("DAMING_SKILLGAIN_REPO", raising=False)
    assert _is_placeholder_repo() is False
    assert PUBLIC_VERSION == __version__

    monkeypatch.setenv("DAMING_SKILLGAIN_REPO", "owner/repo")
    assert _is_placeholder_repo() is True


def test_cli_version_demo_and_doctor(tmp_path: Path):
    env = dict(os.environ)
    env["DAMING_SKILLGAIN_SKIP_UPDATE_CHECK"] = "1"

    ver_proc = subprocess.run(
        [sys.executable, "-m", "evaluator.cli", "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert ver_proc.returncode == 0
    assert __version__ in ver_proc.stdout

    demo_out = tmp_path / "cli-demo.html"
    demo_proc = subprocess.run(
        [sys.executable, "-m", "evaluator.cli", "demo", "--output", str(demo_out)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert demo_proc.returncode == 0, demo_proc.stderr
    demo_json = json.loads(demo_proc.stdout)
    assert demo_json["success"] is True
    assert demo_out.exists()

    doc_proc = subprocess.run(
        [sys.executable, "-m", "evaluator.cli", "doctor"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert doc_proc.returncode in (0, 1)
    doc_json = json.loads(doc_proc.stdout)
    assert "python" in doc_json
    assert "hosts" in doc_json
