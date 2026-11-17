"""Tools owned by WP5 (versioning): versions.list / get / diff / restore.

Backed by the git repo inside the project folder (see
``muestra.versions.core`` and ``docs/versions.md``): one version = one
commit + annotated tag ``vNNN``; ``renders/vNNN/song.yaml`` is the snapshot
source of truth, ``git show vNNN:song.yaml`` the fallback.
``versions.restore`` copies the snapshot BYTE-FOR-BYTE and never loses
uncommitted work.
"""

from __future__ import annotations

from pydantic import Field

from ..errors import MuestraError
from ..project.core import open_project
from ..schemas._base import VersionStr
from ..toolbase import ProjectScoped
from .core import restore_files, snapshot_bytes, song_at
from .describe import describe_song, musical_summary
from .diff import song_diff
from .metrics_delta import compare_metrics, ears_delta, load_metrics
from .models import (
    DiffItem,
    RenderMetaOut,
    VersionFeedback,
    VersionInfo,
    VersionsDiffResult,
    VersionsGetResult,
    VersionsListResult,
    VersionsRestoreResult,
)
from .records import build_record, build_records, load_meta, stems_on_disk

__all__ = [
    "VersionFeedback",
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


class VersionsListParams(ProjectScoped):
    pass


def versions_list(p: VersionsListParams) -> VersionsListResult:
    """List every version newest-first with request, summary, parent/children
    (history is a tree), render/re-render status and feedback."""
    project = open_project(p.project)
    records, roots, base = build_records(project)
    return VersionsListResult(versions=records, roots=roots, base=base)


class VersionsGetParams(ProjectScoped):
    version: VersionStr


def versions_get(p: VersionsGetParams) -> VersionsGetResult:
    """Return one version's merged record, meta, metrics and song.yaml text."""
    project = open_project(p.project)
    data, source = snapshot_bytes(project, p.version)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise MuestraError.one(
            "parse_error",
            f"song.yaml at {p.version} is not valid UTF-8.",
            value=p.version,
            hint="The snapshot is corrupted; compare renders/<v>/song.yaml "
            "with 'git show <v>:song.yaml'.",
        ) from e
    try:
        info: VersionInfo | None = build_record(project, p.version)
    except MuestraError:
        info = None  # e.g. no git: still serve the snapshot
    meta, _ = load_meta(project, p.version)
    meta_out = RenderMetaOut.model_validate(meta.model_dump(by_alias=True)) if meta else None
    mix = project.renders_dir / p.version / "mix.wav"
    return VersionsGetResult(
        version=p.version,
        meta=meta_out,
        song_yaml=text,
        song_source=source,  # type: ignore[arg-type]
        info=info,
        metrics=load_metrics(project, p.version),
        mix=mix.relative_to(project.root).as_posix() if mix.is_file() else None,
        stems=stems_on_disk(project, p.version),
    )


class VersionsDiffParams(ProjectScoped):
    a: VersionStr
    b: VersionStr


def versions_diff(p: VersionsDiffParams) -> VersionsDiffResult:
    """Compare two versions: ID-based structured diff plus a Spanish, producer-style
    description of every change (and a metrics delta when both have metrics)."""
    project = open_project(p.project)
    song_a, song_b = song_at(project, p.a), song_at(project, p.b)
    diff = song_diff(song_a, song_b)
    sentences = describe_song(song_a, song_b)
    if diff and not sentences:  # safety net: never describe a real change as "no changes"
        sentences = [f"Cambié {e.path} de {_short(e.from_)} a {_short(e.to)}" for e in diff[:8]] + (
            [f"y {len(diff) - 8} cambios más"] if len(diff) > 8 else []
        )
    metrics_a, metrics_b = load_metrics(project, p.a), load_metrics(project, p.b)
    structured = ears_delta(metrics_a, metrics_b)
    delta = structured.notes if structured is not None else compare_metrics(metrics_a, metrics_b)
    return VersionsDiffResult(
        a=p.a,
        b=p.b,
        diff=[DiffItem(path=e.path, **{"from": e.from_}, to=e.to) for e in diff],
        musical_summary=musical_summary(sentences, delta),
        changes=sentences,
        metrics_delta=delta,
        metrics_compare=structured,
        identical=not diff,
    )


def _short(v: object, limit: int = 60) -> str:
    s = repr(v)
    return s if len(s) <= limit else s[: limit - 1] + "…"


class VersionsRestoreParams(ProjectScoped):
    version: VersionStr = Field(description="Version whose song.yaml (and midi/) to restore.")


def versions_restore(p: VersionsRestoreParams) -> VersionsRestoreResult:
    """Put song.yaml (and midi/) back to a previous version, byte-for-byte,
    saving your uncommitted work first so the restore can be undone."""
    project = open_project(p.project)
    out = restore_files(project, p.version)
    root = f'git -C "{project.root}"'
    undo = (
        f"{root} restore --source={out.pre_restore_commit} -- {' '.join(out.undo_paths)}"
        if out.pre_restore_commit
        else None
    )
    parts = [
        f"song.yaml{' and midi/' if any(f.startswith('midi/') for f in out.files) else ''} "
        f"now match {p.version} (from {out.snapshot_source}); nothing was rendered.",
        f"Run render to create the next version: its parent will be {p.version}.",
    ]
    if out.snapshot_commit:
        parts.append(
            f"Your uncommitted edits were saved first in commit {out.snapshot_commit[:10]} "
            "(not lost)."
        )
    if undo:
        parts.append(f"To undo the restore: {undo}")
    return VersionsRestoreResult(
        version=p.version,
        restored_from=p.version,
        changed=out.changed,
        snapshot_source=out.snapshot_source,  # type: ignore[arg-type]
        previous_commit=out.previous_commit,
        snapshot_commit=out.snapshot_commit,
        restore_commit=out.restore_commit,
        undo=undo,
        hint=" ".join(parts),
        warnings=out.warnings,
        files=[f for f in out.files if (project.root / f).exists() or f == "song.yaml"],
    )


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
