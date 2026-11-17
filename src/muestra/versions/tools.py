"""Tool stubs owned by WP5 (versioning): versions.list / diff / restore."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ..schemas._base import ContractModel, VersionStr
from ..schemas.render_meta import DiffEntry
from ..toolbase import ProjectScoped, ToolOutput

__all__ = [
    "VersionInfo",
    "VersionsListParams",
    "VersionsListResult",
    "VersionsDiffParams",
    "VersionsDiffResult",
    "VersionsRestoreParams",
    "VersionsRestoreResult",
    "versions_list",
    "versions_diff",
    "versions_restore",
]

_WP = "WP5"


class VersionInfo(ContractModel):
    version: VersionStr
    parent: VersionStr | None = None
    created_at: datetime | None = None
    summary: str | None = None
    liked: bool | None = Field(default=None, description="Latest rating for this version, if any.")


class VersionsListParams(ProjectScoped):
    pass


class VersionsListResult(ToolOutput):
    versions: list[VersionInfo] = Field(default_factory=list)


def versions_list(p: VersionsListParams) -> VersionsListResult:
    """List render versions newest-first, with ratings."""
    raise NotImplementedError(_WP)


class VersionsDiffParams(ProjectScoped):
    a: VersionStr
    b: VersionStr


class VersionsDiffResult(ToolOutput):
    a: VersionStr
    b: VersionStr
    diff: list[DiffEntry] = Field(
        default_factory=list, description="Changed fields, song.yaml addressing."
    )
    musical_summary: str | None = Field(
        default=None, description="Human-readable musical diff (WP5)."
    )


def versions_diff(p: VersionsDiffParams) -> VersionsDiffResult:
    """Compare two versions' song.yaml state and describe the musical diff."""
    raise NotImplementedError(_WP)


class VersionsRestoreParams(ProjectScoped):
    version: VersionStr


class VersionsRestoreResult(ToolOutput):
    version: VersionStr
    restored_from: VersionStr


def versions_restore(p: VersionsRestoreParams) -> VersionsRestoreResult:
    """Restore song.yaml to a previous version's state."""
    raise NotImplementedError(_WP)
