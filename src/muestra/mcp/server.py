"""Official MCP SDK 2.x server, generated from the decentralized registry."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ConfigDict

from mcp import types

from ..api.core import (
    REGISTRY,
    Runtime,
    compact_result,
    failure,
    input_schema,
    normalized_progress,
    spec_by_mcp_name,
    tool_description,
)
from ..errors import MuestraError, ToolResult


def guide() -> str:
    return files("muestra.mcp").joinpath("guide.md").read_text(encoding="utf-8")


class GuideParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def create_server(project: str | Path | None = None) -> Server:
    runtime = Runtime(project)
    feedback_cursor = 0

    async def list_tools(ctx, params):
        tools = [
            types.Tool(
                name=spec.mcp_name,
                description=tool_description(spec),
                input_schema=input_schema(spec, project_default=True),
                output_schema=ToolResult.model_json_schema(),
                annotations=types.ToolAnnotations(
                    read_only_hint=spec.readonly, open_world_hint=False
                ),
            )
            for spec in REGISTRY.values()
        ]
        tools.append(
            types.Tool(
                name="muestra_guide",
                description="Read the Muestra workflow first: "
                "project, sources, chop selection, song edits, render and feedback.",
                input_schema=GuideParams.model_json_schema(),
                output_schema=ToolResult.model_json_schema(),
                annotations=types.ToolAnnotations(read_only_hint=True),
            )
        )
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx, params):
        nonlocal feedback_cursor
        arguments = params.arguments or {}
        acted_on = runtime.project
        if params.name == "muestra_guide":
            if arguments:
                result = failure(
                    "muestra.guide",
                    "unknown_field",
                    "Guide takes no parameters.",
                    "Call muestra_guide with {}.",
                )
            else:
                result = ToolResult(tool="muestra.guide", ok=True, data={"guide": guide()})
        else:
            try:
                spec = spec_by_mcp_name(params.name)
            except MuestraError as exc:
                result = ToolResult(tool=params.name, ok=False, errors=exc.issues, hint=exc.hint)
            else:

                def progress(value: float, message: str | None = None):
                    anyio.from_thread.run(
                        ctx.session.report_progress, normalized_progress(value), 1.0, message
                    )

                result, acted_on = await anyio.to_thread.run_sync(
                    lambda: runtime.call_with_project(spec, arguments, progress=progress)
                )
                if result.ok and spec.name in ("project.create", "project.open"):
                    feedback_cursor = 0
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=compact_result(result, acted_on))],
            structured_content=result.model_dump(mode="json"),
            is_error=not result.ok,
            meta={"project": str(acted_on) if acted_on else None},
        )

    async def list_prompts(ctx, params):
        return types.ListPromptsResult(
            prompts=[
                types.Prompt(
                    name="muestra.guide",
                    description="How to make music and iterate with a human in Muestra.",
                )
            ]
        )

    async def get_prompt(ctx, params):
        if params.name != "muestra.guide":
            raise ValueError("Unknown prompt; use muestra.guide.")
        return types.GetPromptResult(
            description="Muestra workflow",
            messages=[
                types.PromptMessage(
                    role="user", content=types.TextContent(type="text", text=guide())
                )
            ],
        )

    async def list_resources(ctx, params):
        return types.ListResourcesResult(
            resources=[
                types.Resource(uri=f"muestra://{name}", name=title, mime_type=mime)
                for name, title, mime in (
                    ("song", "Current song.yaml", "application/yaml"),
                    ("metrics/latest", "Latest render metrics", "application/json"),
                    (
                        "feedback/unread",
                        "Unread feedback (cursor advances on resource read)",
                        "application/json",
                    ),
                )
            ]
        )

    async def read_resource(ctx, params):
        nonlocal feedback_cursor
        root = runtime.project
        uri = str(params.uri)
        if uri == "muestra://feedback/unread":
            result = await anyio.to_thread.run_sync(
                lambda: runtime.call(REGISTRY["feedback.read"], {"cursor": feedback_cursor})
            )
            if result.ok:
                feedback_cursor = result.data["next_cursor"]
            text = compact_result(result, root)
            mime = "application/json"
            # Resource JSON must itself be JSON, unlike compact tool text.
            text = json.dumps(
                {"project": str(root) if root else None, "result": result.model_dump(mode="json")}
            )
        elif root and uri in ("muestra://song", "muestra://metrics/latest"):
            if uri == "muestra://song":
                path, mime = root / "song.yaml", "application/yaml"
            else:
                paths = sorted(
                    (root / "renders").glob("v*/metrics.json"),
                    key=lambda p: int(p.parent.name[1:]) if p.parent.name[1:].isdigit() else -1,
                )
                path, mime = paths[-1] if paths else None, "application/json"
            if path and path.is_file():
                text = await anyio.to_thread.run_sync(lambda: path.read_text("utf-8"))
            else:
                text = json.dumps(
                    {"project": str(root), "hint": "No render metrics yet; render first."}
                )
        else:
            text, mime = (
                json.dumps({"hint": "Open a project first; then list resources."}),
                "application/json",
            )
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=params.uri, mime_type=mime, text=text)]
        )

    return Server(
        "Muestra",
        version="0.0.0",
        instructions=guide(),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_prompts=list_prompts,
        on_get_prompt=get_prompt,
        on_list_resources=list_resources,
        on_read_resource=read_resource,
    )


async def _stdio(server: Server):
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run_server(project=None, *, transport="stdio", host="127.0.0.1", port=8766):
    server = create_server(project)
    if transport == "stdio":
        anyio.run(_stdio, server)
    else:
        import sys

        import uvicorn

        if host not in ("127.0.0.1", "localhost", "::1"):
            print(
                "Warning: Muestra MCP has no authentication. Use only on a trusted network.",
                file=sys.stderr,
            )
        app = server.streamable_http_app(host=host)
        uvicorn.run(app, host=host, port=port)
