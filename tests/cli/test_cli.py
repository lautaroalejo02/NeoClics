from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from muestra.cli.main import emit
from muestra.cli.setup import agent_setup
from muestra.registry import REGISTRY

REPO = Path(__file__).resolve().parents[2]


def cli(*args, cwd=REPO, input=None, env=None, fake=False):
    return subprocess.run(
        [sys.executable, "-m", "tests.mcp.host" if fake else "muestra.cli.main", *map(str, args)],
        cwd=cwd,
        input=input,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        env={**os.environ, "PYTHONUTF8": "1", "MUESTRA_PROJECT": "", **(env or {})},
    )


def test_cli_project_workflow_flags_stdin_and_errors(tmp_path):
    created = cli("project", "create", "--name", "cli-song", "--dir", tmp_path)
    assert created.returncode == 0, created.stderr + created.stdout
    root = tmp_path / "cli-song"
    assert json.loads(created.stdout)["data"]["project_path"] == str(root)
    valid = cli("--project", root, "song", "validate", "--no-check-files")
    assert valid.returncode == 0 and json.loads(valid.stdout)["data"]["valid"]
    feedback = cli(
        "feedback",
        "append",
        "--project",
        root,
        "--event.type",
        "comment",
        "--event.version",
        "v001",
        "--event.at-s",
        "1.5",
        "--event.text",
        "Más bajo 🎶",
    )
    assert feedback.returncode == 0, feedback.stdout + feedback.stderr
    piped = cli(
        "feedback",
        "append",
        "--project",
        root,
        "--params-json",
        "-",
        input=json.dumps({"event": {"type": "rating", "version": "v001", "value": "like"}}),
    )
    assert piped.returncode == 0, piped.stdout
    read = cli(
        "feedback", "read", "--project", root, "--params-json", '{"cursor":99}', "--cursor", "0"
    )
    assert json.loads(read.stdout)["data"]["count"] == 2
    assert json.loads(read.stdout)["data"]["events"][0]["text"] == "Más bajo 🎶"
    nested = root / "sub/directory"
    nested.mkdir(parents=True)
    nearest = cli("song", "get", cwd=nested)
    assert nearest.returncode == 0
    from_env = cli("song", "get", env={"MUESTRA_PROJECT": str(root)}, cwd=tmp_path)
    assert from_env.returncode == 0
    # The renderer is integrated now: a real render through the CLI succeeds.
    rendered = cli("render", "--project", root, "--range-bars", "[1,3]", "--no-stems")
    assert rendered.returncode == 0, rendered.stdout
    assert json.loads(rendered.stdout)["ok"]
    missing = cli("versions", "get", "--project", root, "--version", "v999")
    assert missing.returncode != 0 and not json.loads(missing.stdout)["ok"]
    bad = cli("feedback", "append", "--project", root, "--params-json", "[]")
    assert bad.returncode != 0 and not json.loads(bad.stdout)["ok"]
    with (root / "song.yaml").open("a", encoding="utf-8") as file:
        file.write("\nunknown_field: true\n")
    invalid_song = cli("song", "validate", "--project", root)
    assert invalid_song.returncode == 1
    assert not json.loads(invalid_song.stdout)["data"]["valid"]


def test_cli_fake_tools_are_generated_and_crashes_are_private(tmp_project):
    response = cli("future", "echo", "--project", tmp_project.root, "--value", "33", fake=True)
    assert response.returncode == 0 and json.loads(response.stdout)["data"]["value"] == 33
    slow = cli("future", "slow", "--project", tmp_project.root, "--value", "44", fake=True)
    assert slow.returncode == 0 and json.loads(slow.stdout)["data"]["value"] == 44
    crash = cli("future", "crash", "--project", tmp_project.root, fake=True)
    assert crash.returncode == 1
    assert "PRIVATE_TOKEN" not in crash.stdout + crash.stderr
    assert json.loads(crash.stdout)["errors"][0]["code"] == "internal_error"
    missing = cli(
        "future",
        "echo",
        "--project",
        tmp_project.root,
        "--params-json",
        '{"value":"bad"}',
        fake=True,
    )
    assert missing.returncode == 1 and json.loads(missing.stdout)["errors"][0]["path"] == "value"


def test_tools_doctor_and_setup(tmp_project, tmp_path):
    listing = cli("tools")
    assert {t["name"] for t in json.loads(listing.stdout)["tools"]} == set(REGISTRY)
    diagnosis = cli("doctor", "--project", tmp_project.root)
    checks = json.loads(diagnosis.stdout)["checks"]
    names = {c["name"].split(":")[0] for c in checks}
    assert {"ffmpeg", "git", "python-core", "python-separation", "model", "ui"} <= names
    assert {"project", "library", "plugin", "port"} <= names
    assert all(c["fix"] for c in checks if c["status"] != "ok")
    assert {c["status"] for c in checks} <= {"ok", "missing", "optional"}
    workspace = tmp_path / "agent workspace"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text('{"mcpServers":{"other":{"command":"other"}}}', "utf-8")
    (workspace / ".codex").mkdir()
    (workspace / ".codex/config.toml").write_text('model = "keep-me"\n', "utf-8")
    setup = cli("agent-setup", "--project", tmp_project.root, "--write", workspace)
    assert setup.returncode == 0, setup.stdout
    result = json.loads(setup.stdout)
    claude = json.loads((workspace / ".mcp.json").read_text("utf-8"))
    codex = tomllib.loads((workspace / ".codex/config.toml").read_text("utf-8"))
    assert "other" in claude["mcpServers"] and codex["model"] == "keep-me"
    entry = claude["mcpServers"]["muestra"]
    assert entry["type"] == "stdio"
    assert entry["args"][:3] == ["run", "--directory", str(REPO)]
    assert entry["args"][-2:] == ["--project", str(tmp_project.root)]
    assert codex["mcp_servers"]["muestra"]["args"] == entry["args"]
    assert "claude mcp add --transport stdio --scope project muestra --" in result["claude_command"]
    assert agent_setup(REPO, tmp_project.root)["codex_config_toml"].startswith(
        "[mcp_servers.muestra]"
    )


def test_tty_readable_output_and_explicit_json(monkeypatch):
    class TTY(io.StringIO):
        def isatty(self):
            return True

    output = TTY()
    monkeypatch.setattr(sys, "stdout", output)
    emit(
        {
            "tool": "fake",
            "ok": False,
            "errors": [
                {
                    "path": "meta.bpm",
                    "code": "too_small",
                    "message": "Tempo too low",
                    "hint": "Choose a positive BPM",
                }
            ],
        }
    )
    assert "meta.bpm: [too_small]" in output.getvalue()
    assert "Fix: Choose a positive BPM" in output.getvalue()
    output.seek(0)
    output.truncate()
    emit({"ok": True, "tool": "fake"}, force_json=True)
    assert json.loads(output.getvalue())["ok"]
