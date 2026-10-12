from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from .skill_analyzer import SkillAnalysis


class DataGenerator:
    """测试数据生成器：当用户选择生成测试数据时，必须产出真实可用的数据文件。

    不再依赖 skill 类别关键词匹配，而是从 skill 的资源声明和输出格式推导。
    """

    def __init__(self, analysis: SkillAnalysis, output_dir: Path):
        self.analysis = analysis
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.generated_files: list[dict[str, str]] = []

    def generate_all(self) -> list[dict[str, str]]:
        """根据 skill 的资源声明和输出格式，强制生成真实数据文件。"""
        # 策略：按资源类型推导需要什么数据
        resource_types = {r.type for r in self.analysis.required_resources}
        output_formats = self.analysis.capability.output_formats

        # 如果有 file 资源需求，生成对应的输入数据
        if "file" in resource_types:
            self._generate_input_file()

        # 如果输出包含代码格式，生成需求规格
        code_formats = {".html", ".tsx", ".ts", ".vue", ".svelte", ".js"}
        if output_formats and set(output_formats) & code_formats:
            self._generate_requirements_spec()

        # 如果输出包含 SQL 或资源有 database
        if "database" in resource_types or ".sql" in output_formats:
            self._generate_database_schema()

        # 如果输出包含文档
        if ".md" in output_formats and "file" not in resource_types:
            self._generate_document_topic()

        # 如果输出包含 UI 页面格式
        if output_formats and set(output_formats) & {".html", ".tsx", ".vue", ".svelte"}:
            self._generate_ui_requirements()

        # 如果仍然没有生成任何文件，生成通用数据
        if not self.generated_files:
            self._generate_generic_data_file()

        # file 资源一旦授权自动生成，必须至少产出一个真实可读的数据文件。
        if "file" in resource_types and not self._has_generated_data_file():
            self._generate_fallback_data()

        # 始终生成任务描述
        self._generate_task_description()

        # 兜底
        if not self._has_generated_data_file():
            self._generate_fallback_data()

        return self.generated_files

    def _has_generated_data_file(self) -> bool:
        data_suffixes = {".csv", ".tsv", ".xlsx", ".xls", ".json", ".xml", ".sql", ".db", ".sqlite"}
        for item in self.generated_files:
            if item.get("type") == "task_description":
                continue
            path = Path(item.get("path", ""))
            if path.suffix.lower() in data_suffixes and path.exists() and path.stat().st_size > 0:
                return True
        return False

    # ---- 数据文件生成器 ----

    def _generate_input_file(self) -> None:
        """根据 skill 类型生成匹配的输入数据。"""
        safe_name = self.analysis.skill_name.replace("-", "_").replace(" ", "_")
        output_formats = set(self.analysis.capability.output_formats)
        resource_types = {r.type for r in self.analysis.required_resources}

        # 如果 output 包含代码/UI 格式，生成 UI 需求规格
        if output_formats & {".html", ".tsx", ".vue", ".svelte"}:
            self._generate_ui_requirements()
            return

        # 如果 output 包含 SQL 或 resource 有 database
        if "database" in resource_types or ".sql" in output_formats:
            self._generate_database_schema()
            return

        # 默认：生成结构化 CSV
        output_path = self.output_dir / f"{safe_name}_input.csv"
        random.seed(42)

        # 根据 skill 核心能力生成匹配的列名
        actions_lower = [a.lower() for a in self.analysis.capability.core_actions]
        has_analysis = any(kw in " ".join(actions_lower) for kw in ["分析", "数据", "探索", "异常", "检测", "洞察"])

        if has_analysis:
            # 数据分析类：带异常值的多维数据
            rows = [["id", "date", "category", "value", "metric", "quality_score"]]
            for i in range(1, 31):
                date = datetime(2025, 1, 1) + timedelta(days=random.randint(0, 364))
                rows.append([
                    i,
                    date.strftime("%Y-%m-%d"),
                    random.choice(["A", "B", "C", "D"]),
                    round(random.uniform(100, 10000), 2),
                    round(random.uniform(1, 100), 2),
                    round(random.uniform(0.5, 0.99), 4) if random.random() > 0.05 else round(random.uniform(0.0, 0.3), 4),
                ])
            # 注入异常
            rows[-2][4] = -999  # 异常值
            rows[-1][5] = 0.99  # 异常高分
        else:
            # 通用数据类
            rows = [["id", "name", "category", "amount", "date", "status"]]
            for i in range(1, 101):
                rows.append([
                    i,
                    f"Item_{i:03d}",
                    random.choice(["A", "B", "C"]),
                    round(random.uniform(10, 1000), 2),
                    (datetime(2025, 1, 1) + timedelta(days=random.randint(0, 364))).strftime("%Y-%m-%d"),
                    random.choice(["active", "inactive", "pending"]),
                ])

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(rows)

        self.generated_files.append({
            "path": str(output_path),
            "type": "input_file",
            "description": f"输入数据 CSV（{len(rows)-1} 条记录）",
        })

    def _generate_generic_data_file(self) -> None:
        """通用结构化数据。"""
        safe_name = self.analysis.skill_name.replace("-", "_").replace(" ", "_")
        output_path = self.output_dir / f"{safe_name}_data.json"
        random.seed(42)
        data = {
            "records": [
                {"id": i, "value": random.randint(10, 1000), "category": random.choice(["A", "B", "C"]), "date": (datetime(2025, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d")}
                for i in range(1, 51)
            ],
            "metadata": {"generated": True, "source": "daming-skillgain test data generator", "record_count": 50},
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.generated_files.append({
            "path": str(output_path),
            "type": "structured_data",
            "description": "通用结构化 JSON 数据（50 条记录）",
        })

    def _generate_fallback_data(self) -> None:
        """兜底：确保至少有一个数据文件。"""
        safe_name = self.analysis.skill_name.replace("-", "_").replace(" ", "_")
        output_path = self.output_dir / f"{safe_name}_fallback.csv"
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "name", "value"])
            for i in range(1, 11):
                writer.writerow([i, f"Item_{i}", random.randint(1, 100)])
        self.generated_files.append({
            "path": str(output_path),
            "type": "fallback_data",
            "description": "兜底 CSV 数据（10 条记录）",
        })

    def _generate_task_description(self) -> None:
        """生成任务描述，引用已生成的数据文件。"""
        safe_name = self.analysis.skill_name.replace("-", "_").replace(" ", "_")
        output_path = self.output_dir / f"{safe_name}_task.md"

        # 收集实际生成的数据文件信息
        data_files_info = []
        for f in self.generated_files:
            fp = Path(f["path"])
            size_kb = round(fp.stat().st_size / 1024, 1)
            data_files_info.append(f"`{fp.name}` ({size_kb} KB) - {f['description']}")

        # 生成文件引用列表（供 task prompt 引用）
        file_refs = []
        for f in self.generated_files:
            fp = Path(f["path"])
            file_refs.append(fp.name)

        # 生成动态任务描述
        if file_refs:
            files_text = "、".join(f"`{fn}`" for fn in file_refs[:5])
            task_body = f"""## 测试数据
已为你准备好测试数据文件：{files_text}，文件位于当前工作目录，可直接读取。

## 业务需求
按照 skill 的完整流程对上述数据进行完整分析，保留关键步骤和结果证据。
"""
        else:
            task_body = """## 业务需求
按照 skill 的完整流程执行任务，保留关键步骤和结果证据。
"""

        content = f"""# {self.analysis.skill_name} 评测任务

{self.analysis.capability.summary}

## 核心能力
"""
        for a in self.analysis.capability.core_actions[:5]:
            content += f"- {a}\n"

        content += "\n## 生成的测试数据\n"
        for info in data_files_info:
            content += f"- {info}\n"

        content += "\n" + task_body

        output_path.write_text(content, encoding="utf-8")
        self.generated_files.append({
            "path": str(output_path),
            "type": "task_description",
            "description": f"评测任务描述文件（引用 {len([f for f in self.generated_files if f['type'] != 'task_description'])} 个数据文件）",
        })

    def generate_readme(self) -> Path:
        safe_name = self.analysis.skill_name.replace("-", "_").replace(" ", "_")
        readme_path = self.output_dir / f"{safe_name}_README.md"

        data_files = [f for f in self.generated_files if f["type"] != "task_description"]
        task_files = [f for f in self.generated_files if f["type"] == "task_description"]

        lines = [
            f"# {self.analysis.skill_name} 测试数据",
            "",
            f"**执行模式**: {self.analysis.execution_mode}",
            "",
            "## 数据文件",
        ]
        for f in data_files:
            fp = Path(f["path"])
            size_kb = round(fp.stat().st_size / 1024, 1)
            lines.append(f"- `{fp.name}`: {f['description']}（{size_kb} KB）")

        if task_files:
            lines += ["", "## 任务描述"]
            for f in task_files:
                lines.append(f"- `{Path(f['path']).name}`: {f['description']}")

        lines += [
            "",
            "## Skill 核心能力",
            f"- 概述: {self.analysis.capability.summary}",
        ]
        if self.analysis.capability.core_actions:
            lines.append("- 主要动作:")
            lines.extend(f"  - {a}" for a in self.analysis.capability.core_actions[:5])

        lines += [
            "",
            f"## 评测提示",
            f"这个 skill 的评测任务应围绕其核心能力展开，重点考察: {self.analysis.estimated_task_type}",
            "",
            "> 注意：本测试数据由 daming-skillgain 自动生成（synthetic/mock），分析结论仅供参考。",
        ]
        readme_path.write_text("\n".join(lines), encoding="utf-8")
        return readme_path


def generate_test_data(analysis: SkillAnalysis, output_dir: Path) -> tuple[list[dict[str, str]], Path]:
    generator = DataGenerator(analysis, output_dir)
    files = generator.generate_all()
    readme = generator.generate_readme()
    return files, readme
