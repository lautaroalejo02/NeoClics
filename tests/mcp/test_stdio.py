from __future__ import annotations

import json
import sys
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from muestra.registry import REGISTRY

REPO = Path(__file__).resolve().parents[2]


def test_sdk_stdio_end_to_end(tmp_path):
    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "tests.mcp.host", "mcp"],
            cwd=REPO,
            env={"PYTHONUTF8": "1", "MUESTRA_PROJECT": ""},
        )
        with anyio.fail_after(45):
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    assert initialized.server_info.name == "Muestra"
                    listing = await client.list_tools()
                    names = {t.name for t in listing.tools}
                    assert names >= {s.mcp_name for s in REGISTRY.values()}
                    assert {"test_slow", "test_echo", "muestra_guide"} <= names
                    song_tool = next(t for t in listing.tools if t.name == "song_validate")
                    assert "project" not in song_tool.input_schema.get("required", [])
                    assert "1-based" in initialized.instructions
                    assert "1-based" not in song_tool.description
                    assert song_tool.annotations.read_only_hint

                    created = await client.call_tool(
                        "project_create", {"name": "mcp-song", "dir": str(tmp_path)}
                    )
                    assert not created.is_error, created
                    project = tmp_path / "mcp-song"
                    assert created.structured_content["data"]["project_path"] == str(project)
                    assert created.meta["project"] == str(project)
                    assert str(project) in created.content[0].text
                    validation = await client.call_tool("song_validate", {})
                    assert validation.structured_content["data"]["valid"]
                    appended = await client.call_tool(
                        "feedback_append",
                        {
                            "event": {
                                "type": "comment",
                                "version": "v001",
                                "at_s": 1.25,
                                "text": "Más bajo 🎶",
                            }
                        },
                    )
                    assert not appended.is_error
                    feedback = await client.call_tool("feedback_read", {})
                    assert feedback.structured_content["data"]["events"][0]["text"] == "Más bajo 🎶"
                    bad = await client.call_tool("feedback_append", {"event": {"type": "invalid"}})
                    assert bad.is_error and bad.structured_content["errors"]
                    unknown = await client.call_tool("absent_tool", {})
                    assert unknown.is_error
                    stub = await client.call_tool("test_stub", {})
                    assert stub.is_error
                    assert stub.structured_content["errors"][0]["code"] == "not_implemented"
                    crashed = await client.call_tool("test_crash", {})
                    assert crashed.is_error
                    assert "PRIVATE_TOKEN" not in json.dumps(crashed.model_dump(mode="json"))
                    assert "PRIVATE_TOKEN" in (project / "cache/logs/muestra.log").read_text(
                        "utf-8"
                    )
                    notifications = []

                    async def progress(value, total, message):
                        notifications.append((value, total, message))

                    slow = await client.call_tool(
                        "test_slow", {"value": 7}, progress_callback=progress
                    )
                    assert slow.structured_content["data"]["value"] == 7
                    assert notifications == [
                        (0.25, 1.0, "Preparing audio"),
                        (0.75, 1.0, "Finishing"),
                    ]
                    prompts = await client.list_prompts()
                    assert prompts.prompts[0].name == "muestra.guide"
                    prompt = await client.get_prompt("muestra.guide")
                    assert "feedback_read" in prompt.messages[0].content.text
                    resources = await client.list_resources()
                    assert len(resources.resources) == 3
                    song = await client.read_resource("muestra://song")
                    assert "schema_version" in song.contents[0].text
                    unread = await client.read_resource("muestra://feedback/unread")
                    assert json.loads(unread.contents[0].text)["result"]["data"]["count"] == 1
                    unread = await client.read_resource("muestra://feedback/unread")
                    assert json.loads(unread.contents[0].text)["result"]["data"]["count"] == 0
                    await client.call_tool(
                        "project_create", {"name": "second", "dir": str(tmp_path)}
                    )
                    opened = await client.call_tool("project_open", {"path": str(project)})
                    assert opened.meta["project"] == str(project)
                    unread = await client.read_resource("muestra://feedback/unread")
                    assert json.loads(unread.contents[0].text)["result"]["data"]["count"] == 1

    anyio.run(scenario)


def test_generated_setup_command_stdio(tmp_path):
    """Exercise the exact uv invocation emitted for any external MCP client."""
    import shutil

    from muestra.cli.setup import agent_setup
    from muestra.project import create_project

    project = create_project(tmp_path / "external")
    config = agent_setup(REPO, project.root)["claude_mcp_json"]["mcpServers"]["muestra"]

    async def scenario():
        parameters = StdioServerParameters(
            command=shutil.which("uv") or config["command"],
            args=config["args"],
            cwd=tmp_path,
            env={"PYTHONUTF8": "1", "MUESTRA_PROJECT": ""},
        )
        with anyio.fail_after(45):
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    assert len((await client.list_tools()).tools) == len(REGISTRY) + 1
                    result = await client.call_tool("song_validate", {})
                    assert result.structured_content["data"]["valid"]

    anyio.run(scenario)
