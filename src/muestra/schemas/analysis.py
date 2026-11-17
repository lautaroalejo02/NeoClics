"""analysis.json contract (spec 7.2) — cached per-source analysis.

Lives at ``cache/analysis/<source_id>.json``. All times are seconds.
``downbeats_s`` entries are expected to coincide with ``beats_s`` entries;
``muestra.project.validate`` matches them with a tolerance when resolving
chops (see BEAT_MATCH_TOLERANCE_S there).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator
from pydantic_core import PydanticCustomError

from ._base import ContractModel, IdStr, RelPath

__all__ = [
    "TEMPO_FACTORS",
    "Analysis",
    "GridAlternative",
    "GridCorrection",
    "GridStep",
    "KeyEstimate",
    "RawGrid",
    "Section",
    "Stems",
]

#: allowed beat-grid tempo factors relative to the raw tracker output
#: (0.5 = keep every 2nd tracked beat, 2 = add a beat between each pair).
TEMPO_FACTORS = (0.5, 1.0, 2.0)
TempoFactor = Literal[0.5, 1.0, 2.0]


class KeyEstimate(ContractModel):
    tonic: str = Field(
        pattern=r"^[A-G](#|b)?$",
        description="Tonic note, e.g. 'A', 'F#', 'Bb'.",
    )
    mode: Literal["major", "minor"]
    confidence: float = Field(ge=0.0, le=1.0)


class Section(ContractModel):
    label: str = Field(min_length=1)
    start_s: float = Field(ge=0.0)
    end_s: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _end_after_start(self) -> Section:
        if self.end_s <= self.start_s:
            raise PydanticCustomError(
                "invalid_range",
                "Section {label}: end_s must be after start_s.",
                {"label": self.label, "hint": "Swap or fix the timestamps."},
            )
        return self


class Stems(ContractModel):
    """Relative paths to separated stem files under cache/stems/<source_id>/.

    At least one stem must be present; a 2-stem separation may only have
    some of them. The full mix is the source file itself, so there is no
    'mix' field here ('mix' exists only as a chop stem choice).
    """

    drums: RelPath | None = None
    bass: RelPath | None = None
    vocals: RelPath | None = None
    other: RelPath | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Stems:
        if not any([self.drums, self.bass, self.vocals, self.other]):
            raise PydanticCustomError(
                "invalid_value",
                "'stems' must list at least one stem file.",
                {"hint": "Add at least one of drums/bass/vocals/other."},
            )
        return self


class RawGrid(ContractModel):
    """The beat tracker's own output, kept so grid corrections are reversible."""

    bpm_global: float = Field(gt=0.0, lt=400.0)
    tempo_variable: bool = False
    beats_s: list[float] = Field(min_length=1)
    downbeats_s: list[float] = Field(min_length=1)


class GridAlternative(ContractModel):
    """Another plausible reading of the grid, as ready-to-pass source.regrid params."""

    tempo_factor: TempoFactor
    beat_phase: Literal[0, 1] = 0
    downbeat_shift_beats: int = 0
    bpm: float = Field(gt=0.0, description="Global tempo of this reading.")
    label: str = Field(description="Short Spanish description for the human.")


class GridStep(ContractModel):
    """One applied correction (history; the newest is the current one)."""

    at: AwareDatetime
    origin: Literal["auto", "manual", "reset"]
    tempo_factor: TempoFactor
    beat_phase: Literal[0, 1] = 0
    downbeat_shift_beats: int = 0
    beats_per_bar: int = 4


