"""HTTP API contracts (WP7): jobs and websocket events.

The API serves exactly ONE project per server process
(``muestra serve --project <path>``); routes never carry the project path.

* Tool routes: ``POST /tools/<mcp_name>`` with the tool's params JSON
  body (``GET /tools/<mcp_name>`` for ``readonly`` tools, params as
  query). ``long_running`` tools return ``{"job_id": ...}`` immediately;
  ``GET /jobs/<id>`` returns a :class:`Job`. Over MCP and CLI,
  long-running tools simply block.
* Static audio: ``GET /files/<project-relative path>`` with HTTP Range
  support, restricted to ``renders/`` and ``cache/previews/``.
* Events: websocket ``/events`` streams :class:`WsEvent` messages. The
  API server produces them by WATCHING the project folder (``renders/``,
  ``cache/chops/``, ``feedback.jsonl``, ``song.yaml``), because renders
  are usually triggered from a separate MCP process.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, Field

from ._base import ContractModel

__all__ = [
    "Job",
    "JobStatus",
    "WsEvent",
    "WsEventType",
]

JobStatus = Literal["queued", "running", "done", "failed"]


class Job(ContractModel):
    """State of a long-running tool invocation (``GET /jobs/<id>``)."""

    id: str = Field(min_length=1)
    tool: str = Field(description="Tool name, e.g. 'source.separate'.")
    status: JobStatus
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    message: str | None = None
    result: dict[str, Any] | None = Field(
        default=None,
        description="The tool's ToolResult envelope once done/failed.",
    )


#: websocket event types emitted on /events.
WsEventType = Literal[
    "job.updated",
    "version.created",
    "candidates.created",
    "feedback.appended",
    "song.changed",
    "grid.changed",
]


class WsEvent(ContractModel):
    """One websocket message on /events.

    ``data`` is type-specific: e.g. a Job for ``job.updated``, a
    ``{version, meta}`` pair for ``version.created``, a
    ``{set_id, source_id}`` pair for ``candidates.created``, the event
    line for ``feedback.appended``, ``{}`` for ``song.changed``,
    ``{source_id}`` for ``grid.changed`` (WP12: source.regrid rewrote the
    analysis or source.preview_grid wrote click previews).
    """

    type: WsEventType
    ts: AwareDatetime
    data: dict[str, Any] = Field(default_factory=dict)
