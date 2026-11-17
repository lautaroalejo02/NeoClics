"""feedback.jsonl contract (spec 7.5) — written by the UI, read by the agent.

One JSON object per line, discriminated union on ``type``:

* ``rating`` — like/dislike for a version.
* ``comment`` — free text, optionally pinned to a playback position and track.
* ``ab_choice`` — the winner of an A/B comparison.
* ``chop_choice`` — a manual chop pick that overrides the agent's
  ``source.find_chops`` pick (spec section 3 step 4, mode dual). Added in
  WP0; the spec's event list was not exhaustive.

Unknown ``type`` values are validation errors, not silently skipped.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, model_validator
from pydantic_core import PydanticCustomError

from ._base import CandidateIdStr, ContractModel, IdStr, LaneName, VersionStr
from .chops import ChopAdjust

__all__ = [
    "FeedbackEvent",
    "RatingEvent",
    "CommentEvent",
    "ABChoiceEvent",
    "ChopChoiceEvent",
    "Selection",
]


class _EventBase(ContractModel):
    ts: AwareDatetime = Field(description="ISO-8601 timestamp of the event (timezone-aware, UTC).")


class RatingEvent(_EventBase):
    type: Literal["rating"] = "rating"
    version: VersionStr
    value: Literal["like", "dislike"]


class ClipRef(ContractModel):
    clip: int = Field(ge=0, description="0-based clip index in tracks.<track>.clips.")
    instance: int | None = Field(default=None, ge=1, description="1-based pass (repeat).")


class HitRef(ContractModel):
    lane: LaneName
    bar: int = Field(ge=1, description="Song bar (1-indexed).")
    step: int | None = Field(default=None, ge=0, description="0-based grid step; null = free hit.")
    beat: float = Field(ge=1.0, description="1-indexed song beat of the hit.")
    index: int | None = Field(default=None, ge=0, description="pattern.hits index (free hits).")


class NoteRef(ContractModel):
    index: int = Field(ge=0, description="0-based index in tracks.<track>.notes.")
    beat: float = Field(ge=1.0)
    pitch: int = Field(ge=0, le=127)
    phrase: IdStr | None = Field(default=None, description="The note belongs to this phrase.")


class PadRef(ContractModel):
    """A pad (WP17): bank id + physical key code (pads/banks.json)."""

    bank: IdStr
    key: str = Field(pattern=r"^[A-Za-z0-9]{1,16}$")
    name: str | None = None


class Selection(ContractModel):
    """What the human pointed at in the editor (WP16). Positions are musical:
    start_beat/end_beat are 1-indexed song beats, end exclusive."""

    label: str = Field(min_length=1, description="Spanish description, e.g. 'bombo, compás 7'.")
    track: IdStr | None = None
    lane: LaneName | None = None
    start_beat: float | None = Field(default=None, ge=1.0)
    end_beat: float | None = Field(default=None, ge=1.0)
    clips: list[ClipRef] = Field(default_factory=list)
    hits: list[HitRef] = Field(default_factory=list)
    notes: list[NoteRef] = Field(default_factory=list)
    automation: str | None = Field(default=None, description="Automation target path.")
    points: list[int] = Field(default_factory=list, description="0-based automation point indices.")
    chop: IdStr | None = None
    source: IdStr | None = None
    source_s: tuple[float, float] | None = Field(
        default=None, description="[start_s, end_s) inside the source (source view)."
    )
    paths: list[str] = Field(
        default_factory=list,
        description="song.edit paths of the selected elements (versions.diff grammar).",
    )
    excerpt: dict[str, Any] | None = Field(
        default=None, description="The selected elements as they were (song.yaml values)."
    )
    take: str | None = Field(
        default=None,
        pattern=r"^t[0-9]{3,}$",
        description="WP17: a recorded take (pads/takes/<id>.json; read it with pads.take_get).",
    )
    pad: PadRef | None = Field(default=None, description="WP17: a pad of pads/banks.json.")


class CommentEvent(_EventBase):
    """Free text. WP16: with selection it is a request about exactly those
    elements ("Pedile al agente"); song_revision is the song.state revision
    the human was looking at."""

    type: Literal["comment"] = "comment"
    version: VersionStr | None = None
    at_s: float | None = Field(default=None, ge=0.0)
    track: IdStr | None = None
    text: str = Field(min_length=1)
    selection: Selection | None = None
    song_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")


class ABChoiceEvent(_EventBase):
    type: Literal["ab_choice"] = "ab_choice"
    a: VersionStr
    b: VersionStr
    winner: VersionStr

    @model_validator(mode="after")
    def _winner_is_a_or_b(self) -> ABChoiceEvent:
        if self.winner not in (self.a, self.b):
            raise PydanticCustomError(
                "invalid_value",
                "'winner' must be either 'a' ({a}) or 'b' ({b}).",
                {
                    "a": self.a,
                    "b": self.b,
                    "hint": "Set 'winner' to the value of 'a' or 'b'.",
                },
            )
        return self


class ChopChoiceEvent(_EventBase):
    """A manual chop selection (UI, mode 'manual'); overrides the agent pick."""

    type: Literal["chop_choice"] = "chop_choice"
    version: VersionStr | None = None
    candidate_id: CandidateIdStr = Field(
        description="Global candidate id 'csNNN/candN', e.g. 'cs001/cand2'."
    )
    adjust: ChopAdjust | None = None


FeedbackEvent = Annotated[
    RatingEvent | CommentEvent | ABChoiceEvent | ChopChoiceEvent,
    Field(discriminator="type"),
]
