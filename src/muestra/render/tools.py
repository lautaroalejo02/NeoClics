"""Tool stubs owned by WP2 (render): preview and render."""

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
]

_WP = "WP2"


class PreviewParams(ProjectScoped):
    target: str = Field(
        description="What to preview: 'mix', 'chop:<id>', 'track:<id>', "
        "or 'clip:<track_id>:<index>'."
    )
    bars: float | None = Field(
        default=None, gt=0, description="Max length in bars (default: whole target)."
    )


class PreviewResult(ToolOutput):
    target: str
    preview_path: RelPath = Field(description="cache/previews/... audio path.")


def preview(p: PreviewParams) -> PreviewResult:
    """Render a short preview of a chop, clip, track or the mix."""
    raise NotImplementedError(_WP)


class RenderParams(ProjectScoped):
    range_bars: tuple[float, float] | None = Field(
        default=None,
        description="[start_bar, end_bar] to render (1-indexed); default: whole song.",
    )
    stems: bool = Field(default=True, description="Also render per-track stems.")


class RenderResult(ToolOutput):
    version: VersionStr
    mix_path: RelPath
    stem_paths: dict[IdStr, RelPath] = Field(default_factory=dict)
    meta_path: RelPath
    meta: RenderMeta
    metrics_path: RelPath | None = None
    metrics: Metrics | None = None


def render(p: RenderParams) -> RenderResult:
    """Render the song to renders/vNNN/ (mix + stems + meta.json), commit it,
    and run ears to produce metrics.json (spec section 8)."""
    raise NotImplementedError(_WP)
