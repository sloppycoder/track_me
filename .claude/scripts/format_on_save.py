#!/usr/bin/env python3
"""PostToolUse hook: auto-format Python files after edit/write.

Runs `ruff format` on any .py file that was edited or created by Claude,
preventing style drift and reducing pre-commit formatting overhead.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime


def _resolve_log_path() -> str | None:
    path = os.environ.get("CLAUDE_HOOK_LOG")
    if not path:
        return None
    if path.startswith("~"):
        return os.path.expanduser(path)
    if not os.path.isabs(path):
        root = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
        return os.path.join(root, path)
    return path


def log(payload: dict, decision: str) -> None:
    log_file = _resolve_log_path()
    if not log_file:
        return
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    timestamp = datetime.now(UTC).isoformat()
    entry = {
        "timestamp": timestamp,
        "hook": "format_on_save",
        "request": payload,
        "decision": decision,
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path.endswith(".py"):
        log(payload, "skip")
        return

    ruff = shutil.which("ruff")
    if ruff:
        cmd = [ruff, "format", file_path]
    else:
        cmd = ["uv", "run", "ruff", "format", file_path]

    result = subprocess.run(cmd, capture_output=True, timeout=10)  # noqa: S603
    decision = "formatted" if result.returncode == 0 else "error"
    log(payload, decision)


if __name__ == "__main__":
    main()
