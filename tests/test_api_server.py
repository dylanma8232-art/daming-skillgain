import os
import pytest

try:
    from fastapi.testclient import TestClient
    from evaluator.api_server import app
    HAS_FASTAPI = app is not None
except ImportError:
    HAS_FASTAPI = False

if not HAS_FASTAPI:
    pytest.skip("fastapi not installed", allow_module_level=True)

client = TestClient(app)

TEST_API_KEY = "test-secret-key-123"


def test_unauthenticated_request_rejected(monkeypatch):
    """未传 API Key 或未配置 Key 时拒绝请求"""
    monkeypatch.setenv("EVALUATOR_API_KEY", TEST_API_KEY)

    response = client.post("/api/evaluate", json={
        "task_prompt": "Test task",
        "models": ["model1"]
    })
    assert response.status_code == 401
    assert "API key" in response.json()["detail"]


def test_unauthenticated_server_no_key_configured(monkeypatch):
    """服务端未配置 EVALUATOR_API_KEY / API_KEY 时拒绝请求"""
    monkeypatch.delenv("EVALUATOR_API_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)

    response = client.get("/api/health", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 401
    assert "not configured" in response.json()["detail"]


def test_authenticated_health_check(monkeypatch):
    """通过 X-API-Key 或 Bearer token 校验服务健康度"""
    monkeypatch.setenv("EVALUATOR_API_KEY", TEST_API_KEY)

    # 方式一：X-API-Key Header
    resp1 = client.get("/api/health", headers={"X-API-Key": TEST_API_KEY})
    assert resp1.status_code == 200
    assert resp1.json() == {"status": "ok"}

    # 方式二：Authorization Bearer Header
    resp2 = client.get("/api/health", headers={"Authorization": f"Bearer {TEST_API_KEY}"})
    assert resp2.status_code == 200
    assert resp2.json() == {"status": "ok"}


def test_evaluate_endpoint_exception(mocker, monkeypatch):
    """测试带 Auth 头信息的 evaluate 异常处理"""
    monkeypatch.setenv("EVALUATOR_API_KEY", TEST_API_KEY)
    mocker.patch('evaluator.sdk.SkillEvaluator.run', side_effect=Exception("Test Error"))

    response = client.post(
        "/api/evaluate",
        headers={"X-API-Key": TEST_API_KEY},
        json={
            "task_prompt": "Test task",
            "models": ["model1"]
        }
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Test Error"


def test_concurrent_create_run_directory_atomic_uniqueness(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from evaluator.models import EvaluationConfig, RunnerConfig, AnalysisConfig
    from evaluator.engine import EvaluationEngine

    num_threads = 10
    barrier = threading.Barrier(num_threads)

    config = EvaluationConfig(
        evaluation_name="concurrent-test",
        target_skill="sample_skill",
        target_skill_path="sample_skill",
        task_prompt="sample task",
        workspace_root=str(tmp_path / "runs"),
        models=["model1"],
        blind_review_models=["model1"],
        summary_model="model1",
        runners=[RunnerConfig(name="opencode", command_template="echo")],
        input_files=[],
        analysis=AnalysisConfig(),
    )

    def run_create(engine):
        barrier.wait()
        return engine.create_run_directory()

    engines = [EvaluationEngine(config, tmp_path / "eval-config.json") for _ in range(num_threads)]

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(run_create, engine) for engine in engines]
        run_dirs = [f.result() for f in futures]

    assert len(run_dirs) == num_threads
    assert len(set(run_dirs)) == num_threads
    for run_dir in run_dirs:
        assert run_dir.exists()
        assert (run_dir / "experiments").exists()


def test_evaluate_uses_server_env_not_request_body(mocker, monkeypatch):
    """验证 evaluate 不再从请求体中接收 skill_path / executor_type / executor_config"""
    monkeypatch.setenv("EVALUATOR_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("EVALUATOR_SKILL_PATH", "/server/safe/path")
    monkeypatch.setenv("EVALUATOR_EXECUTOR_TYPE", "claude-code")

    mock_init = mocker.patch('evaluator.sdk.SkillEvaluator.__init__', return_value=None)
    mock_run = mocker.patch('evaluator.sdk.SkillEvaluator.run', return_value={"status": "completed"})

    response = client.post(
        "/api/evaluate",
        headers={"X-API-Key": TEST_API_KEY},
        json={
            "skill_path": "/malicious/user/path",
            "executor_type": "script",
            "executor_config": {"script": "/bin/sh"},
            "task_prompt": "Secure test",
            "models": ["model1"]
        }
    )

    assert response.status_code == 200
    # 确认 SkillEvaluator 初始化时参数为服务端配置，而非请求体中传入的恶意值
    mock_init.assert_called_once_with(
        skill_path="/server/safe/path",
        executor_type="claude-code",
    )
