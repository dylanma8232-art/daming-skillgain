import pytest
from unittest.mock import MagicMock
import subprocess
from evaluator.model_discovery import ModelDiscovery

def test_opencode_models_subprocess_error(monkeypatch):
    discovery = ModelDiscovery()

    # Mock opencode --version to succeed so we enter the models check
    mock_version_proc = MagicMock()
    mock_version_proc.returncode = 0
    mock_version_proc.stdout = "opencode version 1.0.0"

    # We want subprocess.run to raise an exception when called with ["opencode", "models"]
    def mock_subprocess_run(args, **kwargs):
        if args == ["opencode", "--version"]:
            return mock_version_proc
        elif args == ["opencode", "models"]:
            raise Exception("Subprocess failed!")
        return MagicMock()

    monkeypatch.setattr("subprocess.run", mock_subprocess_run)
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False) # skip GUI config check

    result = discovery._detect_opencode()

    # It shouldn't crash, should be marked as available but with no models
    assert result["available"] is True
    assert result["models"] == []
