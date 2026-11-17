"""Editor tools (WP16): song.edit, song.state, song.pieces.

``song.edit`` is how the browser editor (and any agent that prefers structured
ops to hand-editing YAML) changes the working song. It never creates a
version: ``render`` does that ("Guardar versión" in the UI).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, TypeAdapter

from ..errors import MuestraError
from ..project import open_project
from ..schemas.song import Song
from ..toolbase import ProjectScoped, ToolOutput
from .ops import EditOp, PrimitiveOp

__all__ = ["TOOLS", "SongEditParams", "SongEditResult", "SongStateParams", "SongStateResult"]

_OPS = TypeAdapter(list[EditOp])


class SongEditParams(ProjectScoped):
    ops: list[dict[str, Any]] = Field(
        min_length=1,
        description="Edit operations applied atomically, in order. Primitive: "
        "{op:set,path,value,expect?} {op:unset,path} {op:insert,path,index?,value} "
        "{op:remove,path,expect?} {op:move,path,to}. Semantic: {op:hit,track,lane,bar,step,"
        "velocity|null} {op:add_hits,track,hits:[{beat,lane,velocity?,sample?}],quantize?} "
        "{op:split_clip,track,clip,at_beat} {op:duplicate_clip,track,clip,at_bar?} "
        "{op:add_point,target,bar,value,curve?} {op:revert_to,version}. Paths are id-based "
        "like versions.diff: tracks.drums.pattern.lanes.kick.overrides.7, "
        "tracks.sample.clips[0].instances.3.mute (indices 0-based).",
    )
    base_revision: str | None = Field(
        default=None,
        description="song.state revision the ops were built against; if the file changed "
        "since, ops are re-applied when every 'expect' still holds (rebased) else edit_conflict.",
    )
    dry_run: bool = Field(default=False, description="Validate and describe without writing.")


class SongEditResult(ToolOutput):
    changes: list[str] = Field(
        default_factory=list, description="What changed, in Spanish (musical terms)."
    )
    revision: str = Field(description="Revision of song.yaml after the edit.")
    base_revision: str = Field(description="Revision the ops were applied to.")
    rebased: bool = False
    changed: bool = True
    applied: list[PrimitiveOp] = Field(default_factory=list)
    inverse: list[PrimitiveOp] = Field(
        default_factory=list,
        description="Ops that undo this edit (send with base_revision=revision).",
    )
    song: Song


def song_edit(p: SongEditParams) -> SongEditResult:
    """Edit the working song.yaml with structured ops (atomic, validated, undoable)."""
    from pydantic import ValidationError

    from ..errors import issues_from_validation_error
    from .core import apply_edit, revert_ops

    project = open_project(p.project)
    try:
        ops = _OPS.validate_python(p.ops)
    except ValidationError as exc:
        raise MuestraError(
            issues_from_validation_error(exc, path_prefix="ops"),
            hint="Fix the op list (see the tool description for the op shapes).",
        ) from exc
    snapshot = None
    expanded = []
    for op in ops:
        if op.op == "revert_to":
            doc_ops, snapshot = revert_ops(project, op.version)
            expanded += doc_ops
        else:
            expanded.append(op)
    out = apply_edit(
        project, expanded, base_revision=p.base_revision, dry_run=p.dry_run, snapshot=snapshot
    )
    return SongEditResult(
        revision=out.revision,
        base_revision=out.base_revision,
        rebased=out.rebased,
        changed=out.changed,
        applied=out.applied,
        inverse=out.inverse,
        song=out.song,
        changes=out.changes,
        files=["song.yaml"] if out.changed and not p.dry_run else [],
    )


class SongStateParams(ProjectScoped):
    against: str | None = Field(
        default=None,
        description="Version to compare the working song with (default: the version the "
        "next render descends from).",
    )


class SongStateResult(ToolOutput):
    revision: str
    base_version: str | None = Field(
        default=None, description="Version the working song is compared with."
    )
    dirty: bool = Field(description="The working song differs musically from base_version.")
    changes: list[str] = Field(
        default_factory=list, description="Spanish description of the unsaved changes."
    )
    summary: str | None = Field(
        default=None, description="Suggested version summary ('Edición manual: ...')."
    )
    song: Song | None = None
    issues: list = Field(default_factory=list)


def song_state(p: SongStateParams) -> SongStateResult:
    """The working song with its revision, and what changed since the last version
    (Spanish), for the editor's 'cambios sin guardar' and the version summary."""
    from ..project.validate import load_song
    from ..versions import core as versions
    from ..versions.describe import describe_song
    from .core import current_revision

    project = open_project(p.project)
    revision = current_revision(project)
    try:
        song = load_song(project)
    except MuestraError as exc:
        return SongStateResult(revision=revision, dirty=True, issues=exc.issues)
    base = p.against or versions.suggest_parent(project)
    changes: list[str] = []
    dirty = False
    if base:
        try:
            changes = describe_song(versions.song_at(project, base), song)
            dirty = bool(changes)
        except MuestraError:
            base = None
    return SongStateResult(
        revision=revision,
        base_version=base,
        dirty=dirty if base else True,
        changes=changes,
        summary=manual_summary(changes) if changes else None,
        song=song,
    )


def manual_summary(changes: list[str], limit: int = 6) -> str:
    """'Edición manual: saqué 2 golpes de bombo en el compás 7; moví el clip …'."""
    parts = [c[:1].lower() + c[1:] for c in changes[:limit]]
    more = f"; y {len(changes) - limit} cambios más" if len(changes) > limit else ""
    return "Edición manual: " + "; ".join(parts) + more


def _pieces(*args, **kwargs):
    """Audio pieces of the working song for the browser editor: chop materials, drum
    one-shots, instrument dry stems and (wet=true) per-track renders with fx, cached
    by content under cache/previews/pieces/."""
    from .pieces import song_pieces

    return song_pieces(*args, **kwargs)


def _tools():
    from .pieces import SongPiecesParams, SongPiecesResult

    return [
        (
            "song.edit",
            song_edit,
            SongEditParams,
            SongEditResult,
            {"cli": ("song", "edit")},
        ),
        (
            "song.state",
            song_state,
            SongStateParams,
            SongStateResult,
            {"readonly": True, "cli": ("song", "state")},
        ),
        (
            "song.pieces",
            _pieces,
            SongPiecesParams,
            SongPiecesResult,
            {"cli": ("song", "pieces")},
        ),
    ]


TOOLS = _tools()
