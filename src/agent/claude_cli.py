"""Ask Claude through the local Claude Code CLI in headless mode (uses the user's subscription login, no API key).

The call runs in an empty temp directory with every tool disabled, so no project settings, CLAUDE.md or hooks
load and Claude can only answer. `ask` returns the structured output or None (with a one-line reason)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

NO_TOOLS = "Bash Edit Write Read Glob Grep WebFetch WebSearch Agent NotebookEdit"
_spent = 0.0


def spent() -> float:
    """Total cost in USD reported by the CLI during this process."""
    return _spent


def binary() -> str | None:
    """Resolved at call time so tests can set CLAUDE_BIN."""
    b = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "/opt/homebrew/bin/claude"
    return b if os.path.isfile(b) and os.access(b, os.X_OK) else None


def ask(prompt: str, schema: dict, timeout: int = 600) -> dict | None:
    global _spent
    exe = binary()
    if exe is None:
        print("claude_cli: claude binary not found")
        return None
    cmd = [exe, "-p", prompt, "--output-format", "json", "--no-session-persistence",
           "--json-schema", json.dumps(schema), "--disallowedTools", NO_TOOLS]
    try:
        with tempfile.TemporaryDirectory() as cwd:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"claude_cli: timed out after {timeout}s")
        return None
    except OSError as e:
        print(f"claude_cli: could not run claude ({e})")
        return None
    if p.returncode != 0:
        print(f"claude_cli: exit code {p.returncode}: {(p.stderr or p.stdout).strip()[:120]}")
        return None
    try:
        out = json.loads(p.stdout)
        assert isinstance(out, dict)
    except (ValueError, AssertionError):
        print("claude_cli: output was not a JSON object")
        return None
    cost = out.get("total_cost_usd")
    if isinstance(cost, (int, float)):
        _spent += float(cost)
    if out.get("is_error") or out.get("subtype", "success") != "success":
        print(f"claude_cli: error result ({out.get('subtype')}): {str(out.get('result'))[:120]}")
        return None
    so = out.get("structured_output")
    if not isinstance(so, dict):
        print("claude_cli: no structured_output in the result")
        return None
    return so
