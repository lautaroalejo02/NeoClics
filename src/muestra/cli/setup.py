"""Portable, project-local agent configuration. Never edits global config."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import tomli_w


def agent_setup(repo: Path, project: Path | None, write: Path | None = None) -> dict:
    repo = repo.expanduser().resolve()
    if not (repo / "pyproject.toml").is_file():
        raise ValueError(
            "--repo must point to the Muestra source checkout containing pyproject.toml."
        )
    command = shutil.which("uv") or "uv"
    args = ["run", "--directory", str(repo), "--extra", "server", "muestra", "mcp"]
    if project:
        args += ["--project", str(project.resolve())]
    entry = {"command": command, "args": args}
    claude = {"mcpServers": {"muestra": {"type": "stdio", **entry}}}
    codex = tomli_w.dumps(
        {"mcp_servers": {"muestra": {**entry, "startup_timeout_sec": 60, "tool_timeout_sec": 1800}}}
    )
    result = {
        "claude_mcp_json": claude,
        "claude_command": (subprocess.list2cmdline if os.name == "nt" else shlex.join)(
            [
                "claude",
                "mcp",
                "add",
                "--transport",
                "stdio",
                "--scope",
                "project",
                "muestra",
                "--",
                command,
                *args,
            ]
        ),
        "codex_config_toml": codex,
        "instructions": "Claude: place .mcp.json in your agent workspace and approve the server. "
        "Codex: paste the TOML block into ~/.codex/config.toml or the trusted "
        "workspace's .codex/config.toml. No global install is needed.",
        "sources": [
            "https://code.claude.com/docs/en/mcp",
            "https://developers.openai.com/codex/mcp",
        ],
    }
    if write:
        import tomllib

        write = write.expanduser().resolve()
        write.mkdir(parents=True, exist_ok=True)
        claude_path = write / ".mcp.json"
        existing = json.loads(claude_path.read_text("utf-8")) if claude_path.exists() else {}
        existing.setdefault("mcpServers", {})["muestra"] = claude["mcpServers"]["muestra"]
        codex_path = write / ".codex/config.toml"
        codex_config = tomllib.loads(codex_path.read_text("utf-8")) if codex_path.exists() else {}
        codex_config.setdefault("mcp_servers", {})["muestra"] = tomllib.loads(codex)["mcp_servers"][
            "muestra"
        ]
        claude_path.write_text(
            json.dumps(existing, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        codex_path.parent.mkdir(parents=True, exist_ok=True)
        codex_path.write_text(tomli_w.dumps(codex_config), encoding="utf-8", newline="\n")
        result["written"] = [str(claude_path), str(codex_path)]
    return result
