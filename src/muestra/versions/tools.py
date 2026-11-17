"""Tool stubs owned by WP5 (versioning): versions.list / get / diff /
restore.

Backed by the git repo inside the project folder (see
``muestra.versions.core``): one version = one commit + annotated tag
``vNNN``; ``renders/vNNN/song.yaml`` is the snapshot source of truth,
``git show vNNN:song.yaml`` the fallback. ``versions.restore`` copies
the snapshot text BYTE-FOR-BYTE.
"""

from __future__ import annotations

from pydantic import AwareDatetime, Field

from ..schemas._base import ContractModel, VersionStr
from ..schemas.render_meta import DiffEntry, RenderMeta
from ..toolbase import ProjectScoped, ToolOutput

__all__ = [
    "VersionInfo",
    "VersionsListParams",
    "VersionsListResult",
    "VersionsGetParams",
    "VersionsGetResult",
    "VersionsDiffParams",
    "VersionsDiffResult",
    "VersionsRestoreParams",
    "VersionsRestoreResult",
    "versions_list",
    "versions_get",
    "versions_diff",
    "versions_restore",
    "TOOLS",
]

_WP = "WP5"


class VersionInfo(ContractModel):
    version: VersionStr
    parent: VersionStr | None = None
    created_at: AwareDatetime | None = None
    summary: str | None = None
    git_commit: str | None = None
    liked: bool | None = Field(default=None, description="Latest rating for this version, if any.")


class VersionsListParams(ProjectScoped):
    pass


class VersionsListResult(ToolOutput):
    versions: list[VersionInfo] = Field(default_factory=list)


def versions_list(p: VersionsListParams) -> VersionsListResult:
    """List render versions newest-first, with ratings."""
    raise NotImplementedError(_WP)


class VersionsGetParams(ProjectScoped):
    version: VersionStr


class VersionsGetResult(ToolOutput):
    version: VersionStr
    meta: RenderMeta | None = Field(
        default=None,
        description="renders/<version>/meta.json when it exists (derived data — git-ignored).",
    )
    song_yaml: str = Field(
        description="The version's song.yaml text: renders/<version>/song.yaml "
        "byte copy when present, else 'git show vNNN:song.yaml'."
    )


def versions_get(p: VersionsGetParams) -> VersionsGetResult:
    """Return one version's meta plus its song.yaml snapshot text."""
    raise NotImplementedError(_WP)


class VersionsDiffParams(ProjectScoped):
    a: VersionStr
    b: VersionStr


class VersionsDiffResult(ToolOutput):
    a: VersionStr
    b: VersionStr
    diff: list[DiffEntry] = Field(
        default_factory=list,
        description="Changed fields; ID-based song.yaml paths "
        "(chops.c1.warp.pitch_semitones, tracks.drums.pattern.lanes.kick.steps, "
        "tracks.<id>.clips[<i>], fx[<i>] for id-less fx).",
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
    """Restore song.yaml to a previous version's state — the snapshot
    text is copied BYTE-FOR-BYTE (comments/ordering preserved)."""
    raise NotImplementedError(_WP)


#: tools exported by this subpackage (collected by muestra.registry).
TOOLS = [
    (
        "versions.list",
        versions_list,
        VersionsListParams,
        VersionsListResult,
        {"readonly": True, "cli": ("versions", "list")},
    ),
    (
        "versions.get",
        versions_get,
        VersionsGetParams,
        VersionsGetResult,
        {"readonly": True, "cli": ("versions", "get")},
    ),
    (
        "versions.diff",
        versions_diff,
        VersionsDiffParams,
        VersionsDiffResult,
        {"readonly": True, "cli": ("versions", "diff")},
    ),
    (
        "versions.restore",
        versions_restore,
        VersionsRestoreParams,
        VersionsRestoreResult,
        {"cli": ("versions", "restore")},
    ),
]
