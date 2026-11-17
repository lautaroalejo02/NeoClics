"""renders/vNNN/meta.json contract (spec 7.4) — one per render version.

The render layout is::

    renders/vNNN/
      mix.wav              stereo, project sample_rate, 24-bit PCM, post-master
      stems/<track>.wav    stereo, project sample_rate, 32-bit float,
                           POST track fx/fader/pan, PRE master; exactly the
                           same frame count as the mix, sample-aligned;
                           a muted track still gets a (silent) stem
      stems/<track>.<lane>.wav   per-lane stems for pattern tracks
                           (pre track fx), same length; used by metrics
      song.yaml            byte copy of song.yaml at that version —
                           source of truth for ears/diff/restore
                           (git show vNNN:song.yaml is the fallback)
      meta.json            this contract
      metrics.json         spec section 10

The mix is exactly ``range_bars`` long; tails are cut with a 5 ms
fade-out. Master chain order: master fx in listed order, then loudness
gain toward ``target_lufs``, then a final true-peak safety limiter.

``tracks`` + ``mix`` give the UI every audio path explicitly, so it never
has to list directories.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from ._base import ContractModel, IdStr, KeyStr, LaneName, RelPath, RenderIdStr, VersionStr

__all__ = ["RenderMeta", "DiffEntry", "RenderTrackMeta", "StemLaneRef"]

#: length of the tail-cutting fade applied at the end of every render.
RENDER_FADE_OUT_MS = 5.0


class DiffEntry(ContractModel):
    """One changed value in the musical diff between versions.

    ``path`` uses ID-based song.yaml addressing, matching the automation
    grammar: ``chops.c1.warp.pitch_semitones``,
    ``tracks.sample.fx.lp.cutoff_hz``,
    ``tracks.drums.pattern.lanes.kick.steps``, ``meta.bpm``. Elements
    without ids use indices: ``tracks.<id>.clips[<i>]``, and an fx
    without an ``id`` is addressed as ``fx[<i>]``.
    ``from_`` serializes as ``from`` in every dump (``serialize_by_alias``),
    whichever envelope or tool returns it.
    """

    model_config = ConfigDict(**ContractModel.model_config, serialize_by_alias=True)

    path: str
    from_: Any = Field(alias="from", default=None)
    to: Any = None


class StemLaneRef(ContractModel):
    """One pattern lane's stem file."""

    id: LaneName
    stem: RelPath = Field(
        description="Project-relative path, e.g. 'renders/v001/stems/drums.kick.wav'."
    )


class RenderTrackMeta(ContractModel):
    """A track's entry in meta.json: id, kind and its stem path(s)."""

    id: IdStr
    kind: Literal["clips", "pattern", "instrument"]
    stem: RelPath = Field(
        description="Project-relative path of the track stem, e.g. 'renders/v001/stems/drums.wav'."
    )
    lanes: list[StemLaneRef] = Field(
        default_factory=list,
        description="Per-lane stems (pattern tracks only), pre track fx.",
    )


class RenderMeta(ContractModel):
    schema_version: Literal[0] = 0
    version: RenderIdStr
    parent: VersionStr | None = None
    git_commit: str | None = Field(
        default=None,
        description="The version commit in the project's git repo (it "
        "contains this version's song.yaml). meta.json/metrics.json are "
        "written AFTER the commit and can always be regenerated.",
    )
    created_at: AwareDatetime
    request: str | None = Field(
        default=None,
        description="The natural-language request that produced this version.",
    )
    agent: str = Field(min_length=1, description="Agent/tool that rendered, e.g. 'claude-code'.")
    summary: str | None = Field(
        default=None,
        description="Musical (not technical) description of what changed.",
    )
    diff: list[DiffEntry] = Field(default_factory=list)
    render_time_s: float | None = Field(default=None, ge=0.0)
    duration_s: float | None = Field(default=None, gt=0.0)
    # --- echo of what was rendered, so the UI never opens song.yaml ---
    sample_rate: int = Field(gt=0, description="Project sample rate of every wav in renders/vNNN.")
    bpm: float = Field(gt=0, lt=400)
    key: KeyStr
    length_bars: int = Field(ge=1, description="meta.length_bars of the song at this version.")
    range_bars: tuple[int, int] = Field(
        description="[start, end) 1-indexed bar range that was rendered; "
        "end is EXCLUSIVE (default render: [1, length_bars + 1])."
    )
    mix: RelPath = Field(description="Project-relative path of mix.wav.")
    tracks: list[RenderTrackMeta] = Field(
        default_factory=list,
        description="Ordered track list with stems, mirroring song.yaml.",
    )

    @model_validator(mode="after")
    def _range_is_valid(self) -> RenderMeta:
        start, end = self.range_bars
        if not (1 <= start < end <= self.length_bars + 1):
            raise PydanticCustomError(
                "invalid_range",
                "range_bars {r} must satisfy 1 <= start < end <= length_bars + 1 ({lim}).",
                {
                    "r": self.range_bars,
                    "lim": self.length_bars + 1,
                    "hint": "range_bars is [start, end), 1-indexed, end exclusive.",
                },
            )
        return self
