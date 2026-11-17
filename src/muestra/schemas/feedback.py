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

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator
from pydantic_core import PydanticCustomError

from ._base import CandidateIdStr, ContractModel, IdStr, VersionStr
from .chops import ChopAdjust

__all__ = [
    "FeedbackEvent",
    "RatingEvent",
    "CommentEvent",
    "ABChoiceEvent",
    "ChopChoiceEvent",
]


class _EventBase(ContractModel):
    ts: AwareDatetime = Field(description="ISO-8601 timestamp of the event (timezone-aware, UTC).")


class RatingEvent(_EventBase):
    type: Literal["rating"] = "rating"
    version: VersionStr
    value: Literal["like", "dislike"]


class CommentEvent(_EventBase):
    type: Literal["comment"] = "comment"
    version: VersionStr | None = None
    at_s: float | None = Field(default=None, ge=0.0)
    track: IdStr | None = None
    text: str = Field(min_length=1)


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
