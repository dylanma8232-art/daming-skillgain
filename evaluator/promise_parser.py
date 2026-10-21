from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# Precompiled regular expressions for performance optimization
_FILE_PATTERNS = [
    re.compile(r'(?:生成|创建|产出|导出|保存|输出|编写|提交).*?([`\'"]?)([a-zA-Z0-9_\-]+\.[a-zA-Z0-9]+)\1'),
    re.compile(r'([a-zA-Z0-9_\-]+\.(?:md|json|csv|xlsx|xls|txt|html|sql|py|js|ts|png|jpg))\s*(?:文件|报告|文档|报表|结果|数据|输出)'),
]

_BLOCK_PATTERNS = [
    re.compile(r'(?:生成|创建|产出|导出|保存|输出|编写|提交).*?([a-zA-Z0-9_\-]+\.[a-zA-Z0-9]+)[，,，。:：\s]*([\s\S]{0,500})'),
]

_STEP_PATTERNS = [
    re.compile(r'(?:第[一二三四五六七八九十\d]+步|Step\s*\d+|Phase\s*(\d+))[：:\-\s]+([^\n]{5,120})'),
    re.compile(r'(?:步骤|阶段)[一二三四五六七八九十\d]*[：:\-\s]+([^\n]{5,120})'),
]

_CONSTRAINT_PATTERNS = [
    re.compile(r"(?:必须|禁止|不得|不能|确保|避免|限制).*?([^\n。]{5,100})"),
]

# Patterns for sections and behaviors that previously were compiled dynamically in loops
_FILE_SECTION_KEYWORDS_1 = ["执行摘要", "关键发现", "风险提示", "行动建议", "结论", "建议", "摘要", "发现", "风险", "趋势", "异常", "指标"]
_FILE_SECTION_KEYWORDS_2 = ["执行摘要", "关键发现", "风险提示", "行动建议", "数据质量", "数据校验", "趋势分析", "异常检测", "归因分析", "洞察报告"]

_FILE_SECTION_PATTERNS_1 = {kw: re.compile(rf'{kw}(.{{0,40}})?(?:\n|$)') for kw in _FILE_SECTION_KEYWORDS_1}

_ACTION_VERBS = ["校验", "验证", "检查", "分析", "比对", "检测", "识别", "提取", "计算", "评估", "统计", "汇总"]
_ACTION_VERB_PATTERNS = [re.compile(rf"{verb}.*?([^\n]{{5,100}})") for verb in _ACTION_VERBS]

# Common regex patterns used repeatedly
_RE_NAME = re.compile(r'^name:\s*(.+)$', re.MULTILINE)
_RE_ITEMS_MIN = re.compile(r'(≥|>=|至少|最少|不少于|不低于)\s*(\d+)')
_RE_LENGTH_MAX = re.compile(r'(≤|<=|不超过|最多|以内|以下)\s*(\d+)')
_RE_VALID_FILENAME = re.compile(r'^[a-zA-Z0-9_\-]+\.[a-zA-Z0-9]+$')

_RE_LINE_NAME = re.compile(r'([（(【\[<「]\s*([\u4e00-\u9fff]{2,10})\s*[）)】\]>」])')
_RE_LINE_ITEMS_MIN = re.compile(r'(≥|>=|至少|最少|不少于|不低于)\s*(\d+)\s*(?:条|个|项|点|rows|items|points)')
_RE_LINE_ITEMS_MAX = re.compile(r'(≤|<=|最多|不超过|至多|不大于)\s*(\d+)\s*(?:条|个|项|点|rows|items|points)')
_RE_LINE_ITEMS_EXACT = re.compile(r'(?:恰好|正好|=)\s*(\d+)\s*(?:条|个|项|点|rows|items|points)')
_RE_LINE_LENGTH_MAX = re.compile(r'(≤|<=|不超过|最多|以内|以下|之内)\s*(\d+)\s*(?:字|字符|words|chars|characters|行|lines)')
_RE_LINE_LENGTH_MIN = re.compile(r'(≥|>=|至少|不少于|以上)\s*(\d+)\s*(?:字|字符|words|chars|characters|行|lines)')

_RE_BEHAVIOR_KW_ZH = re.compile(r"[\u4e00-\u9fff]{2,6}")
_RE_BEHAVIOR_KW_EN = re.compile(r"[a-zA-Z]{3,20}")


