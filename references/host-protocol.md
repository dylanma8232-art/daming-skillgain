# 宿主接入协议（HostProtocol）

- 日期：2026-10-30
- 状态：已落地（对应改造项 A1–A5 / A8 / B4）。代码基座：`evaluator/executors/base.py`。
- 适用范围：本文是所有 CLI 型 Agent 宿主接入的唯一口径。新增宿主照抄本文，不再一家一适配。

---

## 1. 适用范围与原则

**纳入标准（三条同时满足）：**

1. 可非交互调用（一次命令拿结果，不需要人类点确认）；
2. 能输出结构化结果（至少 stdout 文本，最好事件流 / usage）；
3. 有零成本探活方式（`--version`，rc=0 判可用）。

**不纳入：** 只有 GUI 没有稳定公共 CLI 的工具；纯 API 宿主（`api` / `standalone`）不走本协议，
维持「无文件能力 + 产物型任务显式拦截」的既有边界。

**三条纪律不变：**

- 声明与实现一致：`capabilities` 里写支持，就必须真的支持；不支持就显式报错（`UnsupportedCapabilityError`），
  **禁止静默降级**。
- 主链只经 `evaluator/host_session.py` 分发，不得直接 import 宿主专用传输代码。
- 所有失败都必须可解释：错误分类（retryable / fatal）+ stderr 截断统一。

---

## 2. 三件套契约（新宿主最小接入面）

一个宿主 = 三个方法。继承 `BaseExecutor` 后只需实现：

```python
def build_invocation(...) -> HostInvocation   # ① 非交互调用形态
def parse_output(...) -> SessionResult        # ② 输出解析器
def probe(...) -> ProbeResult                 # ③ 探活（基类已给默认实现）
```

### 2.1 非交互调用形态（invocation）

`HostInvocation` 字段：

| 字段 | 含义 |
|---|---|
| `argv` | 实际命令行（list，不经 shell） |
| `stdin` | 通过标准输入投递的 prompt；`None` 表示 prompt 走 argv |
| `cwd` | 进程工作目录 |
| `env` | 追加环境变量（在 `os.environ` 之上覆盖） |
| `prompt_channel` | `"stdin"` / `"argv"`，显式声明，便于审计 ARG_MAX 风险 |

**约定：**

- **prompt 通道：stdin 优先**，规避 ARG_MAX。上游 CLI 只支持位置参数时用 `argv`，必须在
  `prompt_channel` 标注，并在宿主模块 docstring 记录长度限制。
- **工作目录隔离**：被测 skill 只可在 workspace 内读写。可行的参数：
  codex `--cd <dir>`、cursor-agent `--workspace <dir>`、claude `--add-dir` 白名单。
- **权限放行语义对齐**：`dangerous_skip_permissions=True` 时分别映射为
  codex `--dangerously-bypass-approvals-and-sandbox`、claude `--dangerously-skip-permissions`、
  cursor-agent `--force`。默认关闭。
- **附加文件**：宿主有原生附件参数就用；没有就用 `augment_prompt_with_files()` 把路径写进 prompt
  （`files` 参数不得被静默忽略）。
- **会话续接**：支持 resume 的宿主把会话 id 存在 `self.last_session_id`，下次通过
  `config["resume_session"]` 或自动续接复用。

### 2.2 输出解析器（output parser）

`parse_output(stdout, stderr, exit_code) -> SessionResult`，必须填充：

| 字段 | 要求 |
|---|---|
| `text` | 从事件流中提取的**模型文本回复**（评委/总结路径使用） |
| `transcript` | 可读过程记录（无则回退 stdout） |
| `usage` | token 用量；**采不到时必须显式写 `{"collected": False}`**，不允许假装是 0 |
| `exit_code` | 原样透传 |

**兜底规则：** 输出不是结构化事件流（无一行能解析成 JSON）时，stdout 原样当 `text`，
`usage={"collected": False}`，不得抛异常。

**范本：** `claude_code.py`（`assistant` 事件 text + `result` 事件 usage）。

### 2.3 探活方式（probe）

两级：

1. **第一级（默认，零成本）**：`<cli> --version`，rc=0 即可用。基类 `probe()` 已实现，
   子类只需覆盖 `default_cli_path`；错误文案可加「怎么装」的提示。
