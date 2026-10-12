"""Subject Adapter - 被测对象（skill）适配层（P2 轨道核心）

统一契约四件事（方案 §7 P2 行的硬性要求）:
    ① 任务投递  (deliver_task):        prompt + 工作目录 → 宿主会话
    ② 按宿主规则注入 skill (inject_skill): 各宿主 skills 目录约定不同
    ③ 产物收集  (collect_artifacts):   约定产物目录 (workspace/artifacts/)
    ④ 加载验证  (verify_skill_loaded): 确认 skill 真被宿主加载，防假对照

支持的宿主注册表 SUBJECT_HOST_ADAPTERS:
    opencode     → .opencode/skills/<name>/  (workspace 级目录注入)
    claude-code  → .claude/skills/<name>/    (workspace 级目录注入)
    codex        → AGENTS.md 追加 skill 指引（AGENTS.md 约定）
    script       → .skills/<name>/           (script 宿主约定的 skills 目录)

新增宿主只需实现 BaseSubjectAdapter 并注册到 SUBJECT_HOST_ADAPTERS。
"""
from __future__ import annotations
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..io_utils import ensure_directory, write_text


class SubjectAdapterError(RuntimeError):
    """Subject Adapter 契约违规（注入失败 / 加载验证失败等）"""


def find_skill_root(skill_path: Path) -> Path:
    """定位 skill 根目录（含 SKILL.md 的目录，支持嵌套）"""
    skill_path = Path(skill_path)
    if (skill_path / "SKILL.md").exists():
        return skill_path
    for subdir in sorted(skill_path.iterdir()):
        if subdir.is_dir():
            if (subdir / "SKILL.md").exists():
                return subdir
            for sub2 in sorted(subdir.iterdir()):
                if sub2.is_dir() and (sub2 / "SKILL.md").exists():
                    return sub2
    return skill_path


