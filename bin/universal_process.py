"""Bounded subprocess calls with separate install and authentication environments."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

AUTH_ENV = {
    "codex": ("CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY", "OPENAI_FEDERATION_RULE_ID", "OPENAI_IDENTITY_TOKEN_FILE"),
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
}


def environment(home: Path, search_path: str, *, auth_provider: str | None = None) -> dict[str, str]:
    """Pass no inherited credentials to package managers or version probes."""
    values = {"HOME": str(home), "PATH": search_path, "LANG": os.environ.get("LANG", "en_US.UTF-8")}
    if os.environ.get("TMPDIR"):
        values["TMPDIR"] = os.environ["TMPDIR"]
    if auth_provider in AUTH_ENV:
        values.update({key: os.environ[key] for key in AUTH_ENV[auth_provider] if key in os.environ})
    return values


def run_command(argv: list[str], timeout: int, env: dict[str, str]) -> int:
    try:
        return subprocess.run(argv, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, env=env).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def read_version(argv: list[str], env: dict[str, str]) -> str | None:
    try:
        result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=8, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout[:256] if result.returncode == 0 else None
