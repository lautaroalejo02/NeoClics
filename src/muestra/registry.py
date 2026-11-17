"""Single registry of every section-8 tool.

``REGISTRY`` maps tool names (``"source.find_chops"``) to a
:class:`ToolSpec` with the callable, its params model and its result
model. WP7 generates MCP tools, CLI commands and API routes mechanically
from this table; JSON Schemas for every params/result model are exported
to ``schemas/tools/`` (see ``muestra.schemas.export``).

Calling convention: ``fn(params) -> result`` where params is the params
model instance. ``run_tool`` wraps any tool into the ``ToolResult``
envelope.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from .ears.tools import EarsAnalyzeParams, EarsAnalyzeResult, ears_analyze
from .errors import Issue, MuestraError, ToolResult, issues_from_validation_error
from .feedback.tools import FeedbackReadParams, FeedbackReadResult, feedback_read
from .ingest.tools import (
    ChopsChooseParams,
    ChopsChooseResult,
    SourceAnalyzeParams,
    SourceAnalyzeResult,
    SourceFindChopsParams,
    SourceFindChopsResult,
    SourceImportParams,
    SourceImportResult,
    SourceSeparateParams,
    SourceSeparateResult,
    chops_choose,
    source_analyze,
    source_find_chops,
    source_import,
    source_separate,
)
from .library.tools import (
    LibraryIndexParams,
    LibraryIndexResult,
    LibrarySearchParams,
    LibrarySearchResult,
    library_index,
    library_search,
)
from .project.tools import (
    ProjectCreateParams,
    ProjectCreateResult,
    ProjectOpenParams,
    ProjectOpenResult,
    SongGetParams,
    SongGetResult,
    SongValidateParams,
    SongValidateResult,
    project_create,
    project_open,
    song_get,
    song_validate,
)
from .render.tools import (
    PreviewParams,
    PreviewResult,
    RenderParams,
    RenderResult,
    preview,
    render,
)
from .versions.tools import (
    VersionsDiffParams,
    VersionsDiffResult,
    VersionsListParams,
    VersionsListResult,
    VersionsRestoreParams,
    VersionsRestoreResult,
    versions_diff,
    versions_list,
    versions_restore,
)

__all__ = ["ToolSpec", "REGISTRY", "tool_spec", "run_tool"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    group: str
    func: Callable[[Any], Any]
    params: type[BaseModel]
    result: type[BaseModel] | None
    description: str


def _spec(
    name: str,
    func: Callable,
    params: type[BaseModel],
    result: type[BaseModel] | None,
) -> ToolSpec:
    doc = (func.__doc__ or "").strip().split("\n")[0]
    return ToolSpec(
        name=name,
        group=name.split(".", 1)[0],
        func=func,
        params=params,
        result=result,
        description=doc,
    )


#: name -> ToolSpec, in spec section-8 order.
REGISTRY: dict[str, ToolSpec] = {
    s.name: s
    for s in [
        _spec("project.create", project_create, ProjectCreateParams, ProjectCreateResult),
        _spec("project.open", project_open, ProjectOpenParams, ProjectOpenResult),
        _spec("source.import", source_import, SourceImportParams, SourceImportResult),
        _spec("source.separate", source_separate, SourceSeparateParams, SourceSeparateResult),
        _spec("source.analyze", source_analyze, SourceAnalyzeParams, SourceAnalyzeResult),
        _spec(
            "source.find_chops",
            source_find_chops,
            SourceFindChopsParams,
            SourceFindChopsResult,
        ),
        _spec("chops.choose", chops_choose, ChopsChooseParams, ChopsChooseResult),
        _spec("preview", preview, PreviewParams, PreviewResult),
        _spec("library.index", library_index, LibraryIndexParams, LibraryIndexResult),
        _spec("library.search", library_search, LibrarySearchParams, LibrarySearchResult),
        _spec("song.get", song_get, SongGetParams, SongGetResult),
        _spec("song.validate", song_validate, SongValidateParams, SongValidateResult),
        _spec("render", render, RenderParams, RenderResult),
        _spec("versions.list", versions_list, VersionsListParams, VersionsListResult),
        _spec("versions.diff", versions_diff, VersionsDiffParams, VersionsDiffResult),
        _spec(
            "versions.restore",
            versions_restore,
            VersionsRestoreParams,
            VersionsRestoreResult,
        ),
        _spec("feedback.read", feedback_read, FeedbackReadParams, FeedbackReadResult),
        _spec("ears.analyze", ears_analyze, EarsAnalyzeParams, EarsAnalyzeResult),
    ]
}


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
