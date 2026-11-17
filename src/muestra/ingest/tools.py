"""Tool stubs owned by WP1 (ingest): source.* and chops.choose /
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

from typing import Literal

from pydantic import Field

from ..schemas._base import CandidateIdStr, IdStr, RelPath
from ..schemas.analysis import Analysis
from ..schemas.chops import ChopAdjust, ChopCandidateSet
from ..schemas.song import Chop, StemName
from ..toolbase import ProjectScoped, ToolOutput

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
    raise NotImplementedError(_WP)


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


def source_separate(p: SourceSeparateParams) -> SourceSeparateResult:
    """Separate a source into stems under cache/stems/<source_id>/ (cached)."""
    raise NotImplementedError(_WP)


class SourceAnalyzeParams(ProjectScoped):
    source_id: IdStr


class SourceAnalyzeResult(ToolOutput):
    source_id: IdStr
    analysis_path: RelPath
    analysis: Analysis


def source_analyze(p: SourceAnalyzeParams) -> SourceAnalyzeResult:
    """Generate cache/analysis/<source_id>.json (BPM, key, beats, downbeats,
    sections, loudness)."""
    raise NotImplementedError(_WP)


class SourceFindChopsParams(ProjectScoped):
    source_id: IdStr
    section: str | None = Field(
        default=None, description="Section label from analysis.json to chop from."
    )
    stem: StemName | None = Field(default=None, description="Stem to chop (default: 'other').")
    length_beats: float | None = Field(
        default=None, gt=0, description="Loop length in beats (default: 8)."
    )
    n: int = Field(default=3, ge=1, description="Number of candidates to propose.")


class SourceFindChopsResult(ToolOutput):
    set_id: str = Field(description="Candidate set id, e.g. 'cs001'.")
    set: ChopCandidateSet
    set_path: RelPath = Field(description="cache/chops/<source_id>/<set_id>.json")
    previews: list[RelPath] = Field(
        default_factory=list, description="Rendered preview audio paths."
    )


def source_find_chops(p: SourceFindChopsParams) -> SourceFindChopsResult:
    """Propose n loop candidates aligned to real downbeats, render a short
    preview of each, record the agent's auto pick + justification, and
    persist the set to cache/chops/ for the UI (spec 3 step 4, mode dual)."""
    raise NotImplementedError(_WP)


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


class ChopsChooseResult(ToolOutput):
    set_id: str
    candidate_id: CandidateIdStr
    chop: Chop = Field(description="The chop written into song.yaml.")


def chops_choose(p: ChopsChooseParams) -> ChopsChooseResult:
    """Fix a candidate from a find_chops set into a Chop (optionally
    adjusted) and write it into song.yaml as ``chop_id`` via a ruamel
    round-trip edit; marks the set as chosen. Manual choices are
    recorded in feedback.jsonl."""
    raise NotImplementedError(_WP)


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
    raise NotImplementedError(_WP)


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
