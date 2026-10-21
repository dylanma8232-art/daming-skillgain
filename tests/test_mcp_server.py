import pytest
from unittest.mock import patch, MagicMock

import evaluator.mcp_server as mcp_server

def test_run_mcp_server_no_mcp():
    """Test run_mcp_server raises ImportError when mcp is not installed."""
    with patch('evaluator.mcp_server.HAS_MCP', False):
        with pytest.raises(ImportError, match="需要安装 mcp: pip install mcp"):
            mcp_server.run_mcp_server()

def test_run_mcp_server_with_mcp():
    """Test run_mcp_server starts the server when mcp is installed."""
    mock_server = MagicMock()
    mock_run_coro = MagicMock()
    mock_server.run.return_value = mock_run_coro

    with patch('evaluator.mcp_server.HAS_MCP', True), \
         patch('evaluator.mcp_server.server', mock_server, create=True), \
         patch('asyncio.run') as mock_asyncio_run:
        mcp_server.run_mcp_server()

        mock_server.run.assert_called_once()
        mock_asyncio_run.assert_called_once_with(mock_run_coro)
