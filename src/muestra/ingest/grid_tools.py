"""Tools for the beat-grid correction (WP12): source.regrid, source.preview_grid.

Registered through ``muestra.ingest.tools.TOOLS``. The grid model lives in
``muestra.schemas.analysis`` (``Analysis.grid``); the math in
``muestra.ingest.grid``; project I/O in ``muestra.ingest.regrid``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from ..schemas._base import ContractModel, IdStr, RelPath
from ..schemas.analysis import Analysis, GridAlternative, KeyEstimate, Section, Stems
from ..schemas.song import StemName
from ..toolbase import ProjectScoped, ToolOutput

__all__ = [
    "AnalysisSummary",
    "ChopGridChange",
    "GridPreviewFile",
    "GridReport",
    "SourcePreviewGridParams",
    "SourcePreviewGridResult",
    "SourceRegridParams",
    "SourceRegridResult",
    "TOOLS",
    "source_preview_grid",
    "source_regrid",
]


class GridReport(ContractModel):
    """Compact view of a source's beat grid and how it was corrected (no beat lists)."""

    source_id: IdStr
    bpm: float = Field(description="Effective global tempo (what chops and the song use).")
    raw_bpm: float = Field(description="Tempo as the beat tracker read it.")
    corrected: bool = Field(description="True when the effective grid differs from the raw one.")
    origin: Literal["tracker", "auto", "manual"]
    tempo_factor: float
    beat_phase: int
    downbeat_shift_beats: int
    beats_per_bar: int
    tempo_range_bpm: tuple[float, float]
    tempo_confidence: float
    bar_phase_confidence: float
    ask_human: bool
    reason: str
    alternatives: list[GridAlternative] = Field(default_factory=list)
    n_beats: int
    n_downbeats: int
    first_downbeats_s: list[float] = Field(
        default_factory=list, description="First 4 downbeats (seconds), to locate the 'one'."
    )

    @classmethod
    def of(cls, analysis: Analysis) -> GridReport:
        from .grid import auto_correct

        grid = analysis.grid or auto_correct(analysis, apply=False).grid
        return cls(
            source_id=analysis.source_id,
            bpm=round(analysis.bpm_global, 3),
            raw_bpm=round(grid.raw.bpm_global, 3),
            corrected=grid.tempo_factor != 1.0
            or grid.downbeat_shift_beats != 0
            or grid.beats_per_bar != 4,
            origin=grid.origin,
            tempo_factor=grid.tempo_factor,
            beat_phase=grid.beat_phase,
            downbeat_shift_beats=grid.downbeat_shift_beats,
            beats_per_bar=grid.beats_per_bar,
            tempo_range_bpm=grid.tempo_range_bpm,
            tempo_confidence=grid.tempo_confidence,
            bar_phase_confidence=grid.bar_phase_confidence,
            ask_human=grid.ask_human,
            reason=grid.reason,
            alternatives=grid.alternatives,
            n_beats=len(analysis.beats_s),
            n_downbeats=len(analysis.downbeats_s),
            first_downbeats_s=[round(d, 3) for d in analysis.downbeats_s[:4]],
        )


class AnalysisSummary(ContractModel):
    """analysis.json minus the beat lists (``beats_s``, ``downbeats_s``, raw grid):
    a 4-minute record has ~1000 beats, which do not belong in an LLM context."""

    source_id: IdStr
    file: RelPath
    duration_s: float
    sample_rate: int
    bpm_global: float
    tempo_variable: bool
    n_beats: int
    n_downbeats: int
    key: KeyEstimate | None = None
    sections: list[Section] = Field(default_factory=list)
    loudness_lufs: float | None = None
    stems: Stems | None = None
    separation_model: str | None = None

    @classmethod
    def of(cls, analysis: Analysis) -> AnalysisSummary:
        data = analysis.model_dump(exclude={"beats_s", "downbeats_s", "grid", "schema_version"})
        return cls(**data, n_beats=len(analysis.beats_s), n_downbeats=len(analysis.downbeats_s))


class SourceRegridParams(ProjectScoped):
    source_id: IdStr
    tempo_factor: Literal[0.5, 1.0, 2.0] | None = Field(
        default=None,
        description="ABSOLUTE, relative to the raw tracker output: 0.5 = keep every 2nd "
        "tracked beat (the tracker counted eighths), 1 = as tracked, 2 = add a beat "
        "between each pair. Omit to keep the current factor.",
    )
    beat_phase: Literal[0, 1] | None = Field(
        default=None,
        description="With tempo_factor 0.5: keep tracked beats 1,3,5... (0) or 2,4,6... "
        "(1). Default: the half that holds the tracked downbeats.",
    )
    downbeat_shift_beats: int | None = Field(
        default=None,
        ge=-16,
        le=16,
        description="ABSOLUTE: move the 'one' this many corrected beats from where the "
        "tracker put it (+1 = one beat later). Omit to keep it (it resets to 0 when "
        "tempo_factor changes).",
    )
    beats_per_bar: int | None = Field(
        default=None, ge=2, le=12, description="Beats per bar (v0 songs are 4/4: keep 4)."
    )
    reset: bool = Field(default=False, description="Back to the raw tracker grid.")
    update_song: bool = Field(
        default=True,
        description="Rewrite song.yaml chops on this source so they cut the SAME audio on "
        "the new grid (false: leave them; the result says what they now cut).",
    )


