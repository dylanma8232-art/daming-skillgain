import os
import subprocess
import yaml
from pathlib import Path

WORKFLOW_PATH = Path(".github/workflows/auto-merge-jules.yml")


def test_workflow_yaml_structure():
    """Verify workflow configuration safety and trigger structure."""
    assert WORKFLOW_PATH.exists(), "Workflow file should exist"
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Trigger should be pull_request_target (note PyYAML parses unquoted 'on' as True)
    on_clause = data.get("on") or data.get(True) or {}
    assert "pull_request_target" in on_clause

    # Ensure jobs exist
    jobs = data.get("jobs", {})
    assert "auto-merge" in jobs

    steps = jobs["auto-merge"].get("steps", [])
    step_names = [s.get("name") for s in steps]
    assert "Identify Jules PR" in step_names

    # Check that any checkout step uses ref: main to avoid running untrusted PR code
    for step in steps:
        if step.get("uses", "").startswith("actions/checkout"):
            with_clause = step.get("with", {})
            assert with_clause.get("ref") == "main", (
                "actions/checkout must checkout main, not PR code"
            )


def get_identify_step_script():
    """Helper to extract the run script from 'Identify Jules PR' step."""
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    steps = data["jobs"]["auto-merge"]["steps"]
    for step in steps:
        if step.get("name") == "Identify Jules PR":
            return step["run"]
    raise RuntimeError("Identify Jules PR step not found in workflow")


def run_identify_script(env_vars: dict) -> str:
    """Run the identify bash script with given environment variables."""
    script = get_identify_step_script()
    env = os.environ.copy()
    env.update(env_vars)

    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_fork_pr_with_jules_body_rejected():
    """Fork PRs copying Jules body text must NOT be identified as Jules PRs."""
    env = {
        "IS_FORK": "true",
        "HEAD_REPO": "attacker/daming-skillgain",
        "TARGET_REPO": "owner/daming-skillgain",
        "PR_USER": "attacker",
        "REPO_OWNER": "owner",
        "BODY": "PR created automatically by Jules",
        "BRANCH": "main",
        "GITHUB_OUTPUT": "/dev/stdout",
    }
    output = run_identify_script(env)
    assert "is_jules=false" in output
    assert "is_jules=true" not in output


def test_fork_pr_with_15_digit_branch_rejected():
    """Fork PRs copying 15-digit branch naming must NOT be identified as Jules PRs."""
    env = {
        "IS_FORK": "true",
        "HEAD_REPO": "attacker/daming-skillgain",
        "TARGET_REPO": "owner/daming-skillgain",
        "PR_USER": "attacker",
        "REPO_OWNER": "owner",
        "BODY": "Normal PR body",
        "BRANCH": "jules-123456789012345",
        "GITHUB_OUTPUT": "/dev/stdout",
    }
    output = run_identify_script(env)
    assert "is_jules=false" in output
    assert "is_jules=true" not in output


def test_fork_pr_spoofing_repo_owner_login_rejected():
    """Fork PRs cannot bypass security even if PR user login is owner (forks remain untrusted)."""
    env = {
        "IS_FORK": "true",
        "HEAD_REPO": "attacker/daming-skillgain",
        "TARGET_REPO": "owner/daming-skillgain",
        "PR_USER": "owner",
        "REPO_OWNER": "owner",
        "BODY": "PR created automatically by Jules",
        "BRANCH": "123456789012345",
        "GITHUB_OUTPUT": "/dev/stdout",
    }
    output = run_identify_script(env)
    assert "is_jules=false" in output


def test_untrusted_user_in_same_repo_rejected():
    """PRs in the same repo by untrusted users must NOT be identified as Jules PRs."""
    env = {
        "IS_FORK": "false",
        "HEAD_REPO": "owner/daming-skillgain",
        "TARGET_REPO": "owner/daming-skillgain",
        "PR_USER": "untrusted_contributor",
        "REPO_OWNER": "owner",
        "BODY": "PR created automatically by Jules",
        "BRANCH": "123456789012345",
        "GITHUB_OUTPUT": "/dev/stdout",
    }
    output = run_identify_script(env)
    assert "is_jules=false" in output


def test_trusted_user_same_repo_jules_body_accepted():
    """PRs from same repo by repo owner with Jules body must be identified as Jules PRs."""
    env = {
        "IS_FORK": "false",
        "HEAD_REPO": "owner/daming-skillgain",
        "TARGET_REPO": "owner/daming-skillgain",
        "PR_USER": "owner",
        "REPO_OWNER": "owner",
        "BODY": "PR created automatically by Jules - bugfix",
        "BRANCH": "patch-1",
        "GITHUB_OUTPUT": "/dev/stdout",
    }
    output = run_identify_script(env)
    assert "is_jules=true" in output


def test_trusted_bot_same_repo_15_digit_branch_accepted():
    """PRs from same repo by Jules bot account with 15-digit branch must be identified as Jules PRs."""
    env = {
        "IS_FORK": "false",
        "HEAD_REPO": "owner/daming-skillgain",
        "TARGET_REPO": "owner/daming-skillgain",
        "PR_USER": "google-jules-app[bot]",
        "REPO_OWNER": "owner",
        "BODY": "Automated fixes",
        "BRANCH": "jules-123456789012345",
        "GITHUB_OUTPUT": "/dev/stdout",
    }
    output = run_identify_script(env)
    assert "is_jules=true" in output


def test_trusted_user_same_repo_without_jules_pattern_rejected():
    """PRs from same repo by trusted user without Jules body or branch pattern are NOT identified as Jules PRs."""
    env = {
        "IS_FORK": "false",
        "HEAD_REPO": "owner/daming-skillgain",
        "TARGET_REPO": "owner/daming-skillgain",
        "PR_USER": "owner",
        "REPO_OWNER": "owner",
        "BODY": "Manual pull request",
        "BRANCH": "my-feature-branch",
        "GITHUB_OUTPUT": "/dev/stdout",
    }
    output = run_identify_script(env)
    assert "is_jules=false" in output
