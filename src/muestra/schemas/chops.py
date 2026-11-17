"""Chop-candidate contracts for ``source.find_chops`` / ``chops.choose``
(spec section 3 step 4 and section 8).

Each ``source.find_chops`` call persists a :class:`ChopCandidateSet` to
``cache/chops/<source_id>/<set_id>.json`` (``set_id`` allocated ``cs001``,
``cs002``...) and writes preview audio under
``cache/previews/<source_id>/<set_id>/``. The UI shows the candidates from
this file; the agent's ``auto`` pick and any ``manual`` override are both
recorded on it.

The globally unique candidate id used by ``chops.choose`` is
``"<set_id>/<local_id>"``, e.g. ``"cs001/cand2"``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from ._base import CandidateIdStr, ContractModel, IdStr, RelPath
from .song import StemName

__all__ = [
    "ChopAdjust",
    "ChopQuery",
    "ChopCandidate",
    "CandidateScore",
    "AgentPick",
    "ChosenRecord",
    "ChopCandidateSet",
    "full_candidate_id",
    "parse_candidate_id",
]


def full_candidate_id(set_id: str, local_id: str) -> str:
    """Global candidate id: '<set_id>/<local_id>', e.g. 'cs001/cand2'."""
    return f"{set_id}/{local_id}"


def parse_candidate_id(candidate_id: str) -> tuple[str, str] | None:
    """Split a global candidate id into (set_id, local_id); None if malformed."""
    parts = candidate_id.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


class ChopAdjust(ContractModel):
    """Beat deltas applied by ``chops.choose`` (may be negative)."""

    start_beats: float = 0.0
    length_beats: float = 0.0


class ChopQuery(ContractModel):
    """Echo of the ``source.find_chops`` request that produced the set."""

    section: str | None = None
    allow_unreliable: bool = False
    stem: StemName | None = None
    length_beats: float | None = Field(default=None, gt=0.0)
    n: int = Field(default=3, ge=1)
    window_s: tuple[float, float] | None = Field(
        default=None, description="Only loops inside [start_s, end_s] of the source (WP13)."
    )
    at_s: float | None = Field(
        default=None, ge=0.0, description="Exact spot: the downbeat nearest this time first."
    )


class CandidateScore(ContractModel):
    value: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class ChopCandidate(ContractModel):
    """One proposed loop, aligned to a real downbeat of the source."""

    id: CandidateIdStr = Field(description="Global id: 'csNNN/candN'.")
    source_id: IdStr
    stem: StemName
    start_downbeat: int = Field(ge=1, description="1-based index into downbeats_s.")
    start_s: float = Field(ge=0.0, description="Resolved start time in the source audio.")
    length_beats: float = Field(gt=0.0)
    end_s: float = Field(gt=0.0, description="Resolved end time in the source audio.")
    local_bpm: float | None = Field(
        default=None,
        gt=0.0,
        description="Source tempo over the chop: 60 * length_beats / (end_s - start_s).",
    )
    preview: RelPath | None = Field(
        default=None,
        description="Project-relative path to the rendered preview audio.",
    )
    score: CandidateScore | None = None
    bar: int | None = Field(
        default=None, ge=1, description="Bar of the record (= start_downbeat, 1-based)."
    )
    start_mmss: str | None = Field(default=None, description="start_s as 'm:ss.s'.")
    length_s: float | None = Field(default=None, gt=0.0, description="end_s - start_s.")


class AgentPick(ContractModel):
    """The candidate the agent chose in mode 'auto', with its reason."""

    candidate_id: CandidateIdStr = Field(description="Global candidate id 'csNNN/candN'.")
    justification: str = Field(min_length=1)


class ChosenRecord(ContractModel):
    """Written by ``chops.choose`` back onto the set it came from."""

    candidate_id: CandidateIdStr
    mode: Literal["auto", "manual"]
    adjust: ChopAdjust = Field(default_factory=ChopAdjust)
    at: AwareDatetime
    chop_id: IdStr | None = Field(
        default=None,
        description="The song.yaml chop id actually written (WP13: may be a new id).",
    )


class ChopCandidateSet(ContractModel):
    schema_version: Literal[0] = 0
    set_id: str = Field(pattern=r"^cs\d{3,}$", description="Allocated id: 'cs001', ...")
    source_id: IdStr
    created_at: AwareDatetime
    query: ChopQuery
    candidates: list[ChopCandidate] = Field(default_factory=list)
    pick: AgentPick | None = None
    chosen: ChosenRecord | None = None
