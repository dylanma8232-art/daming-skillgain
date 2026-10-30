# daming-skillgain 技术摸底（工部 · 备料令）

日期：2026-10-30。只读摸底，未改代码、未提交。仓库 `/Users/wenchen.ma/Desktop/daming-skillgain`，HEAD `7c10446`。

## 一、测试基线（已跑通）

```
$ python3 -m pytest tests/ -x -q
67 passed, 1 skipped in 0.36s        rc=0
```

- Python 3.9.6（系统自带），requirements 核心依赖 pyyaml/openpyxl/requests/httpx 测试全绿无需额外安装。
- 跳过的 1 例是环境相关（无对应 CLI/服务）。
- **注意**：3.9 语法约束——`X | None` 类型标注靠 `from __future__ import annotations` 才没炸，新代码须保持该 import 或用 typing.Optional。

## 二、executors 适配短板（主人目标：任何 agent 可用）

现有宿主：`base.py`(抽象) / `standalone.py` / `api.py` / `agent.py` / `opencode.py` / `claude_code.py` / `codex.py` / `script.py`。

### codex.py（109 行）
| 短板 | 现状 | 影响 |
| --- | --- | --- |
| `--json` 未解析 | `format_json=True` 加了参数但 transcript=stdout 原样、`usage={}` 恒空 | 拿不到 token 用量、事件流；报告里 codex 宿主的成本数据缺失 |
| 无上下文隔离 | 不传 `--cd`/`--sandbox`/workspace 参数，只靠 `cwd=` | 被测 skill 可读写宿主任意目录（有 `--dangerously-bypass-approvals-and-sandbox` 开关但默认关） |
| 无会话续接 | 每次 `codex exec` 都是全新会话 | 多轮评估无法复用上下文，成本×轮数 |
| `files` 参数被忽略 | execute_session 签名收了 `files` 但从不使用 | 与其他 executor 接口不对齐 |
| check_connectivity 走真实模型 | 「回答一个字：好」timeout=60 | 每次连通性检查烧一次真实调用；应先 `--version` 探活再按需深度检查 |

### claude_code.py（141 行）
| 短板 | 现状 | 影响 |
| --- | --- | --- |
| prompt 走 argv | `cmd.extend(["--", prompt])` | 超长 prompt 有 ARG_MAX 截断风险（codex 用 stdin 是对的，这里没对齐）；claude CLI 支持 stdin 传 prompt |
| 无 skills 显式注入 | 注释说靠 `~/.claude/skills/` 自动发现，executor 不做任何注入动作 | 被测 skill 的安装/清理完全靠外部约定，评估目录与用户全局 skills 会互相污染 |
| 无 `--add-dir`/resume | 不传额外目录、不复用 session | 同 codex：上下文隔离与多轮都缺 |
| stream-json 解析尚可 | assistant text + result usage 都解析了 | 这是六个里最完整的，可当解析器范本 |
| check_connectivity 同样走真实模型 | 同 codex | 同上 |

### 通用层（「任何 agent 用」的真正缺口）
1. **缺统一宿主协议规范**：base.py 有 capabilities 声明（file_ops/skill_injection/transcript），但没有强制的「三件套」契约：① 非交互调用形态（stdin/--print/exec）；② 输出解析器（text/usage/exit_code）；③ 探活方式（--version 先于真实调用）。新接一个宿主（如 Gemini CLI、Cursor CLI、pi）没有可照抄的最小模板。
2. **skill_injection 只有约定没有实现**：`agents-md`（codex）和 `claude-skills-dir` 只是字符串声明，注入/清理逻辑不在 executor 里，散落在外部。
3. **错误语义不统一**：CLI 不存在时 validate_config 返回 (False, msg) 是对的，但执行中途 CLI 崩溃（非零 exit_code）与超时的区分、stderr 截断长度（200 字符）各家不一。

## 三、reporting.py 现状（1858 行）

- HTML 渲染全部是 Python f-string 拼接：CSS 是 1151 行附近的巨型内联字符串（.hero/.meta-grid 等全套样式），结构函数 62 个。
- 有 artifacts 预览栈（表格/幻灯片/data-uri 内嵌）、盲测投票数据、匿名 ID、组内载荷——功能全但**没有分层**：数据组装（build_report_payload）与渲染（_render_*）耦合在同一文件。
- 「更简单易懂」的重构建议方向（供翰林院定稿）：
  1. 拆三层：payload 组装（纯数据）/ 模板（单独 .html 模板或模板函数文件）/ 渲染入口；
  2. 报告结构改「结论先行」：首屏 verdict + 一句话总评 + 三个关键指标，明细全部折叠（<details>）；
  3. 保持自包含单文件输出（现有 data-uri 内嵌图片的做法保留）；
  4. 现有 62 个函数里 `_render_*` 15 个、`_artifact_*` 10 个可归并为模板层。

## 四、风险与前置

- 测试全部 mock/离线，**没有任何真实宿主端到端用例**——适配层改完后必须有「真 CLI 冒烟」（本机有哪些 CLI 可用待查：codex/claude 是否在 PATH）。
- 3.9 语法红线见上。
- GitHub 上该仓库有 weekly CI 自动优化 PR（#36），施工期间注意与远端同步策略（主人未授权 push）。

## 五、待翰林院定稿的问题

1. 「任何 agent」的范围清单：除了 codex/claude，opencode/agent/script 之外还要点名哪些（Gemini CLI？pi？）
2. 报告重构的「简单」标准：一页式？信息密度上限？
3. executor 通用协议是否要立 `HostProtocol` 规范文档（references/ 下），新宿主按文档接入。

## 六、本机 CLI 可用性（实测 2026-10-30）

```
codex: 无    claude: 无    opencode: 无    gemini: 无    pi: 无
cursor-agent: /Users/wenchen.ma/.local/bin/cursor-agent  → --version 2026.10.22-27dcf5f rc=0
```

结论：**本机当前只有 cursor-agent 可做真实宿主冒烟**；codex/claude 的适配改动只能靠单测 + mock 验证，真机回归要么等主人提供环境，要么先落 cursor-agent 适配作为「第六宿主」顺带打通通用协议。
