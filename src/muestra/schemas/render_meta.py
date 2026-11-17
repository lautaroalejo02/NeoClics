"""renders/vNNN/meta.json contract (spec 7.4) — one per render version."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from ._base import ContractModel, VersionStr

__all__ = ["RenderMeta", "DiffEntry"]


class DiffEntry(ContractModel):
    """One changed value in the musical diff between versions.

    ``path`` uses dotted/bracketed song.yaml addressing, e.g.
    ``chops[0].warp.pitch_semitones`` or ``tracks[1].fx[2].cutoff_hz``.
    ``from_`` serializes as ``from``.
    """

    path: str
    from_: Any = Field(alias="from", default=None)
    to: Any = None


class RenderMeta(ContractModel):
    schema_version: Literal[0] = 0
    version: VersionStr
    parent: VersionStr | None = None
    git_commit: str | None = None
    created_at: datetime
    request: str | None = Field(
        default=None,
        description="The natural-language request that produced this version.",
    )
    agent: str = Field(min_length=1, description="Agent/tool that rendered, e.g. 'claude-code'.")
    summary: str | None = Field(
        default=None,
        description="Musical (not technical) description of what changed.",
    )
    diff: list[DiffEntry] = Field(default_factory=list)
    render_time_s: float | None = Field(default=None, ge=0.0)
    duration_s: float | None = Field(default=None, gt=0.0)
