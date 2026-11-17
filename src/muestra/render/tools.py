"""Tool stubs owned by WP2 (render): preview and render.

The ``render`` tool orchestrates: validate -> allocate version id
(max+1) -> ``versions.core.record_version`` (commit + tag) -> render
audio -> write ``renders/vNNN/song.yaml`` (byte copy) + meta.json ->
``ears.core.analyze`` -> write metrics.json.
"""

from __future__ import annotations

from pydantic import Field

from ..schemas._base import IdStr, RelPath, VersionStr
from ..schemas.metrics import Metrics
from ..schemas.render_meta import RenderMeta
from ..toolbase import ProjectScoped, ToolOutput

__all__ = [
    "PreviewParams",
    "PreviewResult",
    "RenderParams",
    "RenderResult",
    "preview",
    "render",
    "TOOLS",
]

_WP = "WP2"


class PreviewParams(ProjectScoped):
    target: str = Field(
        description="What to preview: 'mix', 'chop:<id>', 'track:<id>', "
        "or 'clip:<track_id>:<index>' (0-based clip index)."
    )
    bars: float | None = Field(
        default=None, gt=0, description="Max length in bars (default: whole target)."
    )


class PreviewResult(ToolOutput):
    target: str
    preview_path: RelPath = Field(description="cache/previews/... audio path.")


def preview(p: PreviewParams) -> PreviewResult:
    """Render a short preview of a chop, clip, track or the mix.
    Plays at PROJECT tempo when a song.yaml exists, else at source
    tempo."""
    raise NotImplementedError(_WP)


class RenderParams(ProjectScoped):
    range_bars: tuple[int, int] | None = Field(
        default=None,
        description="[start_bar, end_bar) to render, 1-indexed, end "
        "EXCLUSIVE; must satisfy start < end <= length_bars + 1. "
        "Default: the whole song.",
    )
    stems: bool = Field(default=True, description="Also render per-track stems.")
    request: str | None = Field(
        default=None,
        description="The natural-language request that produced this "
        "version (commit trailer Muestra-Request).",
    )
    summary: str | None = Field(
        default=None,
        description="Musical description of what changed (commit subject 'vNNN: <summary>').",
    )
    agent: str | None = Field(
        default=None,
        description="Agent/tool identity (commit trailer Muestra-Agent).",
    )
    version: VersionStr | None = Field(
        default=None,
        description="Re-render an existing version from its tagged "
        "song.yaml into renders/<version>/ — no new commit.",
    )


class RenderResult(ToolOutput):
    version: VersionStr
    mix_path: RelPath
    stem_paths: dict[IdStr, RelPath] = Field(default_factory=dict)
    meta_path: RelPath
    meta: RenderMeta
    metrics_path: RelPath | None = None
    metrics: Metrics | None = None


def render(p: RenderParams) -> RenderResult:
    """Render the song to renders/vNNN/: record the version (git commit
    + tag), render mix + stems, write song.yaml/meta.json, run ears for
    metrics.json (spec section 8)."""
    raise NotImplementedError(_WP)


#: tools exported by this subpackage (collected by muestra.registry).
#: 'render' and 'preview' are TOP-LEVEL CLI commands.
TOOLS = [
    (
        "render",
        render,
        RenderParams,
        RenderResult,
        {"long_running": True, "cli": ("render", None)},
    ),
    (
        "preview",
        preview,
        PreviewParams,
        PreviewResult,
        {"long_running": True, "cli": ("preview", None)},
    ),
]
