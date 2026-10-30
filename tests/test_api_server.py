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

def test_evaluate_endpoint_exception(mocker):
    mocker.patch('evaluator.sdk.SkillEvaluator.run', side_effect=Exception("Test Error"))

    response = client.post("/api/evaluate", json={
        "skill_path": "dummy/path",
        "task_prompt": "Test task",
        "models": ["model1"]
    })

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