@dataclass
class SectionConstraint:
    name: str
    min_items: int | None = None
    max_items: int | None = None
    exact_items: int | None = None
    min_length: int | None = None
    max_length: int | None = None
    must_exist: bool = False


@dataclass
class FilePromise:
    filename: str
    file_type: str
    sections: list[SectionConstraint] = field(default_factory=list)
    required: bool = True
    context: str = ""


@dataclass
class StepPromise:
    name: str
    phase_number: int | None = None
    must_complete: bool = True
    dependencies: list[str] = field(default_factory=list)
    context: str = ""


@dataclass
class BehaviorPromise:
    name: str
    keywords: list[str] = field(default_factory=list)
    must_perform: bool = True
    context: str = ""


@dataclass
class ConstraintPromise:
    text: str
    type: str = ""
    context: str = ""


@dataclass
class StructuredPromises:
    skill_name: str = ""
    files: list[FilePromise] = field(default_factory=list)
    steps: list[StepPromise] = field(default_factory=list)
    behaviors: list[BehaviorPromise] = field(default_factory=list)
    constraints: list[ConstraintPromise] = field(default_factory=list)


def parse_skill_structured_promises(skill_md_path: str) -> StructuredPromises:
    path = Path(skill_md_path)
    if not path.exists():
        return StructuredPromises()

    content = path.read_text(encoding="utf-8", errors="replace")
    skill_name = _extract_name(content)

    promises = StructuredPromises(skill_name=skill_name)
    promises.files = _extract_file_promises(content)
    promises.steps = _extract_step_promises(content)
    promises.behaviors = _extract_behavior_promises(content)
    promises.constraints = _extract_constraint_promises(content)

    return promises


def _extract_name(content: str) -> str:
    match = _RE_NAME.search(content)
    return match.group(1).strip() if match else "unknown"


def _extract_file_promises(content: str) -> list[FilePromise]:
    files = []
    seen_filenames = set()

    for pattern in _FILE_PATTERNS:
        for match in pattern.finditer(content):
            filename = match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(1)
            if not filename or filename in seen_filenames or len(filename) > 80 or not any(c.isalnum() for c in filename):
                continue
            if any(filename.startswith(p) for p in ("-", "_", ".", " ", "#")):
                continue
            seen_filenames.add(filename)
            file_type = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ""

            context_start = max(0, match.start() - 50)
            context_end = min(len(content), match.end() + 200)
            context = content[context_start:context_end].strip()

            sections = _parse_file_sections_from_context(content, match, filename)

            files.append(FilePromise(
                filename=filename,
                file_type=file_type,
                sections=sections,
                required=True,
                context=context[:200],
            ))

    for filename, sections in _extract_section_constraints(content).items():
        if not _RE_VALID_FILENAME.match(filename):
            continue
        if filename not in seen_filenames:
            seen_filenames.add(filename)
            file_type = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ""
            files.append(FilePromise(
                filename=filename,
                file_type=file_type,
                sections=sections,
                required=True,
            ))
        elif files:
            for existing in files:
                if existing.filename == filename and sections:
                    existing.sections.extend(s for s in sections if s.name not in [x.name for x in existing.sections])
                    break

    return files


def _parse_file_sections_from_context(content: str, match: re.Match, filename: str) -> list[SectionConstraint]:
    context_start = max(0, match.end())
    context_end = min(len(content), context_start + 500)
    context = content[context_start:context_end]

    sections = []

    for kw, pattern in _FILE_SECTION_PATTERNS_1.items():
        kw_match = pattern.search(context)
        if kw_match:
            constraint_text = kw_match.group(1) or ""
            if len(constraint_text) > 35:
                constraint_text = constraint_text[:35]
            section = SectionConstraint(name=kw, must_exist=True)

            items_match = _RE_ITEMS_MIN.search(constraint_text)
            if items_match:
                section.min_items = int(items_match.group(2))

            length_match = _RE_LENGTH_MAX.search(constraint_text)
            if length_match:
                section.max_length = int(length_match.group(2))

            sections.append(section)

    return sections[:8]


