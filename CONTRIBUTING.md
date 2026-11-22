# Contributing to daming-skillgain

感谢你关注 **daming-skillgain**！无论是提交 Bug、补充新的 AI 工具适配器、优化报告体验还是完善文档，都非常欢迎。

## 本地开发环境

```bash
git clone https://github.com/dylanma8232-art/daming-skillgain.git
cd daming-skillgain
pip install -e ".[dev]"
pytest -q
```

快速烟雾测试：

```bash
skillgain --version
skillgain doctor
skillgain demo --output /tmp/skillgain-demo.html
```

## 常见贡献方向

1. **新增 AI 工具宿主适配器**
   - 阅读 [references/host-protocol.md](references/host-protocol.md)，按 `HostProtocol` 三件套（`build_invocation` / `parse_output` / `probe`）实现并注册到 `evaluator/executors/`。
   - 在 `tests/` 中补充协议契约测试，无需依赖外部真实 LLM 即可在 CI 通过。
2. **新增确定性断言规则**
   - 参考 `evaluator/assertions/`，保证同输入、同规则下 100% 可复算、零 LLM 依赖。
3. **报告体验与可视化改进**
   - 保持单文件 HTML 报告 **100% 自包含（零外链 CSS/JS）** 与首屏大白话约束（通过 `tests/test_report_self_contained.py` 校验）。

## 提交规范

- 尽量保持每个 PR 聚焦单一目标，并附带可自动运行的单测。
- 代码保持 `from __future__ import annotations`，兼容 Python 3.10+（及本地 Python 3.9 运行环境）。
- 提交前确认 `pytest -q` 全绿。