2. **第二级（按需，烧真实调用）**：调用一次最小 prompt。必须显式开启
   （`config["deep_connectivity"] = true`），默认关闭，避免每次连通检查都烧钱。

`validate_config()` 默认即走第一级探活：CLI 未安装时返回 `(False, 明确文案)`，由调用方
显式报错，**不得静默换宿主**。

---

## 3. capabilities 声明

`capabilities` 字典的已知键：

| 键 | 取值 | 含义 |
|---|---|---|
| `file_ops` | `bool` | 宿主能否在工作区内读写文件（产物型评测的必要条件） |
| `skill_injection` | 字符串 | 注入约定标识：`workspace-skills-dir` / `claude-skills-dir` / `agents-md` / `script-skills-dir` / `none` |
| `transcript` | `bool` | 能否产出过程记录 |
| `resume` | `bool` | 能否复用会话续接 |

`require_capability(name, purpose)` 在能力缺失时抛 `UnsupportedCapabilityError`，
报错里带宿主名、能力名、用途和该宿主已声明的能力集合。

---

## 4. skill 注入与清理

**实现唯一归属：** `evaluator/subject_adapter/`。executor 只做委派
（`BaseExecutor.inject_skill()` / `cleanup_skill()`），保证「声明」与「实现」只有一个来源。

| 宿主 | 注入 | 清理 |
|---|---|---|
| `opencode` | `.opencode/skills/<name>/` + `opencode.json` 放行 | 删目录 + 校验不存在 |
| `claude-code` | `.claude/skills/<name>/` | 删目录 + 校验不存在 |
| `script` | `.skills/<name>/` | 删目录 + 校验不存在 |
| `codex` | `AGENTS.md` 追加带哨兵的注入段 + `.daming-skillgain/skills/` 追溯副本 | 移除哨兵段 + 删追溯副本 |
| `cursor-agent` | 同 codex（AGENTS.md 约定） | 同 codex |
| `agent` / `api` / `standalone` | 不支持（`NoneSubjectAdapter`），注入请求显式报错 | — |

**三条硬要求：**

1. **幂等**：重复注入不产生重复段落（哨兵 `<!-- daming-skillgain:skill-injection:begin -->`）。
2. **without-skill 严格隔离**：对照模式下必须清理干净，否则是假对照。
3. **加载验证**：注入后必须 `verify_skill_loaded()` 有证据（目录 + SKILL.md 或 AGENTS.md 哨兵段），
   失败即抛 `SubjectAdapterError`，不得继续跑。

---

## 5. 错误语义统一

| 类别 | 判定 | 处理 |
|---|---|---|
| `retryable` | 超时 / 网络抖动 / 429 / 502-504 / connection reset / 124 | 自动重试（默认 ≤2 次），计入 `invalid`，不计入能力分母 |
| `fatal` | CLI 缺失 / 配置错误 / 能力不支持 / 其它非零退出 | 立即停止，给出修复指引，不空转重试 |

- **stderr 截断**：一律 `STDERR_TRUNCATE = 500` 字符（`truncate_stderr()`）。
- **超时**：`subprocess.TimeoutExpired` 由上层归一化为 `invalid + command_timeout`。
- **validate_config 文案规范**：必须包含「宿主名 + 找不到/不可用 + 怎么办」三段，
  例：`找不到 codex CLI: codex。请安装 OpenAI Codex CLI 后重试，或改用 mock/script 执行器完成端到端验证。`

---

## 6. 宿主接入空位登记表

本期登记、不实现（留位）：

| 宿主 | 状态 | 备注 |
|---|---|---|
| Gemini CLI | 空位 | 若满足三条纳入标准，按第 2 节实现 `build_invocation` / `parse_output` 并注册到 `HOST_REGISTRY` 与 `SUBJECT_HOST_ADAPTERS` |
| pi | 空位 | 同上；注意其非交互形态与 session 复用约定 |

已在册宿主：`opencode`、`claude-code`、`codex`、`cursor-agent`、`script`、`agent`、`api`、`standalone`。

排查命令：`python3 -m evaluator.cli hosts`（输出各宿主 capabilities + 注入约定 + 是否已安装）。
