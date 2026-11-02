from pathlib import Path
import json
import pytest

from evaluator.result_files import (
    _declared_skill_outputs,
    _is_parseable,
    _is_process_file,
    infer_result_contract,
    select_result_files,
)


def test_is_process_file_ignores_parent_path_keywords(tmp_path: Path):
    # Simulate run folder with "log" or "trace" in parent directory name
    run_dir = tmp_path / "logging-skill-run"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)

    deliverable = artifacts_dir / "sales_report.xlsx"
    deliverable.write_bytes(b"dummy")

    log_file = artifacts_dir / "run.log"
    log_file.write_text("running...", encoding="utf-8")

    trace_file = artifacts_dir / "execution_trace.md"
    trace_file.write_text("trace info", encoding="utf-8")

    # When base_dir is passed, parent dir name containing 'log' should NOT cause deliverable to be flagged
    assert not _is_process_file(deliverable, base_dir=artifacts_dir)
    # Process files within artifacts_dir must still be detected
    assert _is_process_file(log_file, base_dir=artifacts_dir)
    assert _is_process_file(trace_file, base_dir=artifacts_dir)


def test_declared_skill_outputs_code_extensions(tmp_path: Path):
    skill_dir = tmp_path / "code-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        """# Component Skill
This skill exports your component into `.tsx`, `.vue`, and `.sql` migration files.
""",
        encoding="utf-8",
    )

    declared = _declared_skill_outputs(skill_dir)
    exts = [ext for ext, _role, _label in declared]
    assert ".tsx" in exts
    assert ".vue" in exts
    assert ".sql" in exts

    contract = infer_result_contract(task_prompt="", target_skill="code-skill", skill_path=skill_dir)
    contract_exts = [out["ext"] for out in contract["outputs"]]
    assert ".tsx" in contract_exts
    assert ".vue" in contract_exts
    assert ".sql" in contract_exts


def test_is_parseable_for_code_extensions(tmp_path: Path):
    tsx_file = tmp_path / "component.tsx"
    tsx_file.write_text("export const App = () => <div>Hello</div>;", encoding="utf-8")
    ok, _ = _is_parseable(tsx_file)
    assert ok

    vue_file = tmp_path / "component.vue"
    vue_file.write_text("<template><div>Hello</div></template>", encoding="utf-8")
    ok, _ = _is_parseable(vue_file)
    assert ok

    sql_file = tmp_path / "schema.sql"
    sql_file.write_text("CREATE TABLE users (id INT PRIMARY KEY);", encoding="utf-8")
    ok, _ = _is_parseable(sql_file)
    assert ok


def test_select_result_files_with_parent_log_dir_and_code_artifacts(tmp_path: Path):
    # Simulate a workspace path containing 'log'
    workspace = tmp_path / "runs" / "test-logging-agent"
    artifacts = workspace / "artifacts"
    artifacts.mkdir(parents=True)

    # Write metadata
    skill_dir = tmp_path / "sql-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "# SQL Generator Skill\nGenerates optimized `.sql` queries.",
        encoding="utf-8",
    )

    metadata = {
        "target_skill": "sql-skill",
        "target_skill_path": str(skill_dir),
    }
    (workspace / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    # Deliverables and process files
    sql_deliverable = artifacts / "query.sql"
    sql_deliverable.write_text("SELECT id, name FROM users WHERE active = 1;", encoding="utf-8")

    (artifacts / "run.log").write_text("Log output", encoding="utf-8")
    (artifacts / "summary.md").write_text("Task completed successfully", encoding="utf-8")

    res = select_result_files(workspace, task_prompt="生成用户查询脚本", target_skill="sql-skill")
    assert res["status"] == "ok"
    assert len(res["validated_result_files"]) == 1
    assert res["validated_result_files"][0]["name"] == "query.sql"
    assert res["validated_result_files"][0]["ext"] == ".sql"

    # Evidence files should contain the log and summary
    evidence_names = {e["name"] for e in res["evidence_files"]}
    assert "run.log" in evidence_names
    assert "summary.md" in evidence_names
