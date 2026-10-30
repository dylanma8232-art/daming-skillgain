"""
Skill 分析器：从 SKILL.md 中提取资源、能力、执行模式。
不再依赖关键词猜类别，全部由 SKILL.md 内容驱动。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ========== 数据模型 ==========

@dataclass
class SkillMetadata:
    name: str = ""
    description: str = ""
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class SkillCapability:
    summary: str = ""
    core_actions: list[str] = field(default_factory=list)
    output_type: str = ""
    output_formats: list[str] = field(default_factory=list)
    has_interactive_steps: bool = False


@dataclass
class SkillResource:
    name: str = ""
    type: str = ""
    description: str = ""
    source: str = "user"
    value: str = ""
    status: str = "missing"
    required: bool = False
    blocking: bool = False
    supports_auto_generation: bool = False
    sensitivity: str = "normal"
    generation_hint: str = ""
    validation_hint: str = ""
    example: str = ""
    confidence: float = 1.0
    evidence: str = ""
    detection_reason: str = ""


@dataclass
class SkillAnalysis:
    skill_name: str = ""
    skill_path: str = ""
    capability: SkillCapability = field(default_factory=SkillCapability)
    required_resources: list[SkillResource] = field(default_factory=list)
    execution_mode: str = "interactive"
    estimated_task_type: str = ""
    tools_required: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    evaluation_notes: list[str] = field(default_factory=list)
    evaluation_contract: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "skill_path": self.skill_path,
            "capability": asdict(self.capability),
            "required_resources": [asdict(r) for r in self.required_resources],
            "execution_mode": self.execution_mode,
            "estimated_task_type": self.estimated_task_type,
            "tools_required": list(self.tools_required),
            "triggers": list(self.triggers),
            "evaluation_notes": list(self.evaluation_notes),
            "evaluation_contract": dict(self.evaluation_contract),
        }


# ========== 资源检测 ==========

RESOURCE_RULES = [
    {
        "type": "file",
        "name": "input_file",
        "patterns": [
            r"(?:xlsx|xls|csv|json|xml|pdf|docx?|文件|表格|模板|数据集|数据文件|样例数据)",
            r"(?:文件路径|目录|文件夹|输入文件)",
        ],
        "source": "user",
        "supports_auto_generation": True,
        "blocking": False,
        "sensitivity": "low",
        "generation_hint": "用户若没有可直接提供的样例数据，可退化为生成合成测试数据。",
        "validation_hint": "优先提供真实样例文件路径；没有时允许使用生成的 mock 数据，但报告里要标记为合成数据。",
        "example": "/path/to/input.xlsx",
    },
    {
        "type": "credential",
        "name": "credential",
        "patterns": [
            r"(?:账号|账户|用户名|password|密码|secret|密钥|token|key)",
            r"(?:登录名|access key|secret key|merchantPid|pid)",
        ],
        "source": "user",
        "supports_auto_generation": False,
        "blocking": True,
        "sensitivity": "high",
        "generation_hint": "必须由用户提供脱敏后的测试凭证或测试环境账号。",
        "validation_hint": "确认是否为测试环境账号，并在结果中只保留脱敏信息。",
        "example": "test_user / ********",
    },
    {
        "type": "api_key",
        "name": "api_access",
        "patterns": [
            r"(?:api key|api密钥|endpoint|webhook|第三方接口|外部API|调用接口)",
        ],
        "source": "user",
        "supports_auto_generation": False,
        "blocking": True,
        "sensitivity": "high",
        "generation_hint": "必须由用户提供测试环境 API key 或 mock endpoint。",
        "validation_hint": "优先使用测试 endpoint；没有 mock 或测试 endpoint 时不应直接执行正式环境请求。",
        "example": "https://sandbox.example/api",
    },
    {
        "type": "database",
        "name": "database_connection",
        "patterns": [
            r"(?:数据库|数据库连接|db 连接|database|mysql|postgresql|mongodb|sqlite|redis|mongo|sql)",
        ],
        "source": "user",
        "supports_auto_generation": False,
        "blocking": True,
        "sensitivity": "normal",
        "generation_hint": "必须提供测试数据库连接或本地 SQLite 路径。",
        "validation_hint": "验证连接是否可达，确认是否为测试/沙箱环境。",
        "example": "mysql://user:pass@localhost:3306/test_db",
    },
    {
        "type": "environment",
        "name": "runtime_environment",
        "patterns": [
            r"(?:python|node|npm|pip|brew|apt|依赖安装|运行环境|安装|环境配置|python3|依赖)",
        ],
        "source": "user",
        "supports_auto_generation": False,
        "blocking": False,
        "sensitivity": "normal",
        "generation_hint": "记录运行脚本所需的最低环境。",
        "validation_hint": "记录依赖是否存在、版本是否可用、运行环境是否满足脚本要求。",
        "example": "python3 --version",
    },
    {
        "type": "browser",
        "name": "browser_config",
        "patterns": [
            r"(?:chrome|chromium|firefox|safari|浏览器|playwright|selenium|远程调试|debug port|9222)",
        ],
        "source": "user",
        "supports_auto_generation": False,
        "blocking": True,
        "sensitivity": "normal",
        "generation_hint": "若 skill 依赖浏览器自动化，需要确认浏览器安装情况。",
        "validation_hint": "检测浏览器是否存在、远程调试端口是否开放。",
        "example": "chrome --remote-debugging-port=9222",
    },
    {
        "type": "account",
        "name": "external_account",
        "patterns": [
            r"(?:支付宝|微信|飞书|钉钉|商家中心|后台管理|后台|控制台)",
        ],
        "source": "user",
        "supports_auto_generation": False,
        "blocking": True,
        "sensitivity": "high",
        "generation_hint": "必须提供测试账号或 sandbox 环境。",
        "validation_hint": "避免直接使用正式业务账号；报告中只记录账号类型和是否可用。",
        "example": "sandbox merchant account",
    },
    {
        "type": "user_confirmation",
        "name": "user_confirmation",
        "patterns": [
            r"(?:请确认|二次确认|需求再确认|询问用户|让用户|请告诉我|请选择|是否导出|是否保存)",
        ],
        "source": "user",
        "supports_auto_generation": False,
        "blocking": False,
        "sensitivity": "normal",
        "generation_hint": "允许评测器使用保守默认值，但必须在报告里标记采用了默认值。",
        "validation_hint": "记录哪些问题获得了真实回复，哪些问题使用了默认假设。",
        "example": "默认导出报告，不保存经验",
    },
    {
        "type": "business_context",
        "name": "business_context",
        "patterns": [
            r"(?:业务问题|分析目标|需求|主问题|指标口径|业务背景|结论口径)",
        ],
        "source": "user",
        "supports_auto_generation": False,
        "blocking": False,
        "sensitivity": "normal",
        "generation_hint": "若用户未提供，允许评测器使用通用评测问题，但可信度应降低。",
        "validation_hint": "优先收集真实业务问题；没有时要显式写明采用通用默认任务。",
        "example": "分析近 30 天销量变化及异常原因",
    },
]


RESOURCE_SECTION_HINTS = (
    "required resources", "prerequisites", "requirements", "inputs", "input",
    "setup", "environment", "配置", "前置条件", "资源", "输入", "用户需提供",
    "需要提供", "环境准备", "账号准备",
)

LOW_SIGNAL_SECTION_HINTS = (
    "evaluation contract", "evaluation notes", "评测原则", "评测合同",
    "常见资源类型", "资源检测", "报告", "blind vote", "评分",
)

EXECUTION_DEPENDENCY_HINTS = (
    "需要", "需提供", "必须提供", "请提供", "用户提供", "输入", "上传",
    "读取", "导入", "连接", "登录", "配置", "设置", "传入", "环境变量",
    "requires", "required", "provide", "upload", "connect",
    "login", "configure", "set", "env", "environment variable",
)

LOW_SIGNAL_HINTS = (
    "例如", "示例", "常见", "支持", "可识别", "可校验", "包括", "类型包括",
    "报告中", "展示", "评分", "评委", "validator", "preflight", "资源类型",
    "验证", "检测", "校验", "标识",
)


def _strip_frontmatter(content: str) -> str:
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)


def _current_section(lines: list[str], index: int) -> str:
    for i in range(index, -1, -1):
        line = lines[i].strip()
        if line.startswith("#"):
            return line.lstrip("#").strip().lower()
    return ""


def _line_window(lines: list[str], index: int) -> str:
    start = max(0, index - 1)
    end = min(len(lines), index + 2)
    return " ".join(line.strip() for line in lines[start:end] if line.strip())


def _is_markdown_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def _score_resource_evidence(line: str, section: str) -> tuple[float, str]:
    text = f"{section} {line}".lower()
    score = 0.35
    reasons: list[str] = ["keyword_match"]

    if any(hint.lower() in text for hint in EXECUTION_DEPENDENCY_HINTS):
        score += 0.35
        reasons.append("execution_dependency_context")
    if any(hint.lower() in section for hint in RESOURCE_SECTION_HINTS):
        score += 0.25
        reasons.append("resource_section")
    if re.search(r"(?:--[\w-]+|[A-Z][A-Z0-9_]{2,}|https?://|/\w+|\w+\.(?:csv|xlsx?|json|pdf|docx?|sql))", line):
        score += 0.15
        reasons.append("concrete_value_or_parameter")

    if _is_markdown_table_line(line):
        score -= 0.6
        reasons.append("markdown_table")
    if any(hint.lower() in text for hint in LOW_SIGNAL_HINTS):
        score -= 0.35
        reasons.append("descriptive_or_example_context")
    if any(hint.lower() in section for hint in LOW_SIGNAL_SECTION_HINTS):
        score -= 0.45
        reasons.append("low_signal_section")

    return max(0.0, min(1.0, score)), ",".join(reasons)


def _resource_threshold(resource_type: str, default_blocking: bool) -> float:
    if resource_type in {"credential", "api_key", "database", "browser", "account"}:
        return 0.62
    if resource_type in {"file", "user_confirmation", "business_context"}:
        return 0.58
    if default_blocking:
        return 0.55
    return 0.55


def _extract_declared_resources(metadata: dict[str, Any]) -> list[SkillResource]:
    declared = metadata.get("required_resources")
    if not isinstance(declared, list):
        return []

    resources: list[SkillResource] = []
    for raw in declared:
        if not isinstance(raw, dict):
            continue
        resource_type = str(raw.get("type") or "").strip()
        if not resource_type:
            continue
        resources.append(SkillResource(
            name=str(raw.get("name") or resource_type),
            type=resource_type,
            description=str(raw.get("description") or raw.get("name") or resource_type),
            source=str(raw.get("source") or "frontmatter"),
            required=bool(raw.get("required", True)),
            blocking=bool(raw.get("blocking", raw.get("required", True))),
            supports_auto_generation=bool(raw.get("supports_auto_generation", False)),
            sensitivity=str(raw.get("sensitivity") or "normal"),
            generation_hint=str(raw.get("generation_hint") or ""),
            validation_hint=str(raw.get("validation_hint") or ""),
            example=str(raw.get("example") or ""),
            confidence=1.0,
            evidence="declared in SKILL.md frontmatter",
            detection_reason="frontmatter_required_resources",
        ))
    return resources


def detect_resources(content: str, metadata: dict[str, Any]) -> list[SkillResource]:
    declared_resources = _extract_declared_resources(metadata)
    if declared_resources:
        return declared_resources

    resources: list[SkillResource] = []
    seen_types: set[str] = set()
    body = _strip_frontmatter(content)
    lines = body.splitlines()

    for rule in RESOURCE_RULES:
        resource_type = rule["type"]
        if resource_type in seen_types:
            continue

        best: tuple[float, str, str] | None = None
        in_code_block = False
        for index, line in enumerate(lines):
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block or _is_markdown_table_line(line):
                continue
            if not line.strip():
                continue
            if not any(re.search(p, line, re.IGNORECASE) for p in rule["patterns"]):
                continue
            section = _current_section(lines, index)
            evidence = _line_window(lines, index)
            score, reason = _score_resource_evidence(evidence, section)
            if best is None or score > best[0]:
                best = (score, evidence, reason)

        if best is None:
            continue
        confidence, evidence, reason = best
        threshold = _resource_threshold(resource_type, bool(rule.get("blocking", True)))
        if confidence < threshold:
            continue

        seen_types.add(resource_type)
        description = evidence.strip() if evidence else rule["name"]
        if len(description) > 120:
            description = description[:117] + "..."

        resources.append(SkillResource(
            name=rule["name"],
            type=resource_type,
            description=description,
            source=rule.get("source", "user"),
            required=True,
            blocking=rule.get("blocking", True),
            supports_auto_generation=rule.get("supports_auto_generation", False),
            sensitivity=rule.get("sensitivity", "normal"),
            generation_hint=rule.get("generation_hint", ""),
            validation_hint=rule.get("validation_hint", ""),
            example=rule.get("example", ""),
            confidence=round(confidence, 2),
            evidence=evidence[:240],
            detection_reason=reason,
        ))

    return resources


# ========== 内容提取 ==========

def extract_skill_metadata(skill_md: Path) -> dict[str, Any]:
    content = skill_md.read_text(encoding="utf-8", errors="replace")
    metadata: dict[str, Any] = {"name": "", "description": "", "license": "", "compatibility": "", "metadata": {}}
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not frontmatter_match:
        return metadata
    fm_text = frontmatter_match.group(1)
    try:
        import yaml
        parsed = yaml.safe_load(fm_text)
        if isinstance(parsed, dict):
            metadata.update(parsed)
            if not isinstance(metadata.get("metadata"), dict):
                metadata["metadata"] = {}
            return metadata
    except Exception:
        pass

    for line in fm_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":", 1)
        if len(parts) == 2:
            key = parts[0].strip()
            val = parts[1].strip().strip('"').strip("'")
            if key == "metadata":
                metadata["metadata"] = {}
            elif key.startswith("  ") and "metadata" in metadata:
                sub_key = key.strip().split(":", 1)[0].strip()
                sub_val = key.strip().split(":", 1)[1].strip().strip('"').strip("'") if ":" in key else ""
                metadata["metadata"][sub_key] = sub_val
            else:
                metadata[key] = val
    return metadata


def detect_execution_mode(content: str) -> str:
    interactive_keywords = ["确认", "询问", "选择", "请告诉我", "用户输入", "交互式", "等待用户", "让用户"]
    automated_keywords = ["自动执行", "无需确认", "全自动", "无需人工", "自动完成"]
    has_interactive = any(kw in content for kw in interactive_keywords)
    has_automated = any(kw in content for kw in automated_keywords)
    if has_automated and not has_interactive:
        return "automated"
    if has_interactive:
        return "interactive"
    if any(kw in content.lower() for kw in ["python3", "bash", "run", "execute", "脚本", "命令"]):
        return "hybrid"
    return "interactive"


def extract_core_actions(content: str) -> list[str]:
    actions = []
    # 提取 Step / Phase 格式
    step_matches = re.findall(r"(?:第[一二三四五六七八九十\d]+步|Step\s*\d+|Phase\s*\d+)[：:\-\s]*(.+?)(?=\n|##|$)", content)
    actions.extend([s.strip() for s in step_matches if s.strip() and not s.strip().startswith(("##", "###", "name:", "description:", "你是"))])

    # 提取动词开头的句子
    action_verbs = ["校验", "验证", "检查", "分析", "生成", "创建", "填写", "提交", "获取", "下载", "上传", "发送", "调用", "执行", "运行", "发布", "配置", "设置", "导出", "确认"]
    skip_prefixes = ("name:", "description:", "你是", "---", "（", "📊", "###", "##", "# ", "使用 Bash", "在命令提示符", "python3", "pip install", "export", "npm install")
    for verb in action_verbs:
        matches = re.findall(rf"[^。\n]*{verb}[^。\n]+", content)
        for item in matches[:2]:
            cleaned = item.strip().lstrip("> ").lstrip("- ").lstrip("1. ").lstrip("2. ").lstrip("3. ").lstrip("4. ").lstrip("5. ")
            if cleaned and 5 < len(cleaned) < 100 and not any(cleaned.startswith(prefix) for prefix in skip_prefixes):
                actions.append(cleaned)

    # 去重
    unique_actions: list[str] = []
    seen = set()
    for action in actions:
        normalized = action[:60]
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_actions.append(action)
    return unique_actions[:10]


def infer_output_type(content: str) -> str:
    output_indicators = {
        "excel_report": [r"比对报告", r"Excel", r"xlsx", r"表格", r"导出"],
        "api_response": [r"支付链接", r"响应", r"返回", r"API"],
        "database_changes": [r"发布", r"上架", r"创建完成", r"插入", r"更新"],
        "file_generation": [r"生成.*文件", r"保存到", r"输出"],
        "ui_changes": [r"界面", r"页面", r"UI", r"点击", r"填写"],
    }
    for output_type, patterns in output_indicators.items():
        for pattern in patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return output_type
    return "unknown"


def infer_output_formats(content: str) -> list[str]:
    """从 SKILL.md 中提取提到的输出文件格式。"""
    formats = []
    format_patterns = [
        (r"\.(html?|htm)\b", ".html"),
        (r"\.md\b", ".md"),
        (r"\.json\b", ".json"),
        (r"\.xlsx?\b", ".xlsx"),
        (r"\.csv\b", ".csv"),
        (r"\.png\b", ".png"),
        (r"\.pdf\b", ".pdf"),
        (r"\.sql\b", ".sql"),
        (r"\.tsx?\b", ".tsx"),
        (r"\.vue\b", ".vue"),
        (r"\.svelte\b", ".svelte"),
    ]
    for pattern, fmt in format_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            formats.append(fmt)
    return formats[:5]


def build_evaluation_notes(
    execution_mode: str,
    resources: list[SkillResource],
) -> list[str]:
    notes: list[str] = []
    if any(resource.type == "user_confirmation" for resource in resources):
        notes.append("此 skill 含有用户确认步骤，评测时应记录真实回复与默认假设的边界。")
    if any(resource.type in {"credential", "api_key", "database", "account"} for resource in resources):
        notes.append("此 skill 依赖外部系统或敏感资源，若未提供测试环境，评测结果不应被视为可信。")
    if any(resource.supports_auto_generation for resource in resources):
        notes.append('若使用了合成测试数据，报告中应显式标记"mock/synthetic"来源。')
    if execution_mode == "interactive":
        notes.append("交互式 skill 需要额外关注卡在等待用户输入的情况。")
    return notes


def _default_assumptions_for_mode(execution_mode: str, resources: list[SkillResource]) -> list[str]:
    assumptions: list[str] = []
    if execution_mode in {"interactive", "hybrid"}:
        assumptions.append("如遇用户确认步骤，允许使用保守默认值，但必须在产物中显式记录。")
    if any(resource.type == "file" and resource.supports_auto_generation for resource in resources):
        assumptions.append("若缺少真实样本，可退化为 synthetic/mock 数据，但必须标记数据来源。")
    if any(resource.blocking and not resource.value for resource in resources):
        assumptions.append("若依赖环境未验证成功，只能做保守尝试，不能假装脚本已执行通过。")
    return assumptions


# ========== 评测合同（不再依赖类别） ==========

def _build_task_outline(capability: SkillCapability) -> str:
    task_outline_parts = []
    if capability.summary:
        task_outline_parts.append(capability.summary[:200])
    if capability.core_actions:
        task_outline_parts.append(f"关键步骤：{', '.join(capability.core_actions[:4])}")
    if capability.output_type and capability.output_type != "unknown":
        task_outline_parts.append(f"输出形式：{capability.output_type}")

    return "；".join(task_outline_parts) if task_outline_parts else (
        "使用被测 skill 完成其设计目标，保留关键步骤和结果证据。"
    )

def _build_without_skill_task() -> str:
    return (
        "不使用待评测 skill，直接完成同等用户目标。"
        "你不需要复刻被测 skill 的内部步骤，但必须交付与 with-skill 组可比较的最终结果。"
    )

def _build_success_criteria(capability: SkillCapability, resources: list[SkillResource]) -> list[str]:
    success_criteria = [
        "最终明确说明任务是否完成。",
        "关键产物已保存到 artifacts 目录。",
        "with-skill 结果应保留足够证据，证明最终结果并非完全脱离 skill 自由发挥。",
    ]
    if capability.core_actions:
        success_criteria.append(
            f"with-skill 结果最好体现这些关键动作中的主要部分：{', '.join(capability.core_actions[:4])}"
        )
    if any(resource.type == "user_confirmation" for resource in resources):
        success_criteria.append("如果存在用户确认步骤，必须记录真实回复或默认假设。")
    if any(resource.type == "business_context" for resource in resources):
        success_criteria.append("最终产物必须回应本次任务的主问题，而不是泛泛输出。")
    return success_criteria[:5]

def _build_expected_artifacts(capability: SkillCapability) -> list[dict[str, Any]]:
    expected_artifacts = []
    output_formats = capability.output_formats
    if output_formats:
        expected_artifacts.append({
            "name": "main_output",
            "description": "skill 主要产出物",
            "required": True,
            "extensions": output_formats,
            "keywords": ["output", "产出", "结果"],
        })
    expected_artifacts.append({
        "name": "summary_note",
        "description": "任务摘要",
        "required": True,
        "extensions": [".md"],
        "keywords": ["summary"],
    })
    if capability.has_interactive_steps:
        expected_artifacts.append({
            "name": "execution_evidence",
            "description": "执行证据或关键步骤留痕",
            "required": False,
            "extensions": [".md", ".json", ".txt"],
            "keywords": ["trace", "checkpoint", "步骤", "假设", "assumption"],
        })
    return expected_artifacts

def _build_verification_checks(execution_mode: str) -> list[str]:
    verification_checks = [
        "检查 artifacts 中是否存在合同要求的关键产物。",
        "检查 transcript/stdout 中是否有任务完成证据。",
        "检查 with-skill 结果中是否存在 skill 被实际执行的证据。",
        "检查是否至少保留了一份可复核的执行留痕、假设说明或检查点记录。",
    ]
    if execution_mode in {"interactive", "hybrid"}:
        verification_checks.append("检查是否把用户确认步骤记录为真实输入或默认假设。")
    return verification_checks

def _build_validation_assertions(capability: SkillCapability, resources: list[SkillResource]) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = [
        {
            "name": "completion_signal",
            "type": "script_eval",
            "kind": "completion_signal",
            "required": True,
            "weight": 20,
            "target": "output",
            "parameters": {"script_name": "completion_signal"},
            "description": "需要存在明确任务完成信号。",
        },
        {
            "name": "summary_exists",
            "type": "script_eval",
            "kind": "artifact_path_exists",
            "required": True,
            "weight": 15,
            "target": "summary.md",
            "path": "summary.md",
            "parameters": {"script_name": "artifact_path_exists", "path": "summary.md"},
            "description": "artifacts 目录中应包含 summary.md。",
        },
    ]

    if capability.core_actions:
        assertions.append({
            "name": "core_actions_executed",
            "type": "contains_facts",
            "kind": "content_keywords",
            "required": True,
            "weight": 20,
            "target": "output",
            "keywords": [a[:15] for a in capability.core_actions[:4] if len(a) >= 3],
            "parameters": {
                "keywords": [a[:15] for a in capability.core_actions[:4] if len(a) >= 3],
                "min_keywords": 1,
            },
            "description": f"应体现 skill 核心动作的执行痕迹。",
        })

    output_formats = capability.output_formats
    if output_formats:
        assertions.append({
            "name": "expected_output_formats",
            "type": "script_eval",
            "kind": "artifact_extensions",
            "required": True,
            "weight": 15,
            "target": "output",
            "extensions": output_formats,
            "parameters": {
                "script_name": "artifact_extensions",
                "extensions": output_formats,
            },
            "description": f"应产出 skill 设计要求的文件类型。",
        })

    assertions.append({
        "name": "execution_trace",
        "type": "contains_facts",
        "kind": "content_keywords",
        "required": False,
        "weight": 10,
        "target": "output",
        "keywords": ["trace", "checkpoint", "步骤", "假设", "assumption"],
        "parameters": {
            "keywords": ["trace", "checkpoint", "步骤", "假设", "assumption"],
            "min_keywords": 1,
        },
        "description": "至少保留一部分执行留痕或假设说明。",
    })

    if any(resource.type == "business_context" for resource in resources):
        assertions.append({
            "name": "business_question_response",
            "type": "contains_facts",
            "kind": "content_keywords",
            "required": False,
            "weight": 10,
            "target": "output",
            "keywords": ["主问题", "结论", "问题", "建议", "分析目标"],
            "parameters": {
                "keywords": ["主问题", "结论", "问题", "建议", "分析目标"],
                "min_keywords": 1,
            },
            "description": "最终产物应回应主问题或业务目标。",
        })

    if any(ext in [".csv", ".xlsx", ".json"] for ext in output_formats):
        assertions.append({
            "name": "structured_data_integrity",
            "type": "script_eval",
            "kind": "csv_well_formed",
            "required": False,
            "weight": 10,
            "target": "output",
            "parameters": {"script_name": "csv_well_formed"},
            "description": "数据文件需结构完整且行列对齐。",
        })

    return assertions[:8]


def build_evaluation_contract(
    skill_name: str,
    execution_mode: str,
    capability: SkillCapability,
    resources: list[SkillResource],
) -> dict[str, Any]:
    """从 skill 内容直接构建评测合同。

    不再依赖关键词猜测类别，而是从 core_actions / output_type / resources 中提取。
    """
    return {
        "version": "dynamic-contract-v4",
        "skill_name": skill_name,
        "primary_goal": capability.summary or f"使用 {skill_name} 完成其设计目标",
        "task_outline": _build_task_outline(capability),
        "without_skill_task": _build_without_skill_task(),
        "success_criteria": _build_success_criteria(capability, resources),
        "expected_artifacts": _build_expected_artifacts(capability),
        "verification_checks": _build_verification_checks(execution_mode),
        "allowed_assumptions": _default_assumptions_for_mode(execution_mode, resources),
        "evaluation_focus": capability.core_actions[:4] if capability.core_actions else [],
        "validation_assertions": _build_validation_assertions(capability, resources),
    }


# ========== 评分规范（不再依赖类别） ==========

def analyze_skill(skill_path: str) -> SkillAnalysis:
    skill_dir = Path(skill_path)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found in {skill_path}")

    content = skill_md.read_text(encoding="utf-8", errors="replace")
    metadata = extract_skill_metadata(skill_md)
    required_resources = detect_resources(content, metadata)
    execution_mode = detect_execution_mode(content)
    core_actions = extract_core_actions(content)
    output_type = infer_output_type(content)
    output_formats = infer_output_formats(content)
    has_interactive = bool(re.search(r"(?:请确认|询问用户|是否|请选择|确认|让用户)", content))

    capability = SkillCapability(
        summary=str(metadata.get("description", "")),
        core_actions=core_actions,
        output_type=output_type,
        output_formats=output_formats,
        has_interactive_steps=has_interactive,
    )

    # 不再依赖类别，用 skill 自身描述估算任务类型
    estimated_task = (
        f"执行 {metadata.get('name', skill_dir.name)} 的完整流程："
        + "、".join(core_actions[:3]) if core_actions
        else f"执行 {metadata.get('name', skill_dir.name)} 的完整流程"
    )

    evaluation_contract = build_evaluation_contract(
        skill_name=str(metadata.get("name", skill_dir.name)),
        execution_mode=execution_mode,
        capability=capability,
        resources=required_resources,
    )

    return SkillAnalysis(
        skill_name=str(metadata.get("name", skill_dir.name)),
        skill_path=skill_path,
        capability=capability,
        required_resources=required_resources,
        execution_mode=execution_mode,
        estimated_task_type=estimated_task,
        tools_required=[],
        triggers=[],
        evaluation_notes=build_evaluation_notes(execution_mode, required_resources),
        evaluation_contract=evaluation_contract,
    )