def _parse_section_constraint_from_line(line: str) -> list[SectionConstraint]:
    sections = []

    name_match = _RE_LINE_NAME.search(line)
    if name_match:
        name = name_match.group(2).strip()
        constraint = SectionConstraint(name=name)

        items_match = _RE_LINE_ITEMS_MIN.search(line)
        if items_match:
            constraint.min_items = int(items_match.group(2))

        items_match2 = _RE_LINE_ITEMS_MAX.search(line)
        if items_match2:
            constraint.max_items = int(items_match2.group(2))

        items_match3 = _RE_LINE_ITEMS_EXACT.search(line)
        if items_match3:
            constraint.exact_items = int(items_match3.group(1))

        length_match = _RE_LINE_LENGTH_MAX.search(line)
        if length_match:
            constraint.max_length = int(length_match.group(2))

        length_match2 = _RE_LINE_LENGTH_MIN.search(line)
        if length_match2:
            constraint.min_length = int(length_match2.group(2))

        constraint.must_exist = True
        sections.append(constraint)

    return sections


def _extract_section_constraints(content: str) -> dict[str, list[SectionConstraint]]:
    file_sections: dict[str, list[SectionConstraint]] = {}

    for pattern in _BLOCK_PATTERNS:
        for match in pattern.finditer(content):
            filename = match.group(1)
            block = match.group(0)
            if filename not in file_sections:
                file_sections[filename] = []

            found_names = []
            for kw in _FILE_SECTION_KEYWORDS_2:
                if kw in block and kw not in found_names:
                    is_substring = any(kw in other for other in found_names if other != kw)
                    if is_substring:
                        continue

                    has_longer = any(other in kw for other in found_names)
                    if has_longer:
                        found_names = [other for other in found_names if other not in kw]
                        file_sections[filename] = [s for s in file_sections[filename] if s.name not in [other for other in found_names if other in kw]]

                    kw_pos = block.find(kw)
                    kw_context = block[kw_pos:kw_pos+50]
                    section = SectionConstraint(name=kw, must_exist=True)

                    items_match = _RE_ITEMS_MIN.search(kw_context)
                    if items_match:
                        section.min_items = int(items_match.group(2))
                    length_match = _RE_LENGTH_MAX.search(kw_context)
                    if length_match:
                        section.max_length = int(length_match.group(2))

                    file_sections[filename].append(section)
                    found_names.append(kw)

    for filename, sections in file_sections.items():
        file_sections[filename] = list({s.name: s for s in sections}.values())[:8]

    return file_sections


def _extract_step_promises(content: str) -> list[StepPromise]:
    steps = []
    for pattern in _STEP_PATTERNS:
        for match in pattern.finditer(content):
            phase_num = None
            if match.lastindex and match.lastindex >= 1:
                try:
                    val = match.group(1)
                    if val and val.isdigit():
                        phase_num = int(val)
                except (ValueError, IndexError):
                    pass
            name_idx = 2 if match.lastindex and match.lastindex >= 2 else 1
            try:
                name = match.group(name_idx).strip()
            except IndexError:
                name = match.group(0).strip()
            if 5 <= len(name) <= 120 and not name.startswith(("##", "###", "#")):
                steps.append(StepPromise(
                    name=name,
                    phase_number=phase_num,
                    must_complete=True,
                ))
    return steps


def _extract_behavior_promises(content: str) -> list[BehaviorPromise]:
    behaviors = []
    for pattern in _ACTION_VERB_PATTERNS:
        for match in pattern.finditer(content):
            text = match.group(0).strip()
            if 8 <= len(text) <= 120 and not any(text.startswith(p) for p in ("##", "###", "# ", "name:", "description:")):
                keywords = _RE_BEHAVIOR_KW_ZH.findall(text)[:6]
                keywords.extend([t.lower() for t in _RE_BEHAVIOR_KW_EN.findall(text)[:6]])
                behaviors.append(BehaviorPromise(
                    name=text[:80],
                    keywords=list(dict.fromkeys(keywords))[:10],
                    must_perform=True,
                ))
    return behaviors[:12]


def _extract_constraint_promises(content: str) -> list[ConstraintPromise]:
    constraints = []
    for pattern in _CONSTRAINT_PATTERNS:
        for match in pattern.finditer(content):
            text = match.group(0).strip()
            if 5 <= len(text) <= 150:
                constraints.append(ConstraintPromise(text=text[:100]))
    return constraints[:8]
