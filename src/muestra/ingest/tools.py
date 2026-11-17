"""Tools owned by WP1 (ingest): source.* and chops.choose /
chops.list.

WP1 implements these; WP0 only fixes the typed interface. Candidate
sets persist to cache/chops/<source_id>/<set_id>.json; previews to
cache/previews/<source_id>/<set_id>/.

Programmatic song.yaml edits (``source.import`` registering a source,
``chops.choose`` writing a chop) go through
``muestra.project.edit_song_document`` (ruamel round-trip) — never a
wholesale rewrite, so comments, ordering and omitted defaults survive.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from ..schemas._base import CandidateIdStr, ContractModel, IdStr, RelPath
from ..schemas.chops import ChopAdjust, ChopCandidateSet
from ..schemas.song import Chop, StemName
from ..toolbase import ProjectScoped, ToolOutput
from .grid_tools import TOOLS as _GRID_TOOLS
from .grid_tools import AnalysisSummary, GridReport

__all__ = [
    "SourceImportParams",
    "SourceImportResult",
    "SourceSeparateParams",
    "SourceSeparateResult",
    "SourceAnalyzeParams",
    "SourceAnalyzeResult",
    "SourceFindChopsParams",
    "SourceFindChopsResult",
    "ChopsChooseParams",
    "ChopsChooseResult",
    "ChopsListParams",
    "ChopsListResult",
    "source_import",
    "source_separate",
    "source_analyze",
    "source_find_chops",
    "chops_choose",
    "chops_list",
    "TOOLS",
]

_WP = "WP1"

#: transport progress convention (docs/server.md): progress(fraction 0..1, message)
Progress = Callable[[float, str | None], None]


class SourceImportParams(ProjectScoped):
    path: str = Field(description="Path to a local audio file (wav/flac/mp3/m4a).")
    source_id: IdStr | None = Field(
        default=None, description="Force a source id; default allocates src1, src2, ..."
    )


class SourceImportResult(ToolOutput):
    source_id: IdStr
    file: RelPath = Field(description="Project-relative path under sources/.")


def source_import(p: SourceImportParams) -> SourceImportResult:
    """Copy an audio file into sources/ and register it in song.yaml
    (ruamel round-trip edit — comments and ordering survive)."""
    from ..project import open_project
    from .storage import import_source

    project = open_project(Path(p.project))
    source_id, rel = import_source(project, Path(p.path).expanduser().resolve(), p.source_id)
    files = [rel, "sources/.muestra-sources.json"]
    if project.song_path.exists():
        files.append("song.yaml")
    return SourceImportResult(source_id=source_id, file=rel, files=files)


class SourceSeparateParams(ProjectScoped):
    source_id: IdStr
    model: str | None = Field(
        default=None, description="Separation model override (default: muestra.toml)."
    )


class SourceSeparateResult(ToolOutput):
    source_id: IdStr
    stems: dict[StemName, RelPath] = Field(
        default_factory=dict, description="stem name -> project-relative wav path."
    )
    model: str | None = None
    cached: bool = Field(description="True when the cache was reused.")


def source_separate(
    p: SourceSeparateParams, progress: Progress | None = None
) -> SourceSeparateResult:
    """Separate a source into stems under cache/stems/<source_id>/ (cached)."""
    from ..project import open_project
    from .separation import separate

    stems, model, cached = separate(
        open_project(Path(p.project)), p.source_id, p.model, progress=progress
    )
    return SourceSeparateResult(
        source_id=p.source_id, stems=stems, model=model, cached=cached, files=list(stems.values())
    )


class SourceAnalyzeParams(ProjectScoped):
    source_id: IdStr
    min_bpm: float | None = Field(
        default=None,
        gt=0,
        lt=400,
        description="Lowest plausible tempo (default 70). Of the tracked tempo, its half "
        "and its double, the one inside [min_bpm, max_bpm] is used for the grid.",
    )
    max_bpm: float | None = Field(
        default=None, gt=0, lt=400, description="Highest plausible tempo (default 160)."
    )


class SourceAnalyzeResult(ToolOutput):
    source_id: IdStr
    analysis_path: RelPath
    analysis: AnalysisSummary = Field(
        description="analysis.json without the beat lists (read the file for those)."
    )
    grid: GridReport = Field(description="How the beat grid was read and corrected (WP12).")
    hint: str | None = Field(
        default=None, description="Agent-facing note, e.g. confirm the grid by ear."
    )


def source_analyze(p: SourceAnalyzeParams, progress: Progress | None = None) -> SourceAnalyzeResult:
    """Generate cache/analysis/<source_id>.json (BPM, key, beats, downbeats,
    sections, loudness). The tempo octave is chosen inside [min_bpm, max_bpm]
    (default 70-160); ``grid`` says what the tracker read, what was applied, how
    confident it is (``ask_human``) and the alternatives (half/double/shifted
    "one") to offer the human. ``source.regrid`` changes it."""
    from ..project import open_project
    from .analysis import analyze

    tempo_range = None
    if p.min_bpm is not None or p.max_bpm is not None:
        tempo_range = (p.min_bpm or 70.0, p.max_bpm or 160.0)
    analysis = analyze(
        open_project(Path(p.project)), p.source_id, progress=progress, tempo_range=tempo_range
    )
    rel = f"cache/analysis/{p.source_id}.json"
    report = GridReport.of(analysis)
    hint = None
    if report.ask_human or report.corrected:
        hint = report.reason
        if report.ask_human:
            hint += (
                " Usá source.preview_grid (alternatives=true) y preguntale al humano si los "
                "clicks caen en los golpes y el acento en el uno."
            )
    return SourceAnalyzeResult(
        source_id=p.source_id,
        analysis_path=rel,
        analysis=AnalysisSummary.of(analysis),
        grid=report,
        hint=hint,
        files=[rel],
    )


class SourceFindChopsParams(ProjectScoped):
    source_id: IdStr
    section: str | None = Field(
        default=None, description="Section label from analysis.json to chop from."
    )
    stem: StemName | None = Field(default=None, description="Stem to chop (default: 'other').")
    length_beats: float | None = Field(
        default=None, gt=0, description="Loop length in beats (default: compare 1/2/4/8 bars)."
    )
    n: int = Field(default=3, ge=1, description="Number of candidates to propose.")
    window_s: tuple[float, float] | None = Field(
        default=None,
        description="Only loops lying inside [start_s, end_s] of the record, e.g. [0, 30] "
        "for 'the riff at the start'. Combines with section.",
    )
    at_s: float | str | None = Field(
        default=None,
        description="Exact spot ('el riff del 0:15'): seconds or 'm:ss'. Candidate 1 starts "
        "on the downbeat nearest it; set length_beats too.",
    )

    @field_validator("at_s")
    @classmethod
    def _seconds(cls, v):
        if isinstance(v, str):
            try:
                parts = [float(x) for x in v.strip().split(":")]
            except ValueError as exc:
                raise ValueError("Use seconds (15.5) or 'm:ss' ('0:15').") from exc
            if not 1 <= len(parts) <= 3 or any(x < 0 for x in parts):
                raise ValueError("Use seconds (15.5) or 'm:ss' ('0:15').")
            v = sum(x * 60**i for i, x in enumerate(reversed(parts)))
        if v is not None and v < 0:
            raise ValueError("at_s must be >= 0.")
        return v

    @field_validator("window_s")
    @classmethod
    def _window(cls, v):
        if v is not None and not 0 <= v[0] < v[1]:
            raise ValueError("window_s is [start_s, end_s] with 0 <= start < end.")
        return v


class SourceFindChopsResult(ToolOutput):
    set_id: str = Field(description="Candidate set id, e.g. 'cs001'.")
    set: ChopCandidateSet
    set_path: RelPath = Field(description="cache/chops/<source_id>/<set_id>.json")
    previews: list[RelPath] = Field(
        default_factory=list, description="Rendered preview audio paths."
    )
    downbeats_s: list[float] = Field(
        default_factory=list,
        description="Bar map of the record: downbeat/bar i (1-based, = start_downbeat) "
        "starts at downbeats_s[i-1] seconds.",
    )


def source_find_chops(p: SourceFindChopsParams) -> SourceFindChopsResult:
    """Propose n loop candidates aligned to real downbeats, render a short
    preview of each, record the agent's auto pick + justification, and
    persist the set to cache/chops/ for the UI (spec 3 step 4, mode dual)."""
    from ..project import open_project
    from ..schemas.chops import ChopQuery
    from .chops import find

    project = open_project(Path(p.project))
    from ..project import load_analysis

    query = ChopQuery(
        section=p.section,
        stem=p.stem,
        length_beats=p.length_beats,
        n=p.n,
        window_s=p.window_s,
        at_s=p.at_s,
    )
    candidate_set, path = find(project, p.source_id, query)
    rel = path.relative_to(project.root).as_posix()
    previews = [c.preview for c in candidate_set.candidates if c.preview]
    downbeats = [round(t, 3) for t in load_analysis(project, p.source_id).downbeats_s]
    return SourceFindChopsResult(
        set_id=candidate_set.set_id,
        set=candidate_set,
        set_path=rel,
        previews=previews,
        files=[rel, *previews],
        downbeats_s=downbeats,
    )


class ChopsChooseParams(ProjectScoped):
    candidate_id: CandidateIdStr = Field(
        description="Global candidate id 'csNNN/candN', e.g. 'cs001/cand2'."
    )
    chop_id: IdStr = Field(description="Id of the chop to create or replace in song.yaml.")
    adjust: ChopAdjust | None = Field(
        default=None, description="Beat deltas to start/length (mode manual)."
    )
    mode: Literal["auto", "manual"] = Field(
        default="manual",
        description="'auto' = agent pick, 'manual' = human override (logged "
        "to feedback.jsonl as a chop_choice event).",
    )


class RepeatChange(ContractModel):
    track: IdStr
    clip: int = Field(description="0-based clip index on the track.")
    old_repeat: int
    new_repeat: int


class ChopsChooseResult(ToolOutput):
    set_id: str
    candidate_id: CandidateIdStr
    chop: Chop = Field(description="The chop written into song.yaml.")
    chop_id: IdStr | None = Field(
        default=None,
        description="Id actually written: chop_id, or a NEW id when the new length "
        "cannot keep the clips' span (then see agent_todo).",
    )
    repeat_changes: list[RepeatChange] = Field(
        default_factory=list,
        description="Clip repeats rescaled so each clip keeps its span in beats.",
    )
    message: str | None = Field(default=None, description="Spanish, shown to the human.")
    agent_todo: str | None = Field(
        default=None, description="What the agent must still change in song.yaml, if anything."
    )


def chops_choose(p: ChopsChooseParams) -> ChopsChooseResult:
    """Fix a candidate from a find_chops set into a Chop (optionally
    adjusted) and write it into song.yaml as ``chop_id`` via a ruamel
    round-trip edit; marks the set as chosen. Manual choices are
    recorded in feedback.jsonl."""
    from ..project import open_project
    from .chops import choose

    project = open_project(Path(p.project))
    candidate_set, chop, path, outcome = choose(
        project, p.candidate_id, p.chop_id, p.mode, p.adjust
    )
    files = ["song.yaml", path.relative_to(project.root).as_posix()]
    if p.mode == "manual":
        files.append("feedback.jsonl")
    return ChopsChooseResult(
        set_id=candidate_set.set_id,
        candidate_id=p.candidate_id,
        chop=chop,
        files=files,
        chop_id=outcome["chop_id"],
        repeat_changes=outcome["repeat_changes"],
        message=outcome["message"],
        agent_todo=outcome["agent_todo"],
    )


class ChopsListParams(ProjectScoped):
    source_id: IdStr | None = Field(
        default=None, description="Filter candidate sets by source; omit for all."
    )


class ChopsListResult(ToolOutput):
    sets: list[ChopCandidateSet] = Field(
        default_factory=list, description="Candidate sets under cache/chops/."
    )


def chops_list(p: ChopsListParams) -> ChopsListResult:
    """List persisted chop-candidate sets (optionally for one source)."""
    from ..project import open_project
    from .chops import read_set, set_paths

    project = open_project(Path(p.project))
    paths = set_paths(project, p.source_id)
    return ChopsListResult(
        sets=[read_set(path) for path in paths],
        files=[path.relative_to(project.root).as_posix() for path in paths],
    )


#: tools exported by this subpackage (collected by muestra.registry).
TOOLS = [
    (
        "source.import",
        source_import,
        SourceImportParams,
        SourceImportResult,
        {"cli": ("source", "import")},
    ),
    (
        "source.separate",
        source_separate,
        SourceSeparateParams,
        SourceSeparateResult,
        {"long_running": True, "cli": ("source", "separate")},
    ),
    (
        "source.analyze",
        source_analyze,
        SourceAnalyzeParams,
        SourceAnalyzeResult,
        {"long_running": True, "cli": ("source", "analyze")},
    ),
    (
        "source.find_chops",
        source_find_chops,
        SourceFindChopsParams,
        SourceFindChopsResult,
        {"long_running": True, "cli": ("source", "find-chops")},
    ),
    (
        "chops.choose",
        chops_choose,
        ChopsChooseParams,
        ChopsChooseResult,
        {"cli": ("chops", "choose")},
    ),
    (
        "chops.list",
        chops_list,
        ChopsListParams,
        ChopsListResult,
        {"readonly": True, "cli": ("chops", "list")},
    ),
]

TOOLS += _GRID_TOOLS
