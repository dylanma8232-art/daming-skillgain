#!/usr/bin/env python3
"""启动 MCP Server"""
from evaluator.mcp_server import run_mcp_server

def main():
    print("启动 MCP Server...")
    print("任何支持 MCP 的 agent 都能调用")
    run_mcp_server()

if __name__ == "__main__":
    main()