class ChopGridChange(ContractModel):
    chop_id: IdStr
    before: dict = Field(
        description="start_downbeat/start_offset_beats/length_beats, start_s/end_s, "
        "local_bpm, rate (= meta.bpm / local_bpm)."
    )
    after: dict | None = Field(description="Same keys on the new grid; null = no longer fits.")
    updated: bool = Field(description="song.yaml was edited for this chop.")
    same_audio: bool = Field(description="Still cuts the same audio (edges within 5 ms).")
    moved_ms: float | None = None
    rate_before: float | None = None
    rate_after: float | None = None
    note: str = Field(description="Spanish: what this chop now refers to.")


class SourceRegridResult(ToolOutput):
    source_id: IdStr
    analysis_path: RelPath
    bpm_before: float
    bpm_after: float
    grid: GridReport
    chops: list[ChopGridChange] = Field(default_factory=list)
    stale_candidate_sets: list[str] = Field(
        default_factory=list,
        description="Candidate sets scored on the old grid, moved to cache/chops/<src>/stale/.",
    )
    suggested_bpm: float | None = Field(
        default=None,
        description="meta.bpm that keeps the song's chops at the same speed (tempo changed).",
    )
    hint: str | None = None


def source_regrid(p: SourceRegridParams) -> SourceRegridResult:
    """Fix a source's beat grid in one step: halve/double the tracked tempo and/or
    move the downbeat ("el uno está corrido"). Derived from the raw tracker output
    kept in analysis.json, so it is reversible (reset=true) and idempotent (params
    are absolute); chop timing, warp, previews and ears all follow. Song chops on
    this source are rewritten to cut the same audio (update_song) and every move
    is reported; candidate sets scored on the old grid are retired."""
    from ..project import open_project
    from .regrid import regrid

    project = open_project(Path(p.project))
    out = regrid(
        project,
        p.source_id,
        tempo_factor=p.tempo_factor,
        beat_phase=p.beat_phase,
        downbeat_shift_beats=p.downbeat_shift_beats,
        beats_per_bar=p.beats_per_bar,
        reset=p.reset,
        update_song=p.update_song,
    )
    return SourceRegridResult(
        source_id=p.source_id,
        analysis_path=f"cache/analysis/{p.source_id}.json",
        bpm_before=round(out.before.bpm_global, 3),
        bpm_after=round(out.analysis.bpm_global, 3),
        grid=GridReport.of(out.analysis),
        chops=[ChopGridChange(**vars(c)) for c in out.chops],
        stale_candidate_sets=out.stale_sets,
        suggested_bpm=out.suggested_bpm,
        hint=out.hint,
        files=out.files,
    )


class SourcePreviewGridParams(ProjectScoped):
    source_id: IdStr
    start_downbeat: int | None = Field(
        default=None, ge=1, description="Downbeat (current grid, 1-based) to start at; default 1."
    )
    bars: int = Field(default=8, ge=1, le=64)
    stem: StemName = Field(default="mix", description="What to play under the clicks.")
    tempo_factor: Literal[0.5, 1.0, 2.0] | None = Field(
        default=None, description="Audition this correction WITHOUT applying it (see regrid)."
    )
    beat_phase: Literal[0, 1] | None = None
    downbeat_shift_beats: int | None = Field(default=None, ge=-16, le=16)
    beats_per_bar: int | None = Field(default=None, ge=2, le=12)
    alternatives: bool = Field(
        default=False, description="Also render one file per alternative reading (up to 4)."
    )


class GridPreviewFile(ContractModel):
    path: RelPath
    label: str
    applied: bool = Field(description="True = the grid currently in analysis.json.")
    tempo_factor: float
    beat_phase: int
    downbeat_shift_beats: int
    beats_per_bar: int
    bpm: float
    start_downbeat: int
    start_s: float
    end_s: float
    bars: int


class SourcePreviewGridResult(ToolOutput):
    source_id: IdStr
    previews: list[GridPreviewFile]
    listen: str = Field(description="Spanish instructions to give the human.")


LISTEN = (
    "Escuchá: el click agudo y fuerte tiene que caer en el uno de cada compás y los clicks "
    "suaves en cada negra (donde marcarías el pie). Si los clicks van al doble de rápido que "
    "tu pie, a la mitad, o el agudo cae en otro tiempo, decime cuál de los archivos suena bien."
)


def source_preview_grid(p: SourcePreviewGridParams) -> SourcePreviewGridResult:
    """Render a short excerpt of the source with a click on every beat and an
    accented (higher, louder) click on every downbeat, so the human can judge the
    grid by ear. With correction params it auditions that grid without applying
    it; alternatives=true renders the other readings too. Files live under
    cache/previews/<source>/grid/ (the UI's Chops tab plays them)."""
    from ..project import open_project
    from .regrid import preview_grid

    project = open_project(Path(p.project))
    previews = preview_grid(
        project,
        p.source_id,
        start_downbeat=p.start_downbeat,
        bars=p.bars,
        stem=p.stem,
        tempo_factor=p.tempo_factor,
        beat_phase=p.beat_phase,
        downbeat_shift_beats=p.downbeat_shift_beats,
        beats_per_bar=p.beats_per_bar,
        alternatives=p.alternatives,
    )
    return SourcePreviewGridResult(
        source_id=p.source_id,
        previews=[GridPreviewFile(**vars(x)) for x in previews],
        listen=LISTEN,
        files=[x.path for x in previews] + [f"cache/previews/{p.source_id}/grid/index.json"],
    )


TOOLS = [
    (
        "source.regrid",
        source_regrid,
        SourceRegridParams,
        SourceRegridResult,
        {"cli": ("source", "regrid")},
    ),
    (
        "source.preview_grid",
        source_preview_grid,
        SourcePreviewGridParams,
        SourcePreviewGridResult,
        {"cli": ("source", "preview-grid")},
    ),
]
