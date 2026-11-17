"""Performance pads tools (WP17): pads.get / save_banks / take_save / take_get /
take_delete / commit / slice / from_track.

What the human plays on the computer keyboard (the UI's Pads view) is kept as
TAKES under ``pads/takes/`` (raw, latency compensated, quantise stored apart),
and becomes song material only through ``pads.commit`` (song.edit ops: a new
pads track or hits layered into a drum track; note pads -> instrument notes).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, ValidationError

from ..errors import MuestraError, issues_from_validation_error
from ..project import load_song, open_project
from ..schemas._base import ContractModel, IdStr
from ..schemas.pads import PAD_KEY_PATTERN, Pad, PadsFile, Take, TakeCommit, TakeQuantize
from ..schemas.song import LaneSlice, StemName, Warp
from ..toolbase import ProjectScoped, ToolOutput

__all__ = ["TOOLS"]

TakeIdStr = Field(pattern=r"^t[0-9]{3,}$")

#: UI key order (bottom row first): what pads.from_track fills by default
DEFAULT_KEYS = [
    *("KeyZ KeyX KeyC KeyV KeyB KeyN KeyM Comma".split()),
    *("KeyA KeyS KeyD KeyF KeyG KeyH KeyJ KeyK".split()),
    *("KeyQ KeyW KeyE KeyR KeyT KeyY KeyU KeyI".split()),
]


class TakeSummary(ContractModel):
    id: str
    name: str
    status: str
    bpm: float
    length_beats: float
    loop_beats: float | None = None
    origin_beat: float
    n_events: int
    pads: list[str] = Field(default_factory=list, description="Pad names played.")
    quantize: TakeQuantize
    created_at: datetime
    updated_at: datetime
    commits: int = 0


def _summary(t: Take) -> TakeSummary:
    used = sorted({e.pad for e in t.events})
    return TakeSummary(
        id=t.id,
        name=t.name,
        status=t.status,
        bpm=t.bpm,
        length_beats=t.length_beats,
        loop_beats=t.loop_beats,
        origin_beat=t.origin_beat,
        n_events=len(t.events),
        pads=[t.pads[k].name for k in used],
        quantize=t.quantize,
        created_at=t.created_at,
        updated_at=t.updated_at,
        commits=len(t.commits),
    )


# ---------------------------------------------------------------- pads.get


class PadsGetParams(ProjectScoped):
    pass


class PadsGetResult(ToolOutput):
    banks: PadsFile
    takes: list[TakeSummary] = Field(default_factory=list)
    problems: list[dict] = Field(default_factory=list)
    hint: str | None = None


def pads_get(p: PadsGetParams) -> PadsGetResult:
    """The project's pad banks and recorded takes (summaries; read one take's
    events with pads.take_get)."""
    from .store import list_takes, load_banks

    project = open_project(p.project)
    takes, problems = list_takes(project)
    hint = None
    if takes:
        live = [t.id for t in takes if t.status == "recording"]
        hint = (
            f"{len(takes)} takes. Read one with pads.take_get(take_id); turn it into song "
            "material with pads.commit(take_id, target=new_track|track, at_bar, repeat)."
            + (f" Still recording: {', '.join(live)}." if live else "")
        )
    return PadsGetResult(
        banks=load_banks(project), takes=[_summary(t) for t in takes], problems=problems, hint=hint
    )


# ---------------------------------------------------------------- banks


class PadsSaveBanksParams(ProjectScoped):
    banks: PadsFile


class PadsSaveBanksResult(ToolOutput):
    banks: PadsFile


def pads_save_banks(p: PadsSaveBanksParams) -> PadsSaveBanksResult:
    """Replace pads/banks.json (every bank, its pads and the active bank)."""
    from .store import BANKS_FILE, save_banks

    project = open_project(p.project)
    save_banks(project, p.banks)
    return PadsSaveBanksResult(banks=p.banks, files=[BANKS_FILE])


# ---------------------------------------------------------------- takes


class TakeSaveParams(ProjectScoped):
    take: dict[str, Any] = Field(
        description="A Take (pads/takes schema). Without 'id' a new take id is allocated; "
        "created_at/updated_at default to now."
    )


class TakeSaveResult(ToolOutput):
    take: TakeSummary


def take_save(p: TakeSaveParams) -> TakeSaveResult:
    """Create or overwrite a take (the UI saves while recording, so nothing played
    is lost)."""
    from .store import TAKES_DIR, next_take_id, pads_lock, save_take

    project = open_project(p.project)
    data = dict(p.take)
    now = datetime.now(UTC).isoformat()
    data.setdefault("created_at", now)
    data["updated_at"] = now
    if not data.get("id"):
        with pads_lock(project):
            data["id"] = next_take_id(project)
            data.setdefault("name", f"Toma {int(data['id'][1:])}")
    try:
        take = Take.model_validate(data)
    except ValidationError as exc:
        raise MuestraError(
            issues_from_validation_error(exc, path_prefix="take"),
            hint="Send a full Take (schemas/pads: take).",
        ) from exc
    save_take(project, take)
    return TakeSaveResult(take=_summary(take), files=[f"{TAKES_DIR}/{take.id}.json"])


class Cleanup(ContractModel):
    dedupe_ms: float = Field(
        default=0.0, ge=0.0, le=200.0, description="Merge presses of one pad closer than this."
    )
    velocity: Literal["as_played", "fixed", "even"] = "as_played"
    velocity_value: float = Field(default=0.8, gt=0.0, le=1.0)


class TakeGetParams(ProjectScoped):
    take_id: str = TakeIdStr
    quantize: TakeQuantize | None = Field(
        default=None, description="Override the take's stored quantise for this read."
    )
    cleanup: Cleanup = Field(default_factory=Cleanup)
    at_bar: int | None = Field(
        default=None, ge=1, description="Also give 1-indexed song beats as if placed here."
    )


class TakeEventOut(ContractModel):
    beat: float = Field(description="0-based beats from the take origin, quantised.")
    raw_beat: float
    pad: str
    name: str
    velocity: float
    length_beats: float | None = None
    cycle: int
    song_beat: float | None = None


class TakeGetResult(ToolOutput):
    take: Take
    events: list[TakeEventOut]
    span_beats: float
    text: str = Field(description="Compact human/agent-readable digest of the take.")


def take_get(p: TakeGetParams) -> TakeGetResult:
    """One take as events (quantise and clean-up applied as asked; the stored raw
    performance is never changed by reading)."""
    from .process import Cleanup as C
    from .process import process_events, take_span_beats
    from .store import load_take

    project = open_project(p.project)
    take = load_take(project, p.take_id)
    events = process_events(take, p.quantize, C(**p.cleanup.model_dump()))
    span = take_span_beats(take)
    out = []
    for e in events:
        song_beat = None
        if p.at_bar is not None:
            song_beat = round((p.at_bar - 1) * 4 + e.beat + 1, 6)
        out.append(
            TakeEventOut(
                beat=e.beat,
                raw_beat=e.raw_beat,
                pad=e.pad,
                name=take.pads[e.pad].name,
                velocity=e.velocity,
                length_beats=e.length_beats,
                cycle=e.cycle,
                song_beat=song_beat,
            )
        )
    return TakeGetResult(take=take, events=out, span_beats=span, text=_digest(take, out, span))


def _digest(take: Take, events: list[TakeEventOut], span: float) -> str:
    q = take.quantize
    lines = [
        f"{take.id} «{take.name}» {take.status}, {take.bpm:g} BPM, "
        + (f"loop de {take.loop_beats:g} tiempos" if take.loop_beats else f"{span:g} tiempos")
        + f", {len(events)} golpes, cuantización guardada {q.grid}"
        + (f" {round(q.strength * 100)}%" if q.grid != "off" else "")
        + (f" swing {round(q.swing * 100)}%" if q.swing else "")
    ]
    by: dict[str, list[TakeEventOut]] = {}
    for e in events:
        by.setdefault(e.name, []).append(e)
    for name, evs in by.items():
        pos = " ".join(
            f"{e.beat:g}" + (f"@{e.velocity:.2g}" if e.velocity != 0.8 else "") for e in evs[:64]
        )
        more = f" (+{len(evs) - 64})" if len(evs) > 64 else ""
        lines.append(f"  {name} ({len(evs)}): {pos}{more}")
    return "\n".join(lines)


class TakeDeleteParams(ProjectScoped):
    take_id: str = TakeIdStr


class TakeDeleteResult(ToolOutput):
    moved_to: str


def take_delete(p: TakeDeleteParams) -> TakeDeleteResult:
    """Delete a take (it is moved to pads/takes/deleted/, never destroyed)."""
    from .store import delete_take

    project = open_project(p.project)
    dest = delete_take(project, p.take_id)
    return TakeDeleteResult(moved_to=dest, files=[dest])


# ---------------------------------------------------------------- commit


class CommitParams(ProjectScoped):
    take_id: str = TakeIdStr
    target: Literal["new_track", "track"] = Field(
        default="new_track",
        description="new_track = a new pattern track with one lane per pad; track = layer "
        "the hits into an existing drum (pattern) track.",
    )
    track: IdStr | None = Field(default=None, description="With target=track.")
    new_track_id: IdStr | None = Field(default=None, description="Default: pads, pads-2, ...")
    at_bar: int = Field(default=1, ge=1, description="Song bar where take beat 0 goes.")
    repeat: int | None = Field(default=None, ge=1, le=4096, description="Passes (default 1).")
    until_bar: int | None = Field(
        default=None, ge=2, description="Loop the take until this bar (exclusive)."
    )
    quantize: TakeQuantize | None = Field(default=None, description="Default: the take's.")
    cleanup: Cleanup = Field(default_factory=Cleanup)
    base_revision: str | None = None
    dry_run: bool = Field(default=False, description="Return the ops without writing.")


class CommitResult(ToolOutput):
    ops: list[dict]
    track: str
    new_track: bool
    lanes: dict[str, str] = Field(description="Pad key -> lane it plays on.")
    new_lanes: list[str] = Field(default_factory=list)
    hits: int
    notes: int
    dropped: int
    repeat: int
    span_beats: float
    messages: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list, description="Spanish, from song.edit.")
    revision: str | None = None


def pads_commit(p: CommitParams) -> CommitResult:
    """Turn a take into song material (song.edit ops: hits of a new pads track or
    of an existing drum track, notes of instrument tracks), placed at a bar and
    repeated; undoable like any edit."""
    from ..editor.core import apply_edit
    from ..editor.tools import _OPS
    from .commit import commit_ops
    from .process import Cleanup as C
    from .store import TAKES_DIR, load_take, save_take

    project = open_project(p.project)
    take = load_take(project, p.take_id)
    song = load_song(project)
    plan = commit_ops(
        song,
        take,
        target=p.target,
        track=p.track,
        at_bar=p.at_bar,
        repeat=p.repeat,
        until_bar=p.until_bar,
        quantize=p.quantize,
        cleanup=C(**p.cleanup.model_dump()),
        new_track_id=p.new_track_id,
    )
    if not plan.ops:
        raise MuestraError.one(
            "invalid_value",
            "La toma no tiene golpes para poner ahí.",
            path="take_id",
            value=p.take_id,
            hint="Check at_bar/until_bar and the take's events (pads.take_get).",
        )
    out = apply_edit(
        project, _OPS.validate_python(plan.ops), base_revision=p.base_revision, dry_run=p.dry_run
    )
    files = []
    if not p.dry_run:
        files.append("song.yaml")
        take = take.model_copy(
            update={
                "commits": [
                    *take.commits,
                    TakeCommit(
                        at=datetime.now(UTC),
                        track=plan.track,
                        at_bar=p.at_bar,
                        repeat=plan.repeat,
                        hits=plan.hits,
                        notes=plan.notes,
                    ),
                ],
                "status": "done",
            }
        )
        save_take(project, take)
        files.append(f"{TAKES_DIR}/{take.id}.json")
    return CommitResult(
        ops=plan.ops,
        track=plan.track,
        new_track=plan.new_track,
        lanes=plan.lanes,
        new_lanes=plan.new_lanes,
        hits=plan.hits,
        notes=plan.notes,
        dropped=plan.dropped,
        repeat=plan.repeat,
        span_beats=plan.span_beats,
        messages=plan.messages,
        changes=out.changes,
        revision=out.revision,
        files=files,
    )


# ---------------------------------------------------------------- slice


class SliceParams(ProjectScoped):
    chop: IdStr | None = Field(default=None, description="Slice this chop (or a part of it).")
    source: IdStr | None = Field(default=None, description="Or slice a region of this source.")
    stem: StemName = "mix"
    start_downbeat: int | None = Field(default=None, ge=1, description="Source regions only.")
    start_beats: float = Field(
        default=0.0, description="Chop: beats into the chop; source: beats from the downbeat."
    )
    length_beats: float | None = Field(default=None, gt=0, le=64)
    by: Literal["beat", "half", "transients"] = "beat"
    speed: Literal["project", "source"] = "project"
    warp: Warp | None = None
    max_slices: int = Field(default=16, ge=1, le=48)


class SliceResult(ToolOutput):
    slices: list[LaneSlice]


def pads_slice(p: SliceParams) -> SliceResult:
    """Cut a chop or a source region into slices for consecutive pads: per beat,
    per half beat, or at its transients."""
    from .slicing import slice_region

    project = open_project(p.project)
    song = load_song(project)
    slices = slice_region(
        project,
        song,
        chop=p.chop,
        source=p.source,
        stem=p.stem,
        start_downbeat=p.start_downbeat,
        start_beats=p.start_beats,
        length_beats=p.length_beats,
        by=p.by,
        speed=p.speed,
        warp=p.warp,
        max_slices=p.max_slices,
    )
    return SliceResult(slices=slices)


class FromTrackParams(ProjectScoped):
    track: IdStr
    keys: list[str] = Field(
        default_factory=lambda: list(DEFAULT_KEYS),
        description="Keys to fill, in order (default: the UI layout, bottom row first).",
    )


class FromTrackResult(ToolOutput):
    pads: list[Pad]


def pads_from_track(p: FromTrackParams) -> FromTrackResult:
    """Pads with the sounds of an existing drum track (one per lane), to play along
    with or add to it."""
    import re

    from .commit import pads_from_track as build

    bad = [k for k in p.keys if not re.match(PAD_KEY_PATTERN, k)]
    if bad:
        raise MuestraError.one("invalid_value", f"Teclas inválidas: {bad}", path="keys")
    project = open_project(p.project)
    return FromTrackResult(pads=build(load_song(project), p.track, p.keys))


TOOLS = [
    (
        "pads.get",
        pads_get,
        PadsGetParams,
        PadsGetResult,
        {"readonly": True, "cli": ("pads", "get")},
    ),
    (
        "pads.save_banks",
        pads_save_banks,
        PadsSaveBanksParams,
        PadsSaveBanksResult,
        {"cli": ("pads", "save-banks")},
    ),
    ("pads.take_save", take_save, TakeSaveParams, TakeSaveResult, {"cli": ("pads", "take-save")}),
    (
        "pads.take_get",
        take_get,
        TakeGetParams,
        TakeGetResult,
        {"readonly": True, "cli": ("pads", "take-get")},
    ),
    (
        "pads.take_delete",
        take_delete,
        TakeDeleteParams,
        TakeDeleteResult,
        {"cli": ("pads", "take-delete")},
    ),
    ("pads.commit", pads_commit, CommitParams, CommitResult, {"cli": ("pads", "commit")}),
    (
        "pads.slice",
        pads_slice,
        SliceParams,
        SliceResult,
        {"readonly": True, "cli": ("pads", "slice")},
    ),
    (
        "pads.from_track",
        pads_from_track,
        FromTrackParams,
        FromTrackResult,
        {"readonly": True, "cli": ("pads", "from-track")},
    ),
]
