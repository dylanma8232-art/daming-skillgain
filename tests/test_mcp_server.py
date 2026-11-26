import asyncio
import json
import sys
from pathlib import Path

import pytest
from unittest.mock import patch

import evaluator.mcp_server as mcp_server

def test_run_mcp_server_no_mcp():
    """Test run_mcp_server raises ImportError when mcp is not installed."""
    with patch('evaluator.mcp_server.HAS_MCP', False):
        with pytest.raises(ImportError, match="需要安装 mcp: pip install mcp"):
            mcp_server.run_mcp_server()

def test_run_mcp_server_with_mcp():
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    sample_skill = Path(__file__).resolve().parents[1] / "examples" / "sample-skill"

    async def check_server():
        params = StdioServerParameters(command=sys.executable, args=["-m", "evaluator.mcp_server"])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "daming-skillgain"
                tools = await session.list_tools()
                assert {"analyze_skill", "plan", "pipeline"} <= {tool.name for tool in tools.tools}
                result = await session.call_tool("analyze_skill", {"skill_path": str(sample_skill)})
                assert not result.isError
                assert json.loads(result.content[0].text)["skill_name"] == "release-notes-writer"
                result = await session.call_tool("plan", {"skill_path": str(sample_skill)})
                assert not result.isError
                assert json.loads(result.content[0].text)["error"] == "needs_user_input"

    asyncio.run(asyncio.wait_for(check_server(), timeout=25))
