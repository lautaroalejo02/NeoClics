"""Registry-generated CLI. Stdout is JSON when piped; MCP stdout is protocol only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..api.core import REGISTRY, Runtime, failure, resolve_project
from ..errors import MuestraError, ToolResult
from .params import ParamsJsonError, add_tool_arguments, collect_params, read_params_json


def _common(parser):
    parser.add_argument(
        "--project",
        default=argparse.SUPPRESS,
        help="Project path; else MUESTRA_PROJECT or nearest parent muestra.toml.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print machine-readable JSON (automatic when piped).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="muestra", description="Muestra · music as code")
    _common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    groups = {}
    for spec in REGISTRY.values():
        group, action = spec.cli or (spec.group, spec.name.split(".")[-1])
        if action is None:
            leaf = commands.add_parser(group, help=spec.description)
        else:
            if group not in groups:
                parent = commands.add_parser(group)
                _common(parent)
                groups[group] = parent.add_subparsers(dest="action", required=True)
            leaf = groups[group].add_parser(
                action, help=spec.description, description=spec.description
            )
        _common(leaf)
        leaf.set_defaults(spec=spec)
        leaf.add_argument(
            "--params-json", help="JSON object; '-' reads stdin. Flags override JSON."
        )
        add_tool_arguments(leaf, spec)
    serve = commands.add_parser("serve", help="Open the local listening studio (HTTP API + UI).")
    _common(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", dest="open_browser")
    mcp = commands.add_parser("mcp", help="Start the MCP server; stdio by default.")
    _common(mcp)
    mcp.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=8766)
    doctor = commands.add_parser("doctor", help="Check local dependencies and print fixes.")
    _common(doctor)
    doctor.add_argument("--port", type=int, default=8765)
    listing = commands.add_parser("tools", help="List every registered tool and its description.")
    _common(listing)
    setup = commands.add_parser("agent-setup", help="Print Claude Code / Codex MCP setup snippets.")
    _common(setup)
    setup.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    setup.add_argument(
        "--write",
        type=Path,
        metavar="WORKSPACE",
        help="Write project-local .mcp.json and .codex/config.toml here.",
    )
    return parser


def _dotted_flags(extras: list[str]) -> dict:
    """Accept --event.type comment / --adjust.start-beats 1 for nested objects."""
    result = {}
    index = 0
    while index < len(extras):
        flag = extras[index]
        if not flag.startswith("--") or "." not in flag:
            raise ParamsJsonError(f"Unknown argument {flag!r}. Use --help.")
        name, sep, raw = flag[2:].partition("=")
        if not sep:
            index += 1
            if index >= len(extras):
                raise ParamsJsonError(f"{flag} needs a value.")
            raw = extras[index]
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        parts = name.replace("-", "_").split(".")
        node = result
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ParamsJsonError(f"Conflicting flags for {name}.")
        node[parts[-1]] = value
        index += 1
    return result


def _merge(target: dict, updates: dict) -> dict:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value
    return target


def emit(value: dict, force_json: bool = False) -> None:
    if force_json or not sys.stdout.isatty():
        print(json.dumps(value, ensure_ascii=False))
        return
    if "checks" in value:
        for item in value["checks"]:
            print(f"{item['status'].upper():8} {item['name']}: {item['detail']}")
            if item["fix"]:
                print(f"         Fix: {item['fix']}")
    elif "claude_mcp_json" in value:
        print("Claude Code · .mcp.json\n" + json.dumps(value["claude_mcp_json"], indent=2))
        print("\nOr run:\n" + value["claude_command"])
        print("\nCodex · ~/.codex/config.toml\n" + value["codex_config_toml"])
        print(value["instructions"])
    elif "tools" in value:
        for item in value["tools"]:
            print(f"{item['name']:24} {item['description']}")
    elif "ok" in value:
        print(f"{value['tool']}: {'OK' if value['ok'] else 'FAILED'}")
        for issue in value.get("errors", []):
            print(f"  {issue['path'] or '<root>'}: [{issue['code']}] {issue['message']}")
            if issue.get("hint"):
                print(f"  Fix: {issue['hint']}")
        if value.get("data") is not None:
            print(json.dumps(value["data"], indent=2, ensure_ascii=False))
    else:
        print(json.dumps(value, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    # Windows console/pipes otherwise inherit a legacy code page, breaking Unicode feedback.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args, extras = parser.parse_known_args(argv)
    force_json = getattr(args, "json", False)
    try:
        project = resolve_project(getattr(args, "project", None))
        if getattr(args, "spec", None):
            spec = args.spec
            params = read_params_json(args.params_json) if args.params_json else {}
            _merge(params, collect_params(spec, args))
            _merge(params, _dotted_flags(extras))
            runtime = Runtime(project)
            result = runtime.call(spec, params)
            emit(result.model_dump(mode="json"), force_json)
            return 0 if result.ok and (result.data or {}).get("valid", True) is not False else 1
        if extras:
            raise ParamsJsonError(f"Unknown arguments: {' '.join(extras)}")
        if args.command == "tools":
            emit(
                {
                    "tools": [
                        {
                            "name": s.name,
                            "mcp_name": s.mcp_name,
                            "description": s.description,
                            "readonly": s.readonly,
                            "long_running": s.long_running,
                        }
                        for s in REGISTRY.values()
                    ]
                },
                force_json,
            )
        elif args.command == "doctor":
            from .doctor import doctor

            result = doctor(project, args.port)
            emit(result, force_json)
            return 0 if result["ok"] else 1
        elif args.command == "agent-setup":
            from .setup import agent_setup

            emit(agent_setup(args.repo, project, args.write), force_json)
        elif args.command == "mcp":
            from ..mcp.server import run_server

            run_server(project, transport=args.transport, host=args.host, port=args.port)
        elif args.command == "serve":
            from ..api.server import serve

            serve(project, host=args.host, port=args.port, open_browser=args.open_browser)
        return 0
    except MuestraError as exc:
        emit(
            ToolResult(tool=args.command, ok=False, errors=exc.issues, hint=exc.hint).model_dump(
                mode="json"
            ),
            force_json,
        )
        return 2
    except (ParamsJsonError, ValueError, OSError, ImportError) as exc:
        emit(
            failure(
                args.command,
                "invalid_value",
                str(exc),
                "Use muestra --help; install server dependencies with uv sync --extra server.",
            ).model_dump(mode="json"),
            force_json,
        )
        return 2


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
