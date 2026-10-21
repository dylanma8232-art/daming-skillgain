import pytest
from fastapi.testclient import TestClient
from evaluator.api_server import app

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
