"""Ask Claude through the local Claude Code CLI in headless mode (uses the user's subscription login, no API key).

The call runs in an empty temp directory with every tool disabled (except WebSearch/WebFetch when allow_web), so no
project settings, CLAUDE.md or hooks load and Claude can only answer. `ask` returns the structured output or None
(with a one-line reason)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from agent import proposer_health

NO_TOOLS = "Bash Edit Write Read Glob Grep WebFetch WebSearch Agent NotebookEdit"
WEB_TOOLS = ("WebSearch", "WebFetch")
WEB_TIMEOUT = 900
_spent = 0.0


def spent() -> float:
    """Total cost in USD reported by the CLI during this process."""
    return _spent


def binary() -> str | None:
    """Resolved at call time so tests can set CLAUDE_BIN."""
    b = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "/opt/homebrew/bin/claude"
    return b if os.path.isfile(b) and os.access(b, os.X_OK) else None


def ask(prompt: str, schema: dict, timeout: int | None = None, allow_web: bool = False) -> dict | None:
    """allow_web: keep WebSearch and WebFetch available (every other tool stays disabled); longer default timeout."""
    global _spent
    timeout = timeout or (WEB_TIMEOUT if allow_web else 600)
    exe = binary()
    if exe is None:
        print("claude_cli: claude binary not found")
        return None
    cmd = [exe, "-p", prompt, "--output-format", "json", "--no-session-persistence",
           "--json-schema", json.dumps(schema),
           "--disallowedTools", " ".join(t for t in NO_TOOLS.split() if not (allow_web and t in WEB_TOOLS))]
    if allow_web:
        cmd += ["--allowedTools", " ".join(WEB_TOOLS)]
    try:
        with tempfile.TemporaryDirectory() as cwd:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"claude_cli: timed out after {timeout}s")
        return None
    except OSError as e:
        print(f"claude_cli: could not run claude ({e})")
        return None
    try:
        out = json.loads(p.stdout)
        assert isinstance(out, dict)
    except (ValueError, AssertionError):
        out = None
    if out is not None and proposer_health.is_auth_error(out):
        proposer_health.record_auth_error(out.get("result"))
        print(f"claude_cli: login refused ({out.get('api_error_status')}): {proposer_health.scrub(out.get('result'), 80)}")
        return None
    if p.returncode != 0:
        print(f"claude_cli: exit code {p.returncode}: {proposer_health.scrub(p.stderr or p.stdout, 120)}")
        return None
    if out is None:
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
    proposer_health.record_ok()
    return so