class GridCorrection(ContractModel):
    """How the effective grid (``beats_s``/``downbeats_s``) derives from the tracker.

    All parameters are ABSOLUTE, relative to ``raw`` (the tracker output):
    applying the same correction twice is a no-op, and
    ``tempo_factor: 1, downbeat_shift_beats: 0`` gives back the raw grid.
    """

    origin: Literal["tracker", "auto", "manual"] = Field(
        description="tracker = raw grid as tracked; auto = source.analyze picked the tempo "
        "octave inside tempo_range_bpm; manual = source.regrid."
    )
    tempo_factor: TempoFactor = 1.0
    beat_phase: Literal[0, 1] = Field(
        default=0,
        description="With tempo_factor 0.5: which of every two tracked beats is kept "
        "(0 = the 1st, 3rd, ... tracked beat).",
    )
    downbeat_shift_beats: int = Field(
        default=0,
        ge=-16,
        le=16,
        description="Corrected beats the downbeat (the 'one') is moved from where the "
        "tracker put it; +1 = one beat later.",
    )
    beats_per_bar: int = Field(default=4, ge=2, le=12)
    tempo_range_bpm: tuple[float, float] = (70.0, 160.0)
    raw: RawGrid
    tempo_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="How sure the tempo octave is (heuristic, not a probability).",
    )
    bar_phase_confidence: float = Field(
        ge=0.0, le=1.0, description="How sure the downbeat position is (heuristic)."
    )
    ask_human: bool = Field(
        description="True when the agent should have the human confirm the grid by ear."
    )
    reason: str = Field(description="Spanish: what was read, what was applied and why.")
    alternatives: list[GridAlternative] = Field(default_factory=list)
    history: list[GridStep] = Field(default_factory=list)


class GridRegion(ContractModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    reliable: bool
    reason: str


class GridBar(GridRegion):
    start_downbeat: int = Field(ge=1)
    beat_count: int = Field(ge=0)


class GridReliability(ContractModel):
    confidence: float = Field(
        ge=0, le=1, description="Reliable fraction of complete bars; heuristic."
    )
    bars: list[GridBar] = Field(default_factory=list)
    regions: list[GridRegion] = Field(default_factory=list)
    hint: str


class Analysis(ContractModel):
    schema_version: Literal[0] = 0
    source_id: IdStr
    file: RelPath = Field(description="Project-relative path of the source audio.")
    duration_s: float = Field(gt=0.0)
    sample_rate: int = Field(gt=0)
    bpm_global: float = Field(gt=0.0, lt=400.0)
    tempo_variable: bool = False
    beats_s: list[float] = Field(min_length=1)
    downbeats_s: list[float] = Field(min_length=1)
    key: KeyEstimate | None = None
    sections: list[Section] = Field(default_factory=list)
    loudness_lufs: float | None = None
    stems: Stems | None = None
    separation_model: str | None = None
    reliability: GridReliability | None = None
    grid: GridCorrection | None = Field(
        default=None,
        description="Beat-grid correction over the tracker output (WP12). beats_s, "
        "downbeats_s and bpm_global are always the EFFECTIVE grid; absent = the "
        "tracker's grid as-is (analyses written before WP12).",
    )

    @model_validator(mode="after")
    def _grid_within_duration(self) -> Analysis:
        if self.beats_s != sorted(self.beats_s):
            raise PydanticCustomError(
                "not_sorted",
                "'beats_s' must be sorted ascending.",
                {"hint": "Sort the beat timestamps."},
            )
        if self.downbeats_s != sorted(self.downbeats_s):
            raise PydanticCustomError(
                "not_sorted",
                "'downbeats_s' must be sorted ascending.",
                {"hint": "Sort the downbeat timestamps."},
            )
        for name, seq in (("beats_s", self.beats_s), ("downbeats_s", self.downbeats_s)):
            if seq[0] < 0 or seq[-1] > self.duration_s:
                raise PydanticCustomError(
                    "out_of_range",
                    f"'{name}' must lie within [0, duration_s].",
                    {"hint": "Drop timestamps outside the audio."},
                )
        for i, s in enumerate(self.sections):
            if s.end_s > self.duration_s:
                raise PydanticCustomError(
                    "out_of_range",
                    f"sections[{i}] ({s.label!r}) ends after duration_s.",
                    {"hint": "Clamp section bounds to the audio duration."},
                )
        for i, (a, b) in enumerate(zip(self.sections, self.sections[1:], strict=False)):
            if b.start_s < a.end_s:
                raise PydanticCustomError(
                    "overlap",
                    f"sections[{i}] ({a.label!r}) overlaps sections[{i + 1}] ({b.label!r}).",
                    {"hint": "Make section boundaries non-overlapping."},
                )
        return self
