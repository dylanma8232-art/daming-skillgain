import json

import pytest

from evaluator.engine import _extract_skill_promises
from evaluator.promise_parser import _extract_constraint_promises, _extract_file_promises
from evaluator.workflow import SkillEvaluationWorkflow


def test_file_promise_outside_generation_sentence_is_not_lost():
    files = _extract_file_promises("report.md 文件")
    assert [(f.filename, f.file_type) for f in files] == [("report.md", "md")]


def test_constraint_quantifier_matches_normal_requirement():
    constraints = _extract_constraint_promises("必须保留所有原始数据并输出报告")
    assert constraints and "保留所有原始数据" in constraints[0].text


def test_engine_regex_optimization_preserves_extraction(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "name: sample\nPhase 1: Analyze source data\n输出：REPORT.MD\n"
        "必须保留所有原始数据\n命令：python analyze.py\n"
    )
    result = _extract_skill_promises(str(tmp_path))
    assert result["skill_name"] == "sample"
    assert ".md" in result["output_files"]
    assert result["phases"] == ["Phase 1: Analyze source data"]
    assert result["constraints"]
    assert result["entry_points"] == ["python analyze.py"]


def test_resource_validation_preserves_missing_input_questions(tmp_path):
    workflow = SkillEvaluationWorkflow.__new__(SkillEvaluationWorkflow)
    analysis = {"required_resources": [{"name": "source", "type": "file", "required": True}]}
    with pytest.raises(ValueError) as exc:
        workflow._validate_resources(analysis, {"task_prompt": "analyze"})
    detail = json.loads(str(exc.value))
    assert detail["error"] == "needs_user_input"
    assert detail["questions"][0]["resource_type"] == "file"
    source = tmp_path / "source.csv"
    source.write_text("value\n1\n")
    workflow._validate_resources(analysis, {"task_prompt": "analyze", "input_files": [str(source)]})


@pytest.mark.parametrize("resources", ["{invalid", "{}"])
def test_resource_validation_rejects_invalid_resources_json(resources):
    workflow = SkillEvaluationWorkflow.__new__(SkillEvaluationWorkflow)
    with pytest.raises(ValueError):
        workflow._validate_resources({}, {"resources_json": resources})
