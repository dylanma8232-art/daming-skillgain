<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="assets/banner-light.png">
  <img alt="daming-skillgain — Pre-release quality gate for AI Agent Skills" src="assets/banner-dark.png" width="100%">
</picture>

<div align="center">

<p>
  <a href="README.md"><img alt="简体中文" src="https://img.shields.io/badge/语言-简体中文-2b2927?style=flat-square"></a>
  <a href="README_EN.md"><img alt="English" src="https://img.shields.io/badge/Language-English-c8521b?style=flat-square"></a>
  <a href="https://github.com/dylanma8232-art/daming-skillgain/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/dylanma8232-art/daming-skillgain/ci.yml?style=flat-square&label=CI"></a>
  <a href="https://github.com/dylanma8232-art/daming-skillgain/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/dylanma8232-art/daming-skillgain?display_name=tag&style=flat-square"></a>
  <a href="pyproject.toml"><img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776ab?style=flat-square"></a>
  <a href="LICENSE"><img alt="License: GPL-3.0" src="https://img.shields.io/badge/License-GPL--3.0-2e7d32?style=flat-square"></a>
</p>

<p>
  <b>Works with</b>&nbsp;
  <img alt="Claude Code" src="https://img.shields.io/badge/Claude_Code-D97757?style=flat-square&logo=claude&logoColor=white">
  <img alt="Codex" src="https://img.shields.io/badge/Codex-18181B?style=flat-square">
  <img alt="Cursor" src="https://img.shields.io/badge/Cursor-18181B?style=flat-square&logo=cursor&logoColor=white">
  <img alt="OpenCode" src="https://img.shields.io/badge/OpenCode-27272A?style=flat-square">
  <img alt="Any CLI Agent" src="https://img.shields.io/badge/Any_CLI_Agent-Wrapper_Script-52525B?style=flat-square">
</p>

