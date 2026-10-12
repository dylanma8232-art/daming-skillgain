#!/usr/bin/env python3
"""启动 API Server"""
import argparse
from evaluator.api_server import run_server

def main():
    parser = argparse.ArgumentParser(description="Skill Evaluator API Server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    args = parser.parse_args()
    
    print(f"启动 API Server: http://{args.host}:{args.port}")
    print(f"API 文档: http://{args.host}:{args.port}/docs")
    run_server(host=args.host, port=args.port)

if __name__ == "__main__":
    main()
