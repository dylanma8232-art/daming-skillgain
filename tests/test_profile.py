from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluator.profile import (
    EvalProfile,
    ProfileError,
    find_default_profile,
    load_profile,
    profile_to_config,
)


def test_from_dict_defaults_voting_and_summary():
    profile = EvalProfile.from_dict(
        {"skill_path": "./s", "task_prompt": "t", "models": ["m1", "m2"]}
    )
    assert profile.voting_models == ["m1"]
    assert profile.summary_model == "m1"
    assert profile.warnings


def test_load_save_roundtrip(tmp_path: Path):
    profile = EvalProfile.from_dict(
        {
            "skill_path": "./s",
            "task_prompt": "t",
            "models": ["m1"],
            "voting_models": ["j1"],
            "summary_model": "j1",
            "resources": [{"description": "账号", "value": "u", "allow_auto_generation": False}],
            "host": "cursor-agent",
            "host_config": {"timeout": 10},
            "repetitions": 2,
        }
    )
    path = profile.save(tmp_path / "eval-config.json")
    loaded = load_profile(path)
    assert loaded.host == "cursor-agent"
    assert loaded.host_config == {"timeout": 10}
    assert loaded.repetitions == 2
    assert json.loads(loaded.resources_json())[0]["value"] == "u"


def test_missing_profile_file_raises(tmp_path: Path):
    with pytest.raises(ProfileError):
        load_profile(tmp_path / "nope.json")


def test_invalid_json_raises(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(bad)


def test_bad_field_types_raise():
    with pytest.raises(ProfileError):
        EvalProfile.from_dict({"models": 123})
    with pytest.raises(ProfileError):
        EvalProfile.from_dict({"resources": "nope"})
    with pytest.raises(ProfileError):
        EvalProfile.from_dict({"repetitions": "abc"})


def test_unknown_fields_only_warn():
    profile = EvalProfile.from_dict({"models": ["m"], "wat": 1})
    assert any("unknown" in w or "未知" in w for w in profile.warnings)


def test_validate_missing_fields():
    profile = EvalProfile()
    assert profile.validate() == ["skill_path", "task_prompt", "models"]
    relaxed = EvalProfile(skill_path="s", models=["m"], allow_generated_task_prompt=True)
    assert relaxed.validate() == []


def test_profile_to_config_and_plan_kwargs():
    profile = EvalProfile.from_dict(
        {
            "skill_path": "s",
            "task_prompt": "t",
            "models": ["m"],
            "resources": [{"description": "d", "value": "v"}],
            "host": "codex",
            "connectivity_timeout": 5,
        }
    )
    config = profile_to_config(profile, dry_run=True)
    assert config["dry_run"] is True
    assert config["auto"] is True
    assert config["host"] == "codex"
    plan_kwargs = profile.to_plan_kwargs()
    assert plan_kwargs["connectivity_timeout"] == 5
    assert json.loads(plan_kwargs["resources_json"])[0]["value"] == "v"


def test_find_default_profile(tmp_path: Path):
    assert find_default_profile(tmp_path) is None
    (tmp_path / "eval-config.json").write_text("{}", encoding="utf-8")
    assert find_default_profile(tmp_path) is not None
