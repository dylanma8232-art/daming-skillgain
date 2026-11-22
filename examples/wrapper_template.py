#!/usr/bin/env python3
"""Universal script adapter template for daming-skillgain.

Hook up ANY CLI-based AI coding agent (Cursor, Trae, Windsurf, custom scripts, etc.)
to daming-skillgain via `--host script --host-config '{"script": "./examples/wrapper_template.py"}'`.

Contract:
- Reads one JSON object from stdin: {"model", "prompt", "cwd", "skill_dir"}
- Runs your AI tool inside `cwd`
- Prints one JSON object to stdout: {"exit_code", "stdout", "stderr", "text", "transcript", "usage"}
"""
from __future__ import annotations

import json
import subprocess
import sys


def main() -> None:
    payload = json.loads(sys.stdin.read())
    prompt = payload["prompt"]
    cwd = payload["cwd"]
    # Optional: model = payload.get("model", "")
    # Optional: skill_dir = payload.get("skill_dir")  # Injected into cwd/.skills/ when with_skill=True

    # Replace the command below with your AI agent's non-interactive CLI invocation.
    cmd = ["your-agent-cli", "--non-interactive", prompt]

    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    print(
        json.dumps(
            {
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "text": proc.stdout,
                "transcript": proc.stdout,
                "usage": {},
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
