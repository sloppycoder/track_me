#!/usr/bin/env python3
"""PermissionRequest hook: auto-grant python heredocs, block pushes to protected branches.

Claude Code invokes this script when a permission dialog appears for Bash
tool calls. It receives a JSON object on stdin describing the tool call and
can print a JSON decision to stdout:

    {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
     "decision": {"behavior": "allow"}}}   — skip the permission prompt
    {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
     "decision": {"behavior": "deny", "message": "..."}}}  — block the call
    (no output / exit 0)  — fall through to normal permission flow

Decision flow:
    command matches "python heredoc"? → allow
    command matches "git push"?       → parse target branch
                                        → deny if protected, else passthrough
    otherwise                         → passthrough
"""

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone

HEREDOC_PATTERN = re.compile(r"^(uv run )?(python3?)\s+<<<")
GIT_PUSH_PATTERN = re.compile(r"\bgit\b.*\bpush\b")
PROTECTED_BRANCH_RE = re.compile(r"^(main|master)$")

# Flags that consume the next argument
GIT_PUSH_FLAGS_WITH_VALUE = {
    "--push-option", "--repo", "--receive-pack", "--exec",
    "-o", "--recurse-submodules",
}

# Flags that are standalone (no value)
GIT_PUSH_FLAGS_STANDALONE = {
    "-u", "--set-upstream", "-f", "--force", "--force-with-lease",
    "--no-verify", "--tags", "--all", "--mirror", "--delete",
    "--dry-run", "-n", "--verbose", "-v", "--quiet", "-q",
    "--prune", "--porcelain", "--no-thin", "--thin",
    "--follow-tags", "--signed", "--no-signed",
    "--atomic", "--progress", "--no-progress",
    "--force-if-includes",
}

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
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = {
        "timestamp": timestamp,
        "hook": "permission_guard",
        "request": payload,
        "decision": decision,
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _emit(behavior: str, message: str = "") -> None:
    decision: dict = {"behavior": behavior}
    if message:
        decision["message"] = message
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": decision,
            }
        },
        sys.stdout,
    )


def _resolve_push_branch(command: str) -> str | None:
    """Parse a git push command and return the target branch name.

    Returns None if no target branch can be determined (passthrough case).
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    # Find 'git' and 'push' positions, handling 'git -C <dir> push ...'
    cwd = None
    git_idx = None
    for i, tok in enumerate(tokens):
        if tok == "git":
            git_idx = i
            break
    if git_idx is None:
        return None

    # Scan for -C <dir> between 'git' and 'push'
    push_idx = None
    j = git_idx + 1
    while j < len(tokens):
        if tokens[j] == "-C" and j + 1 < len(tokens):
            cwd = tokens[j + 1]
            j += 2
            continue
        if tokens[j] == "push":
            push_idx = j
            break
        j += 1

    if push_idx is None:
        return None

    # Collect positional args after 'push', stripping flags
    positional: list[str] = []
    k = push_idx + 1
    while k < len(tokens):
        tok = tokens[k]
        if tok in GIT_PUSH_FLAGS_WITH_VALUE:
            k += 2  # skip flag + its value
            continue
        if tok in GIT_PUSH_FLAGS_STANDALONE or (
            tok.startswith("-") and "=" in tok
        ):
            k += 1
            continue
        # Stop at shell operators
        if tok in ("&&", "||", ";", "|"):
            break
        positional.append(tok)
        k += 1

    # positional: [remote] [refspec...]
    if len(positional) >= 2:
        refspec = positional[1]
        if ":" in refspec:
            return refspec.rsplit(":", 1)[1]
        return refspec

    # No refspec — resolve current branch via git
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass

    return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_input = payload.get("tool_input", {})
    command = tool_input.get("command", "")

    # 1. Auto-grant python heredocs
    if HEREDOC_PATTERN.match(command.strip()):
        _emit("allow")
        log(payload, "allow")
        return

    # 2. Guard git push to protected branches
    if GIT_PUSH_PATTERN.search(command):
        branch = _resolve_push_branch(command)
        if branch and PROTECTED_BRANCH_RE.match(branch):
            _emit(
                "deny",
                f"Push to protected branch '{branch}' is blocked. "
                "Merge via pull request instead.",
            )
            log(payload, f"deny:push-to-{branch}")
            return

    # 3. Everything else: passthrough
    log(payload, "passthrough")


if __name__ == "__main__":
    main()
