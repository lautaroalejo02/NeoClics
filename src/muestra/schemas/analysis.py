"""analysis.json contract (spec 7.2) — cached per-source analysis.

Lives at ``cache/analysis/<source_id>.json``. All times are seconds.
``downbeats_s`` entries are expected to coincide with ``beats_s`` entries;
``muestra.project.validate`` matches them with a tolerance when resolving
chops (see BEAT_MATCH_TOLERANCE_S there).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ._base import ContractModel, IdStr, RelPath

__all__ = ["Analysis", "KeyEstimate", "Section", "Stems"]


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