[30-Second Quickstart](#30-second-quickstart) · [Report Preview](#what-the-report-looks-like) · [Host Integrations](references/hosts.md) · [Changelog](CHANGELOG.md)

</div>

---

You have written a dozen `SKILL.md` files — how do you know they actually make your agent better rather than quietly hurting output quality?

- **First release**: The instructions read well, yet do they measurably beat the exact same model *without* the Skill on real tasks?
- **Cross-model negative transfer**: A Skill that helps a frontier model can confuse smaller models, double token usage, and degrade deliverables.
- **Iterative edits**: Did your latest prompt edit fix an edge case or introduce a silent regression?

**`daming-skillgain`** is a **pre-release quality gate for AI Agent Skills**. It runs paired **with-Skill vs. without-Skill** experiments on the same model and real task, combines **zero-LLM deterministic assertions** with **anonymized blind reviews**, and outputs a **single-file offline HTML report** — giving you reproducible evidence on whether to ship a Skill, and which models should have it turned on or off.

> Focused strictly on measuring real net Skill gain: it does not rewrite your Skill and is not a bloated general-purpose LLM benchmark suite.

---

## 30-Second Quickstart

**Use it inside an agent or desktop AI**: Download and extract the complete Skill ZIP from [Releases](https://github.com/dylanma8232-art/daming-skillgain/releases/latest). Import the folder containing `SKILL.md` and ask “Use daming-skillgain to generate a demo report.” The client needs access to the Skill files and local commands. Evaluating another Skill also requires a configured tool to run the experiments. See [the Skill instructions](SKILL.md).

### 1. Install the CLI (`skillgain`)

```bash
pip install git+https://github.com/dylanma8232-art/daming-skillgain.git
```

<details>
<summary>Or install from source / Release ZIP</summary>

```bash
git clone https://github.com/dylanma8232-art/daming-skillgain.git
cd daming-skillgain
pip install -e .
```

Pre-packaged Skill bundles are also available on [Releases](https://github.com/dylanma8232-art/daming-skillgain/releases/latest).

</details>

### 2. Inspect a live report instantly (zero API keys, < 1 second)

```bash
skillgain demo --open
```

Generates and opens a self-contained HTML report demonstrating all three verdicts (**Effective**, **Harmful / Negative Transfer**, and **Inconclusive**) with zero configuration.

### 3. Check your local tools & run an evaluation

```bash
skillgain doctor   # Inspect Python, deps, detected AI CLIs, and API key status
skillgain simple   # Guided 4-question wizard -> runs paired tasks and writes report.html
```

For CI or repeatable regression checks, pass a profile directly:

```bash
skillgain pipeline --profile examples/eval-config.example.json
```

---

## What the Report Looks Like

![Actual report screenshot: summary verdict, per-model breakdown, and side-by-side deliverables](assets/report-screenshot.png)

- **Decision-first first screen**: Clear verdict headline, core metrics, pass/fail rule checklist, and explicit negative-transfer warnings.
- **Side-by-side deliverables**: Inspect actual files produced with and without the Skill (Markdown, spreadsheets, slides, code).
- **100% offline single file**: All CSS and scripts are inlined with zero external requests — drop the `.html` file into Slack, PRs, or email.

---

## Why daming-skillgain

| Dimension | Manual spot-checks | Generic LLM eval frameworks | **daming-skillgain** |
|---|---|---|---|
| Core question | Gut feeling | Model / prompt leaderboards | **Net paired Skill gain on the same model (With vs. Without)** |
| Negative transfer | Easily missed | Build custom stats | **Highlights degraded models and recommends turning the Skill off** |
| Insufficient evidence | Tempts guessing | Raw averages hide variance | **Reports `Inconclusive` when confidence intervals cross zero** |
| Reproducibility | Unrepeatable | Heavy custom plumbing | **Zero-LLM rule assertions + `skillgain verify-snapshot`** |
| Sharing results | Chat screenshots | Needs a running web server | **Single-file offline HTML** |
| Host onboarding | Ad-hoc | SDK & dataset boilerplate | **Built-in CLI adapters or a 10-line wrapper script for any agent** |

---

## Three Honest Verdicts & Pipeline

- **Effective**: Paired evidence confirms better deliverables with the Skill; safe to enable on that model.
- **Harmful (Negative Transfer)**: Evidence shows degraded output; surfaced on the first screen so you disable it on that model.
- **Inconclusive**: Sample size is too small or variance is too high; uncertainty stays explicit instead of forcing a call.

```mermaid
flowchart LR
    A["Skill + Real Task"] --> B["Same-Model Paired Runs<br/>Without Skill vs. With Skill"]
    B --> C["Deterministic Checks<br/>(Zero-LLM · Verifiable Snapshot)"]
    B --> D["Anonymous Blind Review<br/>(Condition Labels Stripped)"]
    C --> E["Single-File Offline HTML Report<br/>Verdict · Negative-Transfer Alert · Side-by-Side Diff"]
    D --> E
```

---

## Supported AI Tools & Interfaces

The agent under test requires **no plugins**. Everything runs through `HostProtocol`:

| Integration | Supported Tools | Notes |
|---|---|---|
| **Built-in Adapters** | `opencode`, `claude-code`, `codex`, `cursor-agent` | Select via `--host <name>`; run `skillgain doctor` to inspect local readiness |
| **Universal Script Contract** | Cursor, Trae, Windsurf, Cline, or any CLI agent | Wrap any CLI in ~10 lines (see [`examples/wrapper_template.py`](examples/wrapper_template.py)) |
| **Direct Model API** | `api`, `standalone`, `agent` | Ideal for text-only tasks; file-producing tasks are flagged during preflight |

Also includes a **Python SDK**, **REST API (`daming-skillgain[api]`)**, **MCP Server (`daming-skillgain[mcp]`)**, and a ready-to-install [`SKILL.md`](SKILL.md). See [references/usage.md](references/usage.md).

---

## Evidence & Boundaries

- **Deterministic checks first**: Built-in assertions never call an LLM; rules and results are snapshotted and independently verifiable via `skillgain verify-snapshot`.
- **No forced verdicts**: Verdicts enforce sample, delta, and confidence thresholds; insufficient evidence stays `Inconclusive`.
- **Blind review as audited evidence**: Judges compare anonymized outputs and raw rationale is preserved rather than treated as absolute truth.
- **No cross-host extrapolation**: Reports explicitly state which host, models, and task were tested.

---

## Examples & Documentation

- [`examples/sample-skill/SKILL.md`](examples/sample-skill/SKILL.md) — Ready-to-test sample Skill
- [`examples/eval-config.example.json`](examples/eval-config.example.json) — Non-interactive evaluation profile template
- [`examples/wrapper_template.py`](examples/wrapper_template.py) — Universal script adapter for any CLI agent
- [`references/workflow.md`](references/workflow.md) — Step-by-step pipeline (`plan` → `run` → `evaluate` → `report`)
- [`references/hosts.md`](references/hosts.md) — Host capability matrix & integration guide
- [`references/host-protocol.md`](references/host-protocol.md) — Implementing a new native host adapter

---

## Author

Built by **[Dylan.ma](https://github.com/dylanma8232-art)** · [Explore more open-source tools & experiments →](https://github.com/dylanma8232-art?tab=repositories)

Contributions and real-world evaluation stories are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). If `daming-skillgain` helped you catch negative transfer before shipping, consider giving it a **Star**.

License: [GPL-3.0](LICENSE)
