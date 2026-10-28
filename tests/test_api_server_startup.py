import pytest
from unittest.mock import MagicMock
import evaluator.api_server as api_server
from evaluator.api_server import run_server, app

def test_run_server_success(monkeypatch):
    """测试在安装了依赖时正常启动服务器"""
    mock_uvicorn_run = MagicMock()
    try:
        import uvicorn
        monkeypatch.setattr(uvicorn, "run", mock_uvicorn_run)
    except ImportError:
        import sys
        mock_uvicorn = MagicMock()
        mock_uvicorn.run = mock_uvicorn_run
        monkeypatch.setitem(sys.modules, "uvicorn", mock_uvicorn)

    original_has_fastapi = api_server.HAS_FASTAPI
    api_server.HAS_FASTAPI = True

    try:
        run_server(host="127.0.0.1", port=9000)
        mock_uvicorn_run.assert_called_once_with(app, host="127.0.0.1", port=9000)
    finally:
        api_server.HAS_FASTAPI = original_has_fastapi

def test_run_server_missing_fastapi():
    """测试在未安装依赖时抛出错误"""
    original_has_fastapi = api_server.HAS_FASTAPI
    api_server.HAS_FASTAPI = False

    try:
        with pytest.raises(ImportError, match="需要安装 fastapi 和 uvicorn: pip install fastapi uvicorn"):
            run_server()
    finally:
        api_server.HAS_FASTAPI = original_has_fastapi
