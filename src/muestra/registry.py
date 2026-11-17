"""Single registry of every section-8 tool — collected, not listed.

Tool registration is DECENTRALISED: each subpackage exposes a ``TOOLS``
list in its own ``tools.py`` of ``(name, func, params, result, flags)``
tuples; this module only collects them from a fixed list of
subpackages. WPs add tools by editing their own subpackage — this file
never needs to change for that (merge-safe).

``REGISTRY`` maps tool names (``"source.find_chops"``) to a
:class:`ToolSpec` with the callable, its params/result models and the
routing flags WP7 generates interfaces from:

* ``mcp_name`` — the MCP tool name: dots replaced by underscores
  (``source_find_chops``);
* ``readonly`` — the tool doesn't modify the project (HTTP GET route);
* ``long_running`` — over HTTP it returns ``{job_id}`` and progress via
  ``GET /jobs/<id>``; over MCP/CLI it simply blocks;
* ``cli`` — ``(group, action)``: ``("source", "import")`` means
  ``muestra source import``; a ``None`` action means the group IS the
  command (top-level ``muestra render`` / ``muestra preview``).

Calling convention: ``fn(params) -> result``. ``run_tool`` wraps any
tool into the ``ToolResult`` envelope.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from .errors import Issue, MuestraError, ToolResult, issues_from_validation_error

__all__ = ["ToolSpec", "REGISTRY", "tool_spec", "run_tool"]

_MCP_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    group: str
    func: Callable[[Any], Any]
    params: type[BaseModel]
    result: type[BaseModel] | None
    description: str
    mcp_name: str
    readonly: bool = False
    long_running: bool = False
    cli: tuple[str, str | None] | None = field(
        default=None,
        metadata={
            "doc": "(group, action); action None = the group is the "
            "top-level command ('render', 'preview')."
        },
    )


#: fixed list of subpackages whose tools.py TOOLS lists are collected.
_TOOL_PACKAGES = (
    "project",
    "ingest",
    "render",
    "library",
    "versions",
    "feedback",
    "ears",
)


def _mcp_name(name: str) -> str:
    """Tool name -> MCP name: dots to underscores, validated."""
    mcp = name.replace(".", "_")
    if not _MCP_NAME_RE.match(mcp):
        raise MuestraError.one(
            "internal_error",
            f"Tool name {name!r} produces invalid MCP name {mcp!r}.",
        )
    return mcp


def _collect() -> dict[str, ToolSpec]:
    registry: dict[str, ToolSpec] = {}
    for pkg in _TOOL_PACKAGES:
        module = __import__(f"muestra.{pkg}.tools", fromlist=["TOOLS"])
        for entry in module.TOOLS:
            name, func, params, result, *rest = entry
            flags = rest[0] if rest else {}
            doc = (func.__doc__ or "").strip().split("\n")[0]
            if name in registry:
                raise MuestraError.one(
                    "internal_error",
                    f"Duplicate tool registration {name!r}.",
                )
            registry[name] = ToolSpec(
                name=name,
                group=name.split(".", 1)[0],
                func=func,
                params=params,
                result=result,
                description=doc,
                mcp_name=_mcp_name(name),
                readonly=bool(flags.get("readonly", False)),
                long_running=bool(flags.get("long_running", False)),
                cli=flags.get("cli"),
            )
    return registry


#: name -> ToolSpec, collected from every tool subpackage's TOOLS.
REGISTRY: dict[str, ToolSpec] = _collect()


def tool_spec(name: str) -> ToolSpec:
    """Look up a tool spec; MuestraError if the name is unknown."""
    spec = REGISTRY.get(name)
    if spec is None:
        raise MuestraError.one(
            "unknown_tool",
            f"Unknown tool {name!r}.",
            value=name,
            hint="Known tools: " + ", ".join(REGISTRY),
        )
    return spec


def run_tool(name: str, params: dict[str, Any] | None = None) -> ToolResult:
    """Invoke a tool by name and wrap the outcome in the ToolResult envelope.

    Success: ok=True with the serialized result model in ``data`` and its
    ``files`` mirrored at top level. Failure: ok=False with all issues in
    ``errors`` and a short ``hint``.
    """
    try:
        spec = tool_spec(name)
    except MuestraError as e:
        return ToolResult(tool=name, ok=False, errors=e.issues, hint=e.hint)
    try:
        p = spec.params.model_validate(params or {})
    except ValidationError as e:
        return ToolResult(
            tool=name,
            ok=False,
            errors=issues_from_validation_error(e),
            hint="Fix the tool parameters and retry.",
        )
    try:
        result = spec.func(p)
    except MuestraError as e:
        return ToolResult(tool=name, ok=False, errors=e.issues, hint=e.hint)
    except NotImplementedError as e:
        return ToolResult(
            tool=name,
            ok=False,
            errors=[
                Issue(
                    code="not_implemented",
                    message=str(e) or f"{name} is not implemented yet.",
                    hint="This tool belongs to a later work package; see docs/spec.md section 12.",
                )
            ],
            hint="Not implemented yet.",
        )
    except Exception as e:  # noqa: BLE001 - the envelope must never leak a traceback
        return ToolResult(
            tool=name,
            ok=False,
            errors=[Issue(code="internal_error", message=f"{type(e).__name__}: {e}")],
            hint="Unexpected error; report it.",
        )
    data = result.model_dump(mode="json") if result is not None else None
    files = getattr(result, "files", []) or []
    return ToolResult(tool=name, ok=True, data=data, files=list(files))
