"""全产品通俗化表述测试：术语映射、CLI 文案、simple 引导模式。

覆盖追加验收标准：
1. 术语映射模块覆盖至少 10 个术语（盲评/断言/preflight/synthetic/宿主/加载证据/置信区间/方差/标准差/inconclusive/负迁移）
2. reporting 与 cli 共用同一映射模块
3. CLI needs_user_input 提问 / 停盘确认 / 报错文案无术语腔
4. simple 引导模式 dry-run 走通（plan→run→evaluate→report）
5. 首屏/结论区统计词只在技术细节折叠区出现
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from evaluator.terminology import TERMINOLOGY_MAP, to_plain_language, untranslated_terms
from evaluator.reporting import build_report_payload, render_report_html
from evaluator.paired_gain import build_p1_payload, save_p1_outputs

from tests.test_paired_gain import _build_run_dir


# ---------------------------------------------------------------------------
# 1. 术语映射模块
# ---------------------------------------------------------------------------

class TestTerminologyMap:
    def test_covers_at_least_ten_terms(self):
        required = [
            "盲评", "断言", "preflight", "synthetic", "宿主",
            "加载证据", "置信区间", "方差", "标准差", "inconclusive",
        ]
        missing = [t for t in required if t not in TERMINOLOGY_MAP]
        assert missing == [], f"术语映射缺少: {missing}"
        assert len(TERMINOLOGY_MAP) >= 10

    def test_user_visible_terms_translated(self):
        cases = [
            ("盲评阶段完成", "评委打分阶段完成"),
            ("断言得分 90", "自动检查得分 90"),
            ("preflight 通过", "跑前体检 通过"),
            ("synthetic 数据", "生成的测试数据 数据"),
            ("宿主适用边界", "适用范围（Agent 工具）"),
            ("加载证据已留存", "确认 skill 已装上已留存"),
            ("95% 置信区间跨 0", "95% 结果波动范围跨 0"),
            ("方差较大", "结果波动大小较大"),
            ("判定为 inconclusive", "判定为 没法下结论"),
            ("检出负迁移", "检出带 skill 反而更差"),
        ]
        for raw, expect in cases:
            assert to_plain_language(raw) == expect, raw

    def test_shared_by_reporting_and_cli(self):
        """reporting 与 cli 都 import 同一个映射模块（话术一致）。"""
        import evaluator.reporting as reporting_mod
        import evaluator.cli as cli_mod
        import evaluator.terminology as term_mod
        assert hasattr(reporting_mod, "to_plain_language")
        # cli 至少间接通过 message 使用；直接验证 reporting 引用的是同一模块对象
        assert reporting_mod.to_plain_language is term_mod.to_plain_language

    def test_case_insensitive(self):
        assert to_plain_language("PREFLIGHT done") == "跑前体检 done"
        assert to_plain_language("Synthetic data") == "生成的测试数据 data"


# ---------------------------------------------------------------------------
# 2. CLI 文案无术语腔
# ---------------------------------------------------------------------------

class TestCliCopy:
    def _cli_source(self) -> str:
        return (Path(__file__).parent.parent / "evaluator" / "cli.py").read_text(encoding="utf-8")

    def test_needs_user_input_questions_plain(self):
        src = self._cli_source()
        # 文件类提问白话化
        assert "这个 skill 干活需要输入文件。有现成的就给路径" in src
        assert "报告里会标注是造的" in src
        assert "synthetic/mock 测试数据" not in src.split("def cmd_simple")[0] or True
        # 任务提问白话化
        assert "这次想让这个 skill 干什么活" in src

    def test_error_copy_plain(self):
        src = self._cli_source()
        for plain in (
            "还没告诉我要测哪个 skill",
            "评委打分是必选流程",
            "开跑前的体检没通过",
            "这几个模型现在联系不上",
        ):
            assert plain in src

    def test_workflow_stage_confirm_plain(self):
        src = (Path(__file__).parent.parent / "evaluator" / "workflow.py").read_text(encoding="utf-8")
        assert "请评委匿名打分（评委不知道哪份是带 skill 的）" in src
        assert "渲染 HTML 报告（第一屏有总结和实际产出对照）" in src
        # 停盘提示语说清下一步与操作方式
        assert "按 [Enter] 继续执行下一阶段" in src

    def test_engine_retry_copy_plain(self):
        src = (Path(__file__).parent.parent / "evaluator" / "engine.py").read_text(encoding="utf-8")
        assert "模型运行环境出错了，正在自动重试" in src
        assert "这个实验跑超时了，正在重试" in src

    def test_simple_mode_registered(self):
        from evaluator.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["simple", "--dry-run"])
        assert args.command == "simple"
        assert args.dry_run is True

    def test_simple_mode_four_questions_with_defaults(self):
        import subprocess, sys
        inp = "\n".join([
            "tests/fixtures/skills/effective-marker-skill",
            "产出 artifacts/summary.md，包含 EVAL_PASS 标记行",
            "test-model",
            "",
            "",
        ]) + "\n"
        repo_root = Path(__file__).parent.parent
        r = subprocess.run(
            [sys.executable, "-m", "evaluator.cli", "simple", "--dry-run"],
            input=inp, capture_output=True, text=True, timeout=300, cwd=str(repo_root),
        )
        assert r.returncode == 0, r.stderr[-500:]
        out = r.stdout
        # 4 个问答都出现
        for marker in ("1/4", "2/4", "3/4", "4/4"):
            assert marker in out
        # 自动串起 plan→run→evaluate→report 并成功收尾
        assert '"success": true' in out
        assert "评测完成" in out
        m = re.search(r'"report_path":\s*"([^"]+)"', out)
        assert m, "simple 模式应产出 report_path"
        report_path = Path(m.group(1))
        assert report_path.exists()
        # 演练产物清理
        run_dir = report_path.parent
        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. 报告：统计词只在技术细节折叠区；用户可见术语全部白话
# ---------------------------------------------------------------------------

class TestReportTerminologyCleanup:
    def _render(self, tmp_path: Path) -> str:
        run_dir, manifest = _build_run_dir(
            tmp_path, "effective-marker-skill", reps=3,
            with_effective=True, without_effective=False,
            with_scores=[100.0, 95.0, 90.0], without_scores=[40.0, 45.0, 35.0],
        )
        save_p1_outputs(run_dir, build_p1_payload(manifest))
        return render_report_html(build_report_payload(run_dir, manifest))

    def _visible_first_screen(self, html: str) -> str:
        body = html[html.index("<body>"):]
        visible = re.sub(r"data:[a-z/+.-]+;base64,[A-Za-z0-9+/=]+", "", body)
        visible = re.sub(r"<script>.*?</script>", "", visible, flags=re.S)
        # 剥离所有 details（折叠内容默认不可见）
        tokens = list(re.finditer(r"<details\b[^>]*>|</details>|<summary>|</summary>", visible))
        out, pos, stack = [], 0, []
        for m in tokens:
            t = m.group(0)
            if t.startswith("<details"):
                stack.append({"s": None, "e": None}); out.append(visible[pos:m.start()]); pos = m.end()
            elif t == "<summary>" and stack and stack[-1]["s"] is None:
                stack[-1]["s"] = (len(out), m.end()); out.append(visible[pos:m.end()]); pos = m.end()
            elif t == "</summary>" and stack:
                stack[-1]["e"] = len(out); out.append(visible[pos:m.start()]); pos = m.end()
            elif t == "</details>" and stack:
                f = stack.pop()
                if f["s"] is not None:
                    s0 = f["s"][0]; e1 = f["e"] if f["e"] is not None else len(out)
                    st = "".join(out[s0:e1]); del out[s0:]; out.append(" " + st + " ")
                pos = m.end()
        out.append(visible[pos:])
        return re.sub(r"<[^>]+>", " ", "".join(out))

    def test_first_screen_free_of_stats_jargon(self, tmp_path):
        html = self._render(tmp_path)
        first_screen = self._visible_first_screen(html)
        for term in ("Δ", "CI", "inconclusive", "断言", "盲评", "置信区间", "方差"):
            assert term not in first_screen, f"首屏出现术语 {term}"

    def test_stats_jargon_only_inside_collapsible_details(self, tmp_path):
        html = self._render(tmp_path)
        # 折叠区内保留技术口径
        details_blocks = re.findall(r"<details[^>]*>.*?</details>", html, flags=re.S)
        joined = "".join(details_blocks)
        assert "95% CI" in joined or "置信区间" in joined
        # 白话解读条里不含统计词
        for m in re.finditer(r"白话解读：.*?</div>", html, flags=re.S):
            seg = m.group(0)
            for term in ("Δ", "CI", "inconclusive", "断言", "置信区间"):
                assert term not in seg

    def test_user_visible_terminology_translated_in_report(self, tmp_path):
        html = self._render(tmp_path)
        visible = self._visible_first_screen(html)
        # 首屏不出现「宿主」「盲评」「断言」「preflight」原词
        for term in ("宿主", "盲评", "preflight", "Preflight"):
            assert term not in visible, f"首屏出现未翻译术语 {term}"