def skill_name_from_root(skill_root: Path) -> str:
    """从 SKILL.md frontmatter 提取 skill 名，缺省用目录名"""
    skill_md = skill_root / "SKILL.md"
    if skill_md.exists():
        try:
            for line in skill_md.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("name:"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return skill_root.name


class BaseSubjectAdapter(ABC):
    """被测对象适配层基类

    类属性:
        host_name: 宿主标识
        skill_injection: 该宿主的 skill 注入约定标识
        artifacts_convention: 约定产物目录（相对工作区）
    """

    host_name: str = "unknown"
    skill_injection: str = "none"
    artifacts_convention: str = "artifacts"

    # ---- ① 任务投递 -------------------------------------------------------
    def deliver_task(
        self,
        executor,
        *,
        model: str,
        prompt: str,
        workspace_dir: Path,
        timeout_seconds: int = 600,
        **kwargs,
    ):
        """任务投递：prompt + 工作目录 → 宿主会话（经 executor 统一契约）"""
        executor.require_capability("file_ops", purpose=f"Subject Adapter 任务投递 ({self.host_name})")
        return executor.execute_session(
            model=model,
            prompt=prompt,
            cwd=workspace_dir,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )

    # ---- ② 按宿主规则注入 skill -------------------------------------------
    @abstractmethod
    def inject_skill(self, skill_path: Path, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        """把被测 skill 按宿主约定注入工作区。

        Returns:
            {"skill_dir": 注入后的 skill 目录, "marker": 注入方式标识, ...}
        """

    # ---- ③ 产物收集 --------------------------------------------------------
    def collect_artifacts(self, workspace_dir: Path) -> list[str]:
        """收集约定产物目录下的全部产物（相对工作区路径，排序稳定）"""
        artifacts_dir = Path(workspace_dir) / self.artifacts_convention
        if not artifacts_dir.exists():
            return []
        return sorted(
            str(item.relative_to(workspace_dir))
            for item in artifacts_dir.rglob("*")
            if item.is_file()
        )

    # ---- ④ 加载验证 --------------------------------------------------------
    @abstractmethod
    def verify_skill_loaded(self, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        """验证 skill 已按宿主约定加载（防假对照）。

        Returns:
            {"loaded": bool, "evidence": {...}}
        """

    def cleanup_skill(self, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        """without-skill 隔离：确保工作区无任何该宿主 skills 目录残留"""
        removed = False
        for d in self.skill_dirs(workspace_dir):
            if d.exists():
                if d.is_symlink():
                    d.unlink()
                else:
                    shutil.rmtree(d, ignore_errors=True)
                removed = True
        return {"skill_dirs_absent": all(not d.exists() for d in self.skill_dirs(workspace_dir)), "removed": removed}

    def skill_dirs(self, workspace_dir: Path) -> list[Path]:
        """该宿主在工作区内可能的 skills 目录（cleanup 与隔离验证用）"""
        return []


class OpencodeSubjectAdapter(BaseSubjectAdapter):
    """OpenCode 宿主：.opencode/skills/<name>/ 目录注入 + 目录存在性 + opencode.json 权限放行"""

    host_name = "opencode"
    skill_injection = "workspace-skills-dir"
    artifacts_convention = "artifacts"

    def skill_dirs(self, workspace_dir: Path) -> list[Path]:
        return [Path(workspace_dir) / ".opencode" / "skills"]

    def inject_skill(self, skill_path: Path, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        skills_dir = Path(workspace_dir) / ".opencode" / "skills"
        target = skills_dir / skill_name
        ensure_directory(skills_dir)
        if target.exists():
            if target.is_symlink():
                target.unlink()
            else:
                shutil.rmtree(target)
        src = find_skill_root(Path(skill_path))
        shutil.copytree(
            src, target,
            ignore=shutil.ignore_patterns(".DS_Store", ".pytest_cache", "__pycache__", ".git"),
        )
        # opencode.json 权限放行（沿用主链历史约定）
        import json
        config_path = Path(workspace_dir) / "opencode.json"
        try:
            config = {}
            if config_path.exists():
                config = json.loads(config_path.read_text(encoding="utf-8"))
            permission = config.setdefault("permission", {}).setdefault("skill", {})
            permission[skill_name] = "allow"
            permission.setdefault("daming-skillgain", "deny")  # 评测器自身不可被被测宿主加载（防自评）
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return {"skill_dir": str(target), "marker": self.skill_injection, "skill_md": str(target / "SKILL.md")}

    def verify_skill_loaded(self, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        target = Path(workspace_dir) / ".opencode" / "skills" / skill_name
        skill_md = target / "SKILL.md"
        loaded = target.is_dir() and skill_md.exists()
        return {
            "loaded": loaded,
            "evidence": {
                "skill_dir_exists": target.is_dir(),
                "skill_md_exists": skill_md.exists(),
                "injection": self.skill_injection,
                "host": self.host_name,
            },
        }


class ClaudeCodeSubjectAdapter(OpencodeSubjectAdapter):
    """Claude Code 宿主：.claude/skills/<name>/ 目录注入（Claude Code 项目级 skills 约定）"""

    host_name = "claude-code"
    skill_injection = "claude-skills-dir"

    def skill_dirs(self, workspace_dir: Path) -> list[Path]:
        return [Path(workspace_dir) / ".claude" / "skills"]

    def inject_skill(self, skill_path: Path, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        skills_dir = Path(workspace_dir) / ".claude" / "skills"
        target = skills_dir / skill_name
        ensure_directory(skills_dir)
        if target.exists():
            shutil.rmtree(target)
        src = find_skill_root(Path(skill_path))
        shutil.copytree(
            src, target,
            ignore=shutil.ignore_patterns(".DS_Store", ".pytest_cache", "__pycache__", ".git"),
        )
        return {"skill_dir": str(target), "marker": self.skill_injection, "skill_md": str(target / "SKILL.md")}

    def verify_skill_loaded(self, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        target = Path(workspace_dir) / ".claude" / "skills" / skill_name
        skill_md = target / "SKILL.md"
        loaded = target.is_dir() and skill_md.exists()
        return {
            "loaded": loaded,
            "evidence": {
                "skill_dir_exists": target.is_dir(),
                "skill_md_exists": skill_md.exists(),
                "injection": self.skill_injection,
                "host": self.host_name,
            },
        }


class CodexSubjectAdapter(BaseSubjectAdapter):
    """Codex 宿主：AGENTS.md 追加 skill 指引（Codex 的项目级指令约定）"""

    host_name = "codex"
    skill_injection = "agents-md"
    artifacts_convention = "artifacts"

    AGENTS_BEGIN = "<!-- daming-skillgain:skill-injection:begin -->"
    AGENTS_END = "<!-- daming-skillgain:skill-injection:end -->"

    def skill_dirs(self, workspace_dir: Path) -> list[Path]:
        return []

    def inject_skill(self, skill_path: Path, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        workspace_dir = Path(workspace_dir)
        src = find_skill_root(Path(skill_path))
        skill_md = src / "SKILL.md"
        if not skill_md.exists():
            raise SubjectAdapterError(
                f"codex 宿主注入失败: skill 缺少 SKILL.md: {src}"
            )
        skill_content = skill_md.read_text(encoding="utf-8", errors="replace")
        section = (
            f"{self.AGENTS_BEGIN}\n"
            f"# Injected Skill: {skill_name}\n\n"
            "以下是被注入的被测 skill 的完整说明，任务执行时必须遵循它：\n\n"
            f"{skill_content}\n"
            f"{self.AGENTS_END}\n"
        )
        agents_md = workspace_dir / "AGENTS.md"
        existing = agents_md.read_text(encoding="utf-8") if agents_md.exists() else ""
        # 幂等：先移除旧注入段
        if self.AGENTS_BEGIN in existing and self.AGENTS_END in existing:
            pre = existing.split(self.AGENTS_BEGIN)[0]
            post = existing.split(self.AGENTS_END)[-1]
            existing = pre + post
        agents_md.write_text(existing.rstrip() + ("\n\n" if existing.strip() else "") + section, encoding="utf-8")
        # 同时在工作区保留 skill 源副本供追溯
        trace_dir = workspace_dir / ".daming-skillgain" / "skills" / skill_name
        if trace_dir.exists():
            shutil.rmtree(trace_dir)
        shutil.copytree(src, trace_dir, ignore=shutil.ignore_patterns(".DS_Store", "__pycache__", ".git"))
        return {"skill_dir": str(trace_dir), "marker": self.skill_injection, "agents_md": str(agents_md)}

    def verify_skill_loaded(self, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        agents_md = Path(workspace_dir) / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8", errors="replace") if agents_md.exists() else ""
        has_marker = self.AGENTS_BEGIN in content and self.AGENTS_END in content
        has_skill = f"# Injected Skill: {skill_name}" in content
        return {
            "loaded": has_marker and has_skill,
            "evidence": {
                "agents_md_exists": agents_md.exists(),
                "injection_marker_present": has_marker,
                "skill_section_present": has_skill,
                "injection": self.skill_injection,
                "host": self.host_name,
            },
        }

    def cleanup_skill(self, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        agents_md = Path(workspace_dir) / "AGENTS.md"
        removed = False
        if agents_md.exists():
            content = agents_md.read_text(encoding="utf-8")
            if self.AGENTS_BEGIN in content and self.AGENTS_END in content:
                pre = content.split(self.AGENTS_BEGIN)[0]
                post = content.split(self.AGENTS_END)[-1]
                agents_md.write_text((pre.rstrip() + post.lstrip("\n")).strip() + "\n", encoding="utf-8")
                removed = True
        trace_dir = Path(workspace_dir) / ".daming-skillgain" / "skills" / skill_name
        if trace_dir.exists():
            shutil.rmtree(trace_dir, ignore_errors=True)
            removed = True
        return {"agents_md_cleaned": removed, "skill_dir_absent": not trace_dir.exists()}


class ScriptSubjectAdapter(OpencodeSubjectAdapter):
    """script 宿主：.skills/<name>/ 目录注入（script 宿主约定；mock 端到端用）"""

    host_name = "script"
    skill_injection = "script-skills-dir"

    def skill_dirs(self, workspace_dir: Path) -> list[Path]:
        return [Path(workspace_dir) / ".skills"]

    def inject_skill(self, skill_path: Path, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        skills_dir = Path(workspace_dir) / ".skills"
        target = skills_dir / skill_name
        ensure_directory(skills_dir)
        if target.exists():
            shutil.rmtree(target)
        src = find_skill_root(Path(skill_path))
        shutil.copytree(src, target, ignore=shutil.ignore_patterns(".DS_Store", "__pycache__", ".git"))
        return {"skill_dir": str(target), "marker": self.skill_injection, "skill_md": str(target / "SKILL.md")}

    def verify_skill_loaded(self, workspace_dir: Path, skill_name: str) -> dict[str, Any]:
        target = Path(workspace_dir) / ".skills" / skill_name
        skill_md = target / "SKILL.md"
        loaded = target.is_dir() and skill_md.exists()
        return {
            "loaded": loaded,
            "evidence": {
                "skill_dir_exists": target.is_dir(),
                "skill_md_exists": skill_md.exists(),
                "injection": self.skill_injection,
                "host": self.host_name,
            },
        }


SUBJECT_HOST_ADAPTERS: dict[str, type[BaseSubjectAdapter]] = {
    "opencode": OpencodeSubjectAdapter,
    "claude-code": ClaudeCodeSubjectAdapter,
    "claude_code": ClaudeCodeSubjectAdapter,
    "codex": CodexSubjectAdapter,
    "script": ScriptSubjectAdapter,
}


def get_subject_adapter(host: str) -> BaseSubjectAdapter:
    """按宿主名获取 Subject Adapter；未注册宿主显式报错（禁止静默降级）"""
    adapter_cls = SUBJECT_HOST_ADAPTERS.get(host)
    if adapter_cls is None:
        raise SubjectAdapterError(
            f"未注册的被测宿主: '{host}'。可用宿主: {sorted(SUBJECT_HOST_ADAPTERS.keys())}。"
            f"如需支持新宿主，请实现 BaseSubjectAdapter 并注册到 SUBJECT_HOST_ADAPTERS。"
        )
    return adapter_cls()


def run_subject_contract(
    *,
    host: str,
    executor,
    skill_path: Path,
    skill_name: str,
    model: str,
    prompt: str,
    workspace_dir: Path,
    use_skill: bool,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """端到端执行 Subject Adapter 统一契约四件事。

    Returns:
        {
            "host": 宿主名,
            "use_skill": bool,
            "injection": 注入结果 (with-skill) / cleanup 结果 (without-skill),
            "loaded_evidence": 加载验证证据,
            "session": 会话结果摘要 (exit_code/stdout/stderr/transcript/text),
            "artifacts": 收集到的产物相对路径列表,
        }
    """
    workspace_dir = Path(workspace_dir)
    ensure_directory(workspace_dir)
    adapter = get_subject_adapter(host)

    if use_skill:
        injection = adapter.inject_skill(skill_path, workspace_dir, skill_name)
        loaded_evidence = adapter.verify_skill_loaded(workspace_dir, skill_name)
        if not loaded_evidence["loaded"]:
            raise SubjectAdapterError(
                f"[{host}] skill 注入后加载验证失败（防假对照纪律）: {loaded_evidence['evidence']}"
            )
    else:
        injection = adapter.cleanup_skill(workspace_dir, skill_name)
        loaded_evidence = {
            "loaded": False,
            "evidence": {"mode": "without-skill", "host": host, **injection},
        }

    session = adapter.deliver_task(
        executor,
        model=model,
        prompt=prompt,
        workspace_dir=workspace_dir,
        timeout_seconds=timeout_seconds,
    )
    artifacts = adapter.collect_artifacts(workspace_dir)

    return {
        "host": host,
        "use_skill": use_skill,
        "injection": injection,
        "loaded_evidence": loaded_evidence,
        "session": {
            "exit_code": session.exit_code,
            "stdout": session.stdout,
            "stderr": session.stderr,
            "transcript": session.transcript,
            "text": session.text,
            "usage": session.usage,
        },
        "artifacts": artifacts,
    }
