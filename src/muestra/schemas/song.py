"""song.yaml contract (spec 7.3) — the arrangement source of truth.

Positions are musical: bars and beats are 1-indexed, never seconds.
The renderer converts to seconds using ``meta.bpm``. v0 supports 4/4 only
(``meta.time_signature``); extending it requires a ``schema_version`` bump.

Semantics pinned down here (see docs/decisions.md):

* ``chop.start_downbeat`` is a 1-based index into the source's
  ``analysis.json`` ``downbeats_s`` list; ``start_s`` is taken from
  ``beats_s`` at the beat nearest that downbeat (one grid, not two), and
  ``chop.start_offset_beats`` counts beats along ``beats_s`` from there
  (may be negative; fractional offsets interpolate linearly).
* A chop occupies exactly ``length_beats`` project beats on the grid.
  With ``warp.mode == "resample"`` the playback rate is set by tempo
  ONLY: ``project_bpm / source_local_bpm``, so the chop always fills its
  grid slot exactly and the tempo-induced pitch change is the vinyl
  effect. ``warp.pitch_semitones`` is an additional LENGTH-PRESERVING
  pitch shift (high-quality shifter) applied in both modes. With
  ``"stretch"`` the audio is first time-stretched to the grid preserving
  pitch (Rubber Band), then the same length-preserving pitch shift is
  applied. (``stretch`` maps each source beat onto the project grid,
  beat-wise; ``resample`` uses a single rate for the whole chop.)
* Clips sit at ``(at_bar - 1) * 4 + offset_beats`` project beats and may
  not overrun ``meta.length_bars``; clips on one track may overlap (they
  are summed).
* Drum-pattern step strings use one char per step: ``x`` = hit (velocity
  1.0), ``o`` = ghost hit (0.6), ``1``-``9`` = hit at velocity n/9,
  ``.``/``-`` = rest, spaces ignored. String length must equal
  ``steps_per_bar * bars``; the pattern tiles every ``bars`` bars inside
  the track's ``at_bar``..``until_bar`` window.
* ``pattern.swing`` in [0, 1] delays every odd-indexed (off) step by
  ``swing * step / 3`` beats-of-step (0 = straight, 1 = full triplet
  swing).
* Pattern and instrument tracks may carry ``at_bar`` (1-indexed,
  default 1) and ``until_bar`` (exclusive, default end of song) so
  "drums enter at bar 5" is one field.
* Instrument tracks take exactly one of ``midi: <path>`` or inline
  ``notes: [{beat, pitch, length_beats, velocity}]`` — ``beat`` is a
  1-indexed float from the start of the song, ``pitch`` a MIDI number
  0-127 or a note name like ``F#2``, ``velocity`` 1-127 (default 100).
* ``phrases`` (top level, WP12) are named, looping note phrases:
  ``{id, length_bars, notes: [{beat, pitch, length_beats, velocity}]}`` with
  ``beat`` 1-indexed INSIDE the phrase. An instrument track places them like
  clips: ``phrases: [{phrase, at_bar?, repeat?, transpose_semitones?}]``;
  ``at_bar`` defaults to the track's ``at_bar`` and an omitted ``repeat``
  loops the phrase until the track's ``until_bar`` (or the song end), the
  last pass truncated. Inline ``notes`` may be combined with ``phrases``
  (one-off notes on top); ``midi`` may not.
* MIDI files: ticks/ppq are PROJECT beats at ``meta.bpm``; tick 0 is bar
  1 beat 1; tempo and time-signature events inside the file are ignored.
* Automation ``target`` grammar:
  ``tracks.<track_id>.gain_db | tracks.<track_id>.pan |
  tracks.<track_id>.fx.<fx_id>.<param> |
  tracks.<track_id>.fx.<fx_id>.params.<name> | master.gain_db |
  master.fx.<fx_id>.<param> | master.fx.<fx_id>.params.<name>``.
  ``params.<name>`` targets a plugin fx's native parameter. ``points``
  are ``[bar, value]`` pairs with fractional 1-indexed bar positions in
  [1, length_bars + 1]; value units are the target parameter's own units
  and must stay inside that parameter's declared bounds. Automation
  REPLACES the static value: it holds the first point's value before the
  first point and the last point's value after the last point. Two
  automations may not share a target; NaN/inf values are rejected.
"""

from __future__ import annotations

import math
import re
import types
from dataclasses import dataclass
from typing import Annotated, Literal, Union, get_args, get_origin  # noqa: UP035

import annotated_types
from pydantic import Field, model_validator
from pydantic.functional_validators import AfterValidator, BeforeValidator
from pydantic_core import PydanticCustomError

from ..errors import Issue
from ..music import KeyParseError, parse_note_name
from ._base import (
    ContractModel,
    FileRef,
    IdStr,
    KeyStr,
    LaneName,
)

__all__ = [
    "Song",
    "SongMeta",
    "Source",
    "Chop",
    "Warp",
    "Clip",
    "MidiNote",
    "MidiPitch",
    "Phrase",
    "PhraseNote",
    "PhrasePlacement",
    "expand_phrases",
    "Pattern",
    "PatternLane",
    "Instrument",
    "Fx",
    "Automation",
    "Master",
    "Track",
    "StemName",
    "BEATS_PER_BAR",
    "clip_span_beats",
    "fx_field_bounds",
    "parse_automation_target",
    "parse_steps",
    "InstanceOverride",
    "PatternHit",
    "DrumHit",
    "ClipPass",
    "pattern_hits",
    "clip_passes",
    "lane_bar_steps",
]

#: v0 is 4/4 only; meta.time_signature is a Literal["4/4"] placeholder.
BEATS_PER_BAR = 4

StemName = Literal["drums", "bass", "vocals", "other", "mix"]

_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------
# meta / sources / chops
# ---------------------------------------------------------------------------


class SongMeta(ContractModel):
    """Global song settings. ``seed`` feeds every random decision."""

    title: str = Field(min_length=1)
    bpm: float = Field(gt=0, lt=400)
    key: KeyStr
    length_bars: int = Field(ge=1, le=4096)
    seed: int = 0
    time_signature: Literal["4/4"] = "4/4"


class Source(ContractModel):
    """An imported audio file. ``id`` is allocated as src1, src2, ..."""

    id: IdStr
    file: FileRef = Field(description="Project-relative path or library:// URI.")


class Warp(ContractModel):
    """How source audio conforms to the project tempo.

    * ``resample``: the playback rate is set by tempo ONLY,
      ``project_bpm / source_local_bpm``, so the chop always fills its
      grid slot exactly; the tempo-induced pitch change IS the vinyl
      effect. A single rate is used for the whole chop.
    * ``stretch``: pitch-preserving time-stretch to the grid (Rubber
      Band; each source beat is mapped onto the project grid, beat-wise).

    In both modes ``pitch_semitones`` is an additional LENGTH-PRESERVING
    pitch shift (high-quality shifter), applied after the warp — it never
    changes the chop's grid span.
    """

    mode: Literal["resample", "stretch"] = "resample"
    pitch_semitones: float = Field(default=0.0, ge=-24.0, le=24.0)
    formants: bool | None = Field(
        default=None,
        description="Preserve formants when pitch_semitones != 0. Default: on for "
        "stem: vocals only (measured: it colours instruments and drums).",
    )


def preserve_formants(warp: Warp, stem: str | None) -> bool:
    """WP13 default: formant preservation for vocals only (docs/render.md)."""
    return warp.formants if warp.formants is not None else stem == "vocals"


class Chop(ContractModel):
    """A loop cut from a source stem, aligned to the source's real grid."""

    id: IdStr
    source: IdStr = Field(description="id of a sources[] entry.")
    stem: StemName = "other"
    start_downbeat: int = Field(
        ge=1,
        description="1-based index into analysis.json downbeats_s.",
    )
    start_offset_beats: float = Field(
        default=0.0,
        ge=-4096.0,
        le=4096.0,
        description="Beats to shift the start along beats_s from the "
        "downbeat's matching beat; may be negative, fractional "
        "interpolates. Enables off-downbeat starts and stutters.",
    )
    length_beats: float = Field(gt=0, le=128)
    warp: Warp = Field(default_factory=Warp)
    fade_ms: float = Field(
        default=5.0,
        ge=0.0,
        le=1000.0,
        description="Seam crossfade (ms) between back-to-back repeats; fade to/from "
        "silence only at the first start and last end of a run. 0 = hard cuts.",
    )


# ---------------------------------------------------------------------------
# clips / patterns / instruments
# ---------------------------------------------------------------------------


class InstanceOverride(ContractModel):
    """Override for ONE repeat of a clip (WP16): ``instances: {3: {mute: true}}``.

    Keys of ``Clip.instances`` are 1-based repeat numbers (1 = the first pass).
    Values are relative to the clip: ``gain_db`` adds to the clip's gain,
    ``pitch_semitones`` adds a length-preserving shift on top of the material,
    ``reverse`` plays that pass backwards (XOR with the clip's ``reverse``).
    """

    mute: bool = False
    gain_db: float = Field(default=0.0, ge=-60.0, le=24.0)
    pitch_semitones: float = Field(default=0.0, ge=-24.0, le=24.0)
    reverse: bool = False


class Clip(ContractModel):
    """Places a chop or a library sample on a track's grid.

    Exactly one of ``chop`` / ``sample`` must be set. ``sample`` clips
    need ``length_beats`` (grid occupancy; audio is truncated at the
    boundary, never looped — use ``repeat`` to tile) and may carry their
    own ``warp``; the renderer resolves the source BPM from the library
    index.

    WP16 (all optional, defaults = the WP0 behaviour):

    * ``length_beats`` on a chop clip trims it: each pass is that many beats
      (default: the chop's length). ``trim_start_beats`` skips that many beats
      of the material at the start of every pass (negative = starts earlier in
      the record). Material outside the chop is rendered from the source.
    * ``stretch`` time-stretches the material keeping its pitch: a pass uses
      ``length_beats / stretch`` beats of material and lasts ``length_beats``.
    * ``gain_db``, ``reverse``, ``fade_in_ms``/``fade_out_ms`` (fade every pass
      to/from silence instead of the seam crossfade) apply to every pass;
      ``instances`` overrides single passes.
    """

    chop: IdStr | None = None
    sample: FileRef | None = None
    length_beats: float | None = Field(default=None, gt=0, le=4096)
    warp: Warp | None = None
    at_bar: int = Field(ge=1, description="1-indexed bar where the clip starts.")
    offset_beats: float = Field(
        default=0.0,
        description="Beat offset added to at_bar; may be negative or fractional.",
    )
    repeat: int = Field(default=1, ge=1, le=4096)
    trim_start_beats: float = Field(
        default=0.0,
        ge=-128.0,
        le=4096.0,
        description="Beats of material skipped at the start of every pass (WP16).",
    )
    stretch: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="Time-stretch keeping pitch: a pass plays length_beats/stretch "
        "beats of material over length_beats beats (WP16).",
    )
    gain_db: float = Field(default=0.0, ge=-60.0, le=24.0)
    reverse: bool = False
    fade_in_ms: float | None = Field(default=None, ge=0.0, le=10000.0)
    fade_out_ms: float | None = Field(default=None, ge=0.0, le=10000.0)
    instances: dict[int, InstanceOverride] = Field(
        default_factory=dict,
        description="Per-pass overrides keyed by 1-based repeat number (WP16).",
    )

    @model_validator(mode="after")
    def _exactly_one_source(self) -> Clip:
        if (self.chop is None) == (self.sample is None):
            raise PydanticCustomError(
                "clip_source",
                "A clip needs exactly one of 'chop' or 'sample'.",
                {
                    "hint": "Set either 'chop: <chop_id>' or 'sample: <path-or-library-uri>'.",
                },
            )
        if self.chop is not None:
            if self.warp is not None:
                raise PydanticCustomError(
                    "clip_source",
                    "'warp' is only valid on 'sample' clips; a chop clip takes it from the chop.",
                    {"hint": "Remove 'warp' (change the chop's warp instead) or use 'sample'."},
                )
        elif self.length_beats is None:
            raise PydanticCustomError(
                "missing_field",
                "'sample' clips require 'length_beats' (grid occupancy in beats).",
                {"hint": "Add 'length_beats'; audio is truncated at the boundary."},
            )
        for k in self.instances:
            if not 1 <= k <= self.repeat:
                raise PydanticCustomError(
                    "out_of_range",
                    "instances key {k} is not a pass of this clip (1..{n}).",
                    {
                        "k": k,
                        "n": self.repeat,
                        "hint": "Keys are 1-based repeat numbers: 1 is the first pass.",
                    },
                )
        return self

    def pass_beats(self, chop_length_beats: float) -> float:
        """Grid length of one pass (``chop_length_beats`` = the chop's length;
        for sample clips their own ``length_beats``)."""
        if self.length_beats is not None:
            return self.length_beats
        return max(0.0, (chop_length_beats - self.trim_start_beats) * self.stretch)


def clip_span_beats(clip: Clip, chop_length_beats: float) -> tuple[float, float]:
    """Return (start, end) of a clip's placements in project beats.

    ``chop_length_beats`` is the chop's ``length_beats`` (or the clip's own
    ``length_beats`` for sample clips).
    """
    start = (clip.at_bar - 1) * BEATS_PER_BAR + clip.offset_beats
    return start, start + clip.repeat * clip.pass_beats(chop_length_beats)


_STEP_CHARS = {
    "x": 1.0,
    "X": 1.0,
    "o": 0.6,
    "O": 0.6,
    ".": None,
    "-": None,
    "_": None,
    " ": "IGNORE",
}


def _check_steps(v: str) -> str:
    bad = sorted({c for c in v if c not in _STEP_CHARS and (not c.isdigit() or c == "0")})
    if bad:
        raise PydanticCustomError(
            "invalid_step_char",
            "Invalid step characters {bad}.",
            {
                "bad": "".join(bad),
                "hint": "Use 'x' (hit), 'o' (ghost), '1'-'9' (velocity), "
                "'.'/'-'_' (rest); spaces are ignored.",
            },
        )
    return v


StepStr = Annotated[str, AfterValidator(_check_steps)]


def parse_steps(steps: str) -> list[float | None]:
    """Parse a lane's step string into per-step velocities.

    Returns one entry per step (spaces removed): velocity in (0, 1] for
    hits, None for rests. ``x``/``X`` -> 1.0, ``o``/``O`` -> 0.6,
    ``1``-``9`` -> n/9, ``.``/``-``/``_`` -> rest.
    """
    out: list[float | None] = []
    for c in steps:
        if c.isdigit() and c != "0":
            out.append(int(c) / 9.0)
            continue
        v = _STEP_CHARS.get(c)
        if v == "IGNORE":
            continue
        out.append(v)
    return out


class PatternLane(ContractModel):
    """One instrument lane of a drum pattern.

    ``overrides`` (WP16) replaces single bars: ``{7: "x...x.......x..."}`` =
    in SONG bar 7 this lane plays that bar instead of the pattern (one bar of
    ``steps_per_bar`` step chars; all rests = the lane is silent in that bar).
    ``steps`` may be omitted (all rests) for a lane that only has overrides or
    free ``hits``.
    """

    sample: FileRef
    steps: StepStr = ""
    gain_db: float = Field(default=0.0, ge=-60.0, le=24.0)
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    overrides: dict[int, StepStr] = Field(
        default_factory=dict,
        description="Per-bar exceptions: song bar (1-indexed) -> that bar's steps (WP16).",
    )


class PatternHit(ContractModel):
    """A free-timed drum hit (WP16): any beat, not only the step grid.

    ``beat`` is 1-indexed from the song start (like notes); ``lane`` names a
    lane of the pattern (its sample, gain and pan); ``sample`` optionally
    plays another sound for this hit only. Swing does not move free hits.
    Performance pads (WP17) record into this list.
    """

    beat: float = Field(ge=1.0, description="1-indexed song beat (fractional allowed).")
    lane: LaneName
    velocity: float = Field(default=1.0, gt=0.0, le=1.0)
    sample: FileRef | None = None


class Pattern(ContractModel):
    """Step-sequenced drum pattern, tiled across the track's window.

    Each lane's ``steps`` string must be exactly
    ``steps_per_bar * bars`` characters (ignoring spaces).

    ``swing`` in [0, 1] delays every odd-indexed (off) step by
    ``swing * step / 3``: 0 is straight, 1 is full triplet swing.

    ``hits`` (WP16) are free-timed hits on top of the steps.
    """

    steps_per_bar: int = Field(default=16, ge=1, le=64)
    bars: int = Field(default=1, ge=1, le=4096)
    lanes: dict[LaneName, PatternLane] = Field(min_length=1)
    swing: float = Field(default=0.0, ge=0.0, le=1.0)
    hits: list[PatternHit] = Field(default_factory=list)


def _to_midi_pitch(v: object) -> object:
    if isinstance(v, str):
        try:
            return parse_note_name(v)
        except KeyParseError as e:
            raise PydanticCustomError(
                "invalid_value",
                "Invalid pitch {v}.",
                {
                    "v": v,
                    "hint": "Use a MIDI note number 0-127 or a note name like 'F#2' (C4 = 60).",
                },
            ) from e
    return v


#: MIDI pitch: a note number 0-127 or a note name ('F#2', C4 = 60).
MidiPitch = Annotated[int, BeforeValidator(_to_midi_pitch), Field(ge=0, le=127)]


class MidiNote(ContractModel):
    """One note of an instrument track's inline melody."""

    beat: float = Field(ge=1.0, description="1-indexed beat position from the start of the song.")
    pitch: MidiPitch
    length_beats: float = Field(gt=0.0, le=4096.0)
    velocity: int = Field(default=100, ge=1, le=127)


class PhraseNote(ContractModel):
    """One note of a phrase; ``beat`` is 1-indexed from the phrase start."""

    beat: float = Field(ge=1.0, description="1-indexed beat inside the phrase (1 = its start).")
    pitch: MidiPitch
    length_beats: float = Field(gt=0.0, le=4096.0)
    velocity: int = Field(default=100, ge=1, le=127)


class Phrase(ContractModel):
    """A named note phrase that instrument tracks loop (``tracks[].phrases``)."""

    id: IdStr
    length_bars: int = Field(
        ge=1, le=64, description="Loop length; the notes repeat every this many bars."
    )
    notes: list[PhraseNote] = Field(min_length=1)


class PhrasePlacement(ContractModel):
    """Places a phrase on an instrument track, like a clip places a chop."""

    phrase: IdStr = Field(description="id of a phrases[] entry.")
    at_bar: int | None = Field(
        default=None, ge=1, description="Bar where the first pass starts (default: track at_bar)."
    )
    repeat: int | None = Field(
        default=None,
        ge=1,
        le=4096,
        description="Number of passes; omitted = loop until the track's until_bar / song end.",
    )
    transpose_semitones: int = Field(default=0, ge=-24, le=24)


class Instrument(ContractModel):
    """A plugin instrument (VST3/AU or built-in) fed by MIDI notes."""

    plugin: str = Field(min_length=1, description="Plugin name or path.")
    preset: str | None = Field(
        default=None,
        description="Preset reference (project path or plugin-internal name); not file-checked.",
    )
    params: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# fx (discriminated union on 'type')
# ---------------------------------------------------------------------------


class _FxBase(ContractModel):
    #: required when the fx is targeted by automation; unique per container.
    id: IdStr | None = None


class HighpassFx(_FxBase):
    type: Literal["highpass"] = "highpass"
    cutoff_hz: float = Field(ge=10.0, le=22000.0)
    order: Literal[2, 4, 6, 8] = 4


class LowpassFx(_FxBase):
    type: Literal["lowpass"] = "lowpass"
    cutoff_hz: float = Field(ge=10.0, le=22000.0)
    order: Literal[2, 4, 6, 8] = 4


class SidechainFx(_FxBase):
    """Ducks this track whenever the ``key_track`` produces an event.

    The key signal is EVENT-triggered: when the key track has events
    (pattern steps or notes), the envelope ``env`` jumps to 1 at each
    trigger and decays with ``release_ms``; ``attack_ms`` ramps the jump.
    The track's gain is scaled by ``1 - depth * env`` (0 = off, 1 = full
    duck to silence).

    ``key_lane`` (optional) restricts triggers to one drum lane of a
    pattern track (e.g. ``kick``); it must name an existing lane. If the
    key track is a clips track (no events), the renderer falls back to an
    audio envelope follower on that track's stem.

    Sidechain chains that form a cycle (A keys on B, B keys on A) are a
    validation error.
    """

    type: Literal["sidechain"] = "sidechain"
    key_track: IdStr
    key_lane: LaneName | None = Field(
        default=None,
        description="Restrict triggers to this lane of a pattern key "
        "track (e.g. 'kick'). Must exist.",
    )
    depth: float = Field(ge=0.0, le=1.0)
    attack_ms: float = Field(default=1.0, ge=0.0, le=500.0)
    release_ms: float = Field(default=120.0, gt=0.0, le=5000.0)


class CompressorFx(_FxBase):
    type: Literal["compressor"] = "compressor"
    threshold_db: float = Field(le=0.0, ge=-90.0)
    ratio: float = Field(ge=1.0, le=40.0)
    attack_ms: float = Field(default=10.0, ge=0.0, le=1000.0)
    release_ms: float = Field(default=100.0, gt=0.0, le=5000.0)
    knee_db: float = Field(default=6.0, ge=0.0, le=24.0)
    makeup_gain_db: float = Field(default=0.0, ge=-24.0, le=24.0)


class LimiterFx(_FxBase):
    type: Literal["limiter"] = "limiter"
    ceiling_db: float = Field(default=-1.0, le=0.0, ge=-30.0)
    release_ms: float = Field(default=50.0, gt=0.0, le=1000.0)


class GainFx(_FxBase):
    type: Literal["gain"] = "gain"
    gain_db: float = Field(ge=-60.0, le=24.0)


class ReverbFx(_FxBase):
    """Serial-insert reverb; wet adds on top of dry (pedalboard names)."""

    type: Literal["reverb"] = "reverb"
    room_size: float = Field(default=0.5, ge=0.0, le=1.0)
    damping: float = Field(default=0.5, ge=0.0, le=1.0)
    wet_level: float = Field(default=0.33, ge=0.0, le=1.0)
    dry_level: float = Field(default=0.4, ge=0.0, le=1.0)
    width: float = Field(default=1.0, ge=0.0, le=1.0)


class DelayFx(_FxBase):
    type: Literal["delay"] = "delay"
    time_beats: float = Field(gt=0.0, le=64.0, description="Delay time in project beats.")
    feedback: float = Field(default=0.35, ge=0.0, lt=1.0)
    mix: float = Field(default=0.3, ge=0.0, le=1.0)


class ChorusFx(_FxBase):
    type: Literal["chorus"] = "chorus"
    rate_hz: float = Field(default=1.0, ge=0.01, le=20.0)
    depth: float = Field(default=0.5, ge=0.0, le=1.0)
    centre_delay_ms: float = Field(default=7.0, ge=0.0, le=100.0)
    feedback: float = Field(default=0.0, ge=-1.0, le=1.0)
    mix: float = Field(default=0.5, ge=0.0, le=1.0)


class PhaserFx(_FxBase):
    type: Literal["phaser"] = "phaser"
    rate_hz: float = Field(default=1.0, ge=0.01, le=20.0)
    depth: float = Field(default=0.5, ge=0.0, le=1.0)
    centre_frequency_hz: float = Field(default=1300.0, ge=10.0, le=20000.0)
    feedback: float = Field(default=0.0, ge=-1.0, le=1.0)
    mix: float = Field(default=0.5, ge=0.0, le=1.0)


class SaturationFx(_FxBase):
    type: Literal["saturation"] = "saturation"
    drive_db: float = Field(default=6.0, ge=0.0, le=60.0)


class PluginFx(_FxBase):
    """Generic hosted plugin (VST3/AU). ``params`` maps the plugin's own
    parameter names to values in that parameter's native range."""

    type: Literal["plugin"] = "plugin"
    plugin: str = Field(min_length=1)
    preset: str | None = None
    params: dict[str, float] = Field(default_factory=dict)


Fx = Annotated[
    HighpassFx
    | LowpassFx
    | SidechainFx
    | CompressorFx
    | LimiterFx
    | GainFx
    | ReverbFx
    | DelayFx
    | ChorusFx
    | PhaserFx
    | SaturationFx
    | PluginFx,
    Field(discriminator="type"),
]


def _is_numeric_annotation(annotation: object) -> bool:
    """True if a pydantic field annotation is numeric (float/int, incl. Optional)."""
    if annotation in (float, int):
        return True
    if get_origin(annotation) in (Union, types.UnionType):
        return any(a in (float, int) for a in get_args(annotation))
    return False


def fx_automatable_params(fx: _FxBase) -> dict[str, bool]:
    """Map of field name -> numeric? for an fx model (excluding id/type)."""
    return {
        name: _is_numeric_annotation(f.annotation)
        for name, f in type(fx).model_fields.items()
        if name not in ("id", "type")
    }


def fx_field_bounds(model_cls: type, field_name: str) -> tuple[float | None, float | None]:
    """Declared ``ge``/``gt``/``le``/``lt`` bounds of a numeric model field.

    Returns ``(low, high)``; either side may be None when unconstrained.
    Exclusive bounds (``gt``/``lt``) are nudged inward by a tiny epsilon
    when checking automation values.
    """
    f = model_cls.model_fields.get(field_name)
    if f is None:
        return None, None
    lo = hi = None
    for m in f.metadata:
        if isinstance(m, annotated_types.Ge):
            lo = m.ge
        elif isinstance(m, annotated_types.Gt):
            lo = m.gt + 1e-9
        elif isinstance(m, annotated_types.Le):
            hi = m.le
        elif isinstance(m, annotated_types.Lt):
            hi = m.lt - 1e-9
    return lo, hi


# ---------------------------------------------------------------------------
# automation
# ---------------------------------------------------------------------------

_AUTOMATION_TARGET_DOC = (
    "tracks.<track_id>.gain_db | tracks.<track_id>.pan | "
    "tracks.<track_id>.fx.<fx_id>.<param> | "
    "tracks.<track_id>.fx.<fx_id>.params.<name> | master.gain_db | "
    "master.fx.<fx_id>.<param> | master.fx.<fx_id>.params.<name>"
)

#: a plugin fx's native parameter name inside a target path (anything
#: without a '.'; dots are the grammar's separators).
_PLUGIN_PARAM_RE = re.compile(r"^[^.\s][^.]{0,63}$")


def parse_automation_target(target: str) -> tuple[str, str | None, str | None, str] | None:
    """Parse an automation target path.

    Returns ``(scope, track_id, fx_id, param)`` where scope is "tracks" or
    "master"; for bare gain_db/pan targets fx_id is None and param is the
    leaf name. Plugin-parameter targets return ``param == "params.<name>"``.
    Returns None if the grammar doesn't match.
    """
    parts = target.split(".")
    if parts[0] == "master":
        if parts == ["master", "gain_db"]:
            return ("master", None, None, "gain_db")
        if len(parts) >= 4 and parts[1] == "fx" and _ID_RE.match(parts[2]):
            if len(parts) == 4 and _PARAM_NAME_RE.match(parts[3]):
                return ("master", None, parts[2], parts[3])
            if len(parts) == 5 and parts[3] == "params" and _PLUGIN_PARAM_RE.match(parts[4]):
                return ("master", None, parts[2], f"params.{parts[4]}")
        return None
    if parts[0] == "tracks" and len(parts) >= 3 and _ID_RE.match(parts[1]):
        if len(parts) == 3 and parts[2] in ("gain_db", "pan"):
            return ("tracks", parts[1], None, parts[2])
        if len(parts) >= 5 and parts[2] == "fx" and _ID_RE.match(parts[3]):
            if len(parts) == 5 and _PARAM_NAME_RE.match(parts[4]):
                return ("tracks", parts[1], parts[3], parts[4])
            if len(parts) == 6 and parts[4] == "params" and _PLUGIN_PARAM_RE.match(parts[5]):
                return ("tracks", parts[1], parts[3], f"params.{parts[5]}")
    return None


def _check_points(v: list[tuple[float, float]]) -> list[tuple[float, float]]:
    positions = [p[0] for p in v]
    if any(b < a for a, b in zip(positions, positions[1:], strict=False)):
        raise PydanticCustomError(
            "points_order",
            "Automation point positions must be non-decreasing.",
            {"hint": "Sort points by bar position."},
        )
    return v


class Automation(ContractModel):
    """Parameter automation over time.

    ``points`` are ``[bar, value]`` pairs; bars are 1-indexed, fractional
    allowed, in [1, length_bars + 1] (length_bars + 1 = end of song).
    Value units are the target parameter's own units and must stay inside
    the parameter's declared bounds. ``curve``: ``lin`` linear, ``exp``
    exponential (values must be non-zero and same-sign), ``step`` holds
    the previous value.

    Semantics: automation REPLACES the target's static value entirely;
    before the first point it holds the first value, after the last
    point it holds the last value. Two automations may not share a
    target. NaN/inf values are rejected everywhere.
    """

    target: str = Field(description=_AUTOMATION_TARGET_DOC)
    curve: Literal["lin", "exp", "step"] = "lin"
    points: Annotated[list[tuple[float, float]], AfterValidator(_check_points)] = Field(
        min_length=2
    )


class Master(ContractModel):
    fx: list[Fx] = Field(default_factory=list)
    gain_db: float = Field(default=0.0, ge=-60.0, le=24.0)
    target_lufs: float | None = Field(default=None, le=0.0, ge=-60.0)


# ---------------------------------------------------------------------------
# track
# ---------------------------------------------------------------------------


class Track(ContractModel):
    """One mixer track. Exactly one kind of content:

    * ``clips`` — placed chops / library samples, or
    * ``pattern`` — a step-sequenced drum pattern, or
    * ``instrument`` + (``midi`` XOR ``notes``) — a plugin instrument
      playing a .mid file or an inline note list.

    Pattern and instrument tracks may restrict their window with
    ``at_bar`` (1-indexed, default 1) and ``until_bar`` (exclusive,
    default = ``meta.length_bars + 1``, i.e. end of song).
    """

    id: IdStr
    clips: list[Clip] | None = None
    pattern: Pattern | None = None
    instrument: Instrument | None = None
    midi: FileRef | None = None
    notes: list[MidiNote] | None = Field(
        default=None,
        description="Inline MIDI notes for an instrument track (alternative to 'midi').",
    )
    phrases: list[PhrasePlacement] | None = Field(
        default=None,
        description="Looping note phrases (ids in the top-level 'phrases'); may be "
        "combined with 'notes', not with 'midi'.",
    )
    at_bar: int = Field(
        default=1,
        ge=1,
        description="First bar this track plays (pattern/instrument tracks only).",
    )
    until_bar: int | None = Field(
        default=None,
        ge=2,
        description="Bar the track stops at (exclusive; default = end "
        "of song). Pattern/instrument tracks only.",
    )
    fx: list[Fx] = Field(default_factory=list)
    gain_db: float = Field(default=0.0, ge=-60.0, le=24.0)
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    mute: bool = False

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> Track:
        has_note_src = self.midi is not None or self.notes is not None or self.phrases is not None
        n = sum(
            [
                self.clips is not None,
                self.pattern is not None,
                self.instrument is not None or has_note_src,
            ]
        )
        if n != 1:
            raise PydanticCustomError(
                "track_kind",
                "Track {tid} needs exactly one of 'clips', 'pattern', or "
                "'instrument' + ('midi' | 'notes' | 'phrases').",
                {
                    "tid": self.id,
                    "hint": "Set exactly one content kind on the track.",
                },
            )
        if self.instrument is not None:
            inline = self.notes is not None or self.phrases is not None
            if (self.midi is None) == (not inline):
                raise PydanticCustomError(
                    "track_kind",
                    "Track {tid}: an instrument track takes 'midi' or inline "
                    "'notes'/'phrases' (exactly one source).",
                    {
                        "tid": self.id,
                        "hint": "Set 'midi: <path>', or 'notes: [...]' and/or "
                        "'phrases: [{phrase: <id>}]', not midi together with them.",
                    },
                )
        elif has_note_src:
            raise PydanticCustomError(
                "track_kind",
                "Track {tid}: 'midi'/'notes'/'phrases' require 'instrument'.",
                {
                    "tid": self.id,
                    "hint": "Add 'instrument' or remove the note source.",
                },
            )
        if self.until_bar is not None and self.until_bar <= self.at_bar:
            raise PydanticCustomError(
                "invalid_range",
                "Track {tid}: 'until_bar' ({until}) must be after 'at_bar' ({at}).",
                {
                    "tid": self.id,
                    "until": self.until_bar,
                    "at": self.at_bar,
                    "hint": "Make 'until_bar' > 'at_bar' (it is exclusive).",
                },
            )
        return self


# ---------------------------------------------------------------------------
# song
# ---------------------------------------------------------------------------


class Song(ContractModel):
    """Root of song.yaml."""

    schema_version: Literal[0] = 0
    meta: SongMeta
    sources: list[Source] = Field(default_factory=list)
    chops: list[Chop] = Field(default_factory=list)
    phrases: list[Phrase] = Field(
        default_factory=list, description="Named looping note phrases (WP12)."
    )
    tracks: list[Track] = Field(default_factory=list)
    automation: list[Automation] = Field(default_factory=list)
    master: Master = Field(default_factory=Master)

    # ------------------------------------------------------------------
    # semantic checks (pure model, no filesystem). Filesystem-aware checks
    # live in muestra.project.validate.
    # ------------------------------------------------------------------

    def check(self) -> list[Issue]:
        """All cross-reference/consistency issues. Collects, never raises."""
        issues: list[Issue] = []
        issues += self._check_unique_ids()
        issues += self._check_chops()
        issues += self._check_phrases()
        issues += self._check_tracks()
        issues += self._check_automation()
        return issues

    def _check_unique_ids(self) -> list[Issue]:
        issues = []
        for coll_name, coll in (
            ("sources", self.sources),
            ("chops", self.chops),
            ("phrases", self.phrases),
            ("tracks", self.tracks),
        ):
            seen: dict[str, int] = {}
            for i, item in enumerate(coll):
                if item.id in seen:
                    issues.append(
                        Issue(
                            path=f"{coll_name}[{i}].id",
                            code="duplicate_id",
                            message=f"Duplicate {coll_name} id {item.id!r} "
                            f"(first used at {coll_name}[{seen[item.id]}]).",
                            hint="Give each entry a unique id.",
                            value=item.id,
                        )
                    )
                else:
                    seen[item.id] = i
        return issues

    def _check_chops(self) -> list[Issue]:
        issues = []
        source_ids = {s.id for s in self.sources}
        for i, chop in enumerate(self.chops):
            if chop.source not in source_ids:
                issues.append(
                    Issue(
                        path=f"chops[{i}].source",
                        code="ref_not_found",
                        message=f"Chop {chop.id!r} references unknown source {chop.source!r}.",
                        hint="Point 'source' at an id in 'sources', or import the source first.",
                        value=chop.source,
                    )
                )
        return issues

    def _check_tracks(self) -> list[Issue]:
        issues: list[Issue] = []
        chop_lengths = {c.id: c.length_beats for c in self.chops}
        track_ids = {t.id for t in self.tracks}
        tracks = {t.id: t for t in self.tracks}
        song_beats = self.meta.length_bars * BEATS_PER_BAR

        for ti, track in enumerate(self.tracks):
            # fx ids unique within the track
            issues += _check_fx_ids(track.fx, f"tracks[{ti}].fx")

            # at_bar/until_bar: pattern and instrument tracks only, and
            # inside the song.
            if track.clips is not None and (track.at_bar != 1 or track.until_bar is not None):
                issues.append(
                    Issue(
                        path=f"tracks[{ti}].at_bar",
                        code="invalid_value",
                        message=f"Clip track {track.id!r} cannot use "
                        "'at_bar'/'until_bar'; those belong to pattern "
                        "and instrument tracks.",
                        hint="Remove 'at_bar'/'until_bar' (clips position "
                        "themselves with 'at_bar'/'offset_beats').",
                        value=track.at_bar,
                    )
                )
            if track.until_bar is not None and track.until_bar > self.meta.length_bars + 1:
                issues.append(
                    Issue(
                        path=f"tracks[{ti}].until_bar",
                        code="out_of_range",
                        message=f"Track {track.id!r}: 'until_bar' "
                        f"{track.until_bar} is past the song end "
                        f"(bar {self.meta.length_bars + 1}).",
                        hint=f"Use a value in [{track.at_bar + 1}, {self.meta.length_bars + 1}].",
                        value=track.until_bar,
                    )
                )

            if track.clips is not None:
                for ci, clip in enumerate(track.clips):
                    base = f"tracks[{ti}].clips[{ci}]"
                    if clip.chop is not None:
                        if clip.chop not in chop_lengths:
                            issues.append(
                                Issue(
                                    path=f"{base}.chop",
                                    code="ref_not_found",
                                    message=f"Clip references unknown chop {clip.chop!r}.",
                                    hint="Point 'chop' at an id in 'chops', "
                                    "or define the chop first.",
                                    value=clip.chop,
                                )
                            )
                            continue
                        length = chop_lengths[clip.chop]
                    else:
                        length = clip.length_beats or 0.0
                    if clip.pass_beats(length) <= 1e-9:
                        issues.append(
                            Issue(
                                path=f"{base}.trim_start_beats",
                                code="out_of_range",
                                message=f"Clip trims {clip.trim_start_beats:g} beats off a "
                                f"{length:g}-beat chop: nothing is left to play.",
                                hint="Use a smaller trim_start_beats or set length_beats.",
                                value=clip.trim_start_beats,
                            )
                        )
                        continue
                    if clip.at_bar > self.meta.length_bars:
                        issues.append(
                            Issue(
                                path=f"{base}.at_bar",
                                code="clip_position",
                                message=f"Clip starts at bar {clip.at_bar} but "
                                f"the song is only "
                                f"{self.meta.length_bars} bars.",
                                hint="Reduce 'at_bar' or increase meta.length_bars.",
                                value=clip.at_bar,
                            )
                        )
                        continue
                    start, end = clip_span_beats(clip, length)
                    if start < -1e-9:
                        issues.append(
                            Issue(
                                path=f"{base}.offset_beats",
                                code="clip_position",
                                message=f"Clip starts at beat {start:g} (before the song start).",
                                hint="Increase 'at_bar' or 'offset_beats'.",
                                value=clip.offset_beats,
                            )
                        )
                    if end > song_beats + 1e-9:
                        issues.append(
                            Issue(
                                path=f"{base}.repeat",
                                code="clip_overrun",
                                message=f"Clip ends at beat {end:g} but the "
                                f"song ends at beat {song_beats} "
                                f"(bar {self.meta.length_bars}).",
                                hint="Reduce 'repeat' or 'length_beats', move "
                                "'at_bar' earlier, or extend meta.length_bars.",
                                value=clip.repeat,
                            )
                        )
            if track.phrases is not None:
                issues += self._check_phrase_placements(track, ti)
            if track.pattern is not None:
                issues += _check_pattern(
                    track.pattern, f"tracks[{ti}].pattern", self.meta.length_bars, track
                )
            for fi, fx in enumerate(track.fx):
                if isinstance(fx, SidechainFx):
                    issues += _check_sidechain_ref(fx, track, ti, fi, track_ids, tracks)
        issues += _check_fx_ids(self.master.fx, "master.fx")
        for fi, fx in enumerate(self.master.fx):
            if isinstance(fx, SidechainFx) and fx.key_track not in track_ids:
                issues.append(
                    Issue(
                        path=f"master.fx[{fi}].key_track",
                        code="ref_not_found",
                        message=f"Sidechain key_track {fx.key_track!r} is not a track id.",
                        hint="Point 'key_track' at an existing track id.",
                        value=fx.key_track,
                    )
                )
        issues += self._check_sidechain_cycles()
        return issues

    def _check_phrase_placements(self, track: Track, ti: int) -> list[Issue]:
        issues: list[Issue] = []
        phrases = {p.id: p for p in self.phrases}
        until = track.until_bar or self.meta.length_bars + 1
        for pi, placement in enumerate(track.phrases or []):
            base = f"tracks[{ti}].phrases[{pi}]"
            phrase = phrases.get(placement.phrase)
            if phrase is None:
                issues.append(
                    Issue(
                        path=f"{base}.phrase",
                        code="ref_not_found",
                        message=f"Track {track.id!r} places unknown phrase {placement.phrase!r}.",
                        hint="Define it under top-level 'phrases' "
                        "({id, length_bars, notes}) or fix the id.",
                        value=placement.phrase,
                    )
                )
                continue
            at = placement.at_bar or track.at_bar
            if not track.at_bar <= at < until:
                issues.append(
                    Issue(
                        path=f"{base}.at_bar",
                        code="clip_position",
                        message=f"Phrase {phrase.id!r} starts at bar {at}, outside the "
                        f"window of track {track.id!r} (bars {track.at_bar} to {until - 1}).",
                        hint="Move 'at_bar' inside the track's at_bar/until_bar window.",
                        value=at,
                    )
                )
                continue
            if placement.repeat is not None:
                end = at + placement.repeat * phrase.length_bars
                if end > until:
                    issues.append(
                        Issue(
                            path=f"{base}.repeat",
                            code="clip_overrun",
                            message=f"{placement.repeat} passes of the {phrase.length_bars}-bar "
                            f"phrase {phrase.id!r} from bar {at} end at bar {end}, past bar "
                            f"{until} (track window / song end).",
                            hint="Reduce 'repeat', or omit it to loop until the window ends.",
                            value=placement.repeat,
                        )
                    )
            for ni, note in enumerate(phrase.notes):
                pitch = note.pitch + placement.transpose_semitones
                if not 0 <= pitch <= 127:
                    issues.append(
                        Issue(
                            path=f"{base}.transpose_semitones",
                            code="out_of_range",
                            message=f"Transposing phrase {phrase.id!r} note {ni} gives MIDI "
                            f"pitch {pitch} (valid 0-127).",
                            hint="Use a smaller transpose_semitones.",
                            value=placement.transpose_semitones,
                        )
                    )
                    break
        return issues

    def _check_phrases(self) -> list[Issue]:
        issues: list[Issue] = []
        for i, phrase in enumerate(self.phrases):
            span = phrase.length_bars * BEATS_PER_BAR
            for ni, note in enumerate(phrase.notes):
                if note.beat >= span + 1 - 1e-9:
                    issues.append(
                        Issue(
                            path=f"phrases[{i}].notes[{ni}].beat",
                            code="out_of_range",
                            message=f"Phrase {phrase.id!r} is {phrase.length_bars} bars "
                            f"({span:g} beats) but note {ni} starts at beat {note.beat:g}.",
                            hint=f"Phrase beats are 1-indexed inside the phrase: use "
                            f"1 <= beat < {span + 1:g}, or raise length_bars.",
                            value=note.beat,
                        )
                    )
        return issues

    def track_notes(self, track: Track) -> list[MidiNote] | None:
        """Absolute (song-beat) notes of an instrument track: inline ``notes`` plus
        expanded ``phrases``; None for a ``midi`` track. Sorted by beat."""
        if track.notes is None and track.phrases is None:
            return None
        notes = list(track.notes or [])
        notes += expand_phrases(self, track)
        return sorted(notes, key=lambda n: (n.beat, n.pitch))

    def _check_sidechain_cycles(self) -> list[Issue]:
        """Reject key-signal cycles: A sidechained to B sidechained to A
        has no well-defined envelope."""
        # edges (ti, fi) -> key_track id
        index = {t.id: ti for ti, t in enumerate(self.tracks)}
        edges: dict[str, list[tuple[str, int]]] = {}
        for track in self.tracks:
            for fi, fx in enumerate(track.fx):
                if isinstance(fx, SidechainFx) and fx.key_track in index:
                    edges.setdefault(track.id, []).append((fx.key_track, fi))

        issues: list[Issue] = []
        color: dict[str, int] = {}  # 0=unvisited 1=on stack 2=done

        def visit(node: str, stack: list[str]) -> None:
            color[node] = 1
            stack.append(node)
            for nxt, fi in edges.get(node, []):
                if color.get(nxt, 0) == 1:
                    cycle = " -> ".join([*stack[stack.index(nxt) :], nxt])
                    issues.append(
                        Issue(
                            path=f"tracks[{index[node]}].fx[{fi}].key_track",
                            code="sidechain_cycle",
                            message=f"Sidechain cycle: {cycle}.",
                            hint="Remove one of the sidechain fxs forming the loop.",
                            value=cycle,
                        )
                    )
                elif color.get(nxt, 0) == 0:
                    visit(nxt, stack)
            stack.pop()
            color[node] = 2

        for tid in index:
            if color.get(tid, 0) == 0:
                visit(tid, [])
        return issues

    def _check_automation(self) -> list[Issue]:
        issues: list[Issue] = []
        tracks = {t.id: t for t in self.tracks}
        song_end_bar = self.meta.length_bars + 1

        seen_targets: dict[str, int] = {}
        for ai, auto in enumerate(self.automation):
            base = f"automation[{ai}]"
            if auto.target in seen_targets:
                issues.append(
                    Issue(
                        path=f"{base}.target",
                        code="duplicate_target",
                        message=f"Automation target {auto.target!r} is "
                        f"already automated by automation[{seen_targets[auto.target]}].",
                        hint="Merge the points into a single automation for that target.",
                        value=auto.target,
                    )
                )
            else:
                seen_targets[auto.target] = ai

            parsed = parse_automation_target(auto.target)
            if parsed is None:
                issues.append(
                    Issue(
                        path=f"{base}.target",
                        code="automation_target",
                        message=f"Malformed automation target {auto.target!r}.",
                        hint=f"Use: {_AUTOMATION_TARGET_DOC}.",
                        value=auto.target,
                    )
                )
                continue
            scope, track_id, fx_id, param = parsed

            if scope == "tracks":
                assert track_id is not None
                track = tracks.get(track_id)
                if track is None:
                    issues.append(
                        Issue(
                            path=f"{base}.target",
                            code="ref_not_found",
                            message=f"Automation targets unknown track {track_id!r}.",
                            hint="Point the target at an existing track id.",
                            value=auto.target,
                        )
                    )
                    continue
                container: list[_FxBase] = track.fx
                container_cls: type = Track
                container_path = f"tracks[{self._track_index(track_id)}].fx"
            else:
                track = None
                container = self.master.fx
                container_cls = Master
                container_path = "master.fx"

            bounds: tuple[float | None, float | None] = (None, None)
            if fx_id is not None:
                fx = next((f for f in container if f.id == fx_id), None)
                if fx is None:
                    issues.append(
                        Issue(
                            path=f"{base}.target",
                            code="ref_not_found",
                            message=f"Automation targets fx {fx_id!r} which does "
                            f"not exist in {container_path}.",
                            hint=f"Add 'id: {fx_id}' to the target fx, or fix the target path.",
                            value=auto.target,
                        )
                    )
                    continue
                if param.startswith("params."):
                    # plugin native parameter: only PluginFx has a
                    # 'params' dict; any finite value is allowed.
                    if not isinstance(fx, PluginFx):
                        issues.append(
                            Issue(
                                path=f"{base}.target",
                                code="automation_target",
                                message=f"Fx {fx_id!r} ({fx.type}) is not a "
                                "'plugin' fx and has no native parameters.",
                                hint="Automate a plugin fx, or target one "
                                "of this fx's own parameters.",
                                value=auto.target,
                            )
                        )
                        continue
                else:
                    params = fx_automatable_params(fx)
                    if param not in params:
                        known = ", ".join(sorted(params)) or "(none)"
                        issues.append(
                            Issue(
                                path=f"{base}.target",
                                code="automation_target",
                                message=f"Fx {fx_id!r} ({fx.type}) has no parameter {param!r}.",
                                hint=f"Automatable parameters: {known}.",
                                value=auto.target,
                            )
                        )
                        continue
                    if not params[param]:
                        issues.append(
                            Issue(
                                path=f"{base}.target",
                                code="automation_target",
                                message=f"Parameter {param!r} of fx {fx_id!r} is "
                                f"not numeric and cannot be automated.",
                                hint="Choose a numeric parameter of that fx.",
                                value=auto.target,
                            )
                        )
                        continue
                    bounds = fx_field_bounds(type(fx), param)
            else:
                bounds = fx_field_bounds(container_cls, param)

            lo, hi = bounds
            for pi, (bar, _value) in enumerate(auto.points):
                if bar < 1 or bar > song_end_bar:
                    issues.append(
                        Issue(
                            path=f"{base}.points[{pi}][0]",
                            code="automation_target",
                            message=f"Automation point at bar {bar:g} is "
                            f"outside [1, {song_end_bar}] "
                            f"(length_bars + 1).",
                            hint="Keep point positions within the song, "
                            "1-indexed; length_bars + 1 means 'end of song'.",
                            value=bar,
                        )
                    )
            for pi, (_bar, value) in enumerate(auto.points):
                if (lo is not None and value < lo) or (hi is not None and value > hi):
                    issues.append(
                        Issue(
                            path=f"{base}.points[{pi}][1]",
                            code="out_of_range",
                            message=f"Automation value {value:g} is "
                            f"outside {param}'s bounds "
                            f"[{lo if lo is not None else '-inf'}, "
                            f"{hi if hi is not None else '+inf'}].",
                            hint="Keep the value inside the target parameter's own range.",
                            value=value,
                        )
                    )
            if auto.curve == "exp":
                values = [v for _, v in auto.points]
                if any(v == 0 for v in values) or not (
                    all(v > 0 for v in values) or all(v < 0 for v in values)
                ):
                    issues.append(
                        Issue(
                            path=f"{base}.points",
                            code="automation_target",
                            message="'exp' curve needs all point values to be "
                            "non-zero and same-sign.",
                            hint="Use 'lin' or 'step', or keep values same-signed.",
                        )
                    )
        return issues

    def _track_index(self, track_id: str) -> int:
        for i, t in enumerate(self.tracks):
            if t.id == track_id:
                return i
        return -1


def _check_sidechain_ref(
    fx: SidechainFx,
    track: Track,
    ti: int,
    fi: int,
    track_ids: set[str],
    tracks: dict[str, Track],
) -> list[Issue]:
    """Sidechain key_track/key_lane reference checks for one fx."""
    issues: list[Issue] = []
    base = f"tracks[{ti}].fx[{fi}].key_track"
    if fx.key_track not in track_ids:
        issues.append(
            Issue(
                path=base,
                code="ref_not_found",
                message=f"Sidechain key_track {fx.key_track!r} is not a track id.",
                hint="Point 'key_track' at an existing track id (e.g. the drums track).",
                value=fx.key_track,
            )
        )
        return issues
    if fx.key_track == track.id:
        issues.append(
            Issue(
                path=base,
                code="ref_not_found",
                message=f"Track {track.id!r} cannot sidechain to itself.",
                hint="Point 'key_track' at a different track.",
                value=fx.key_track,
            )
        )
        return issues
    if fx.key_lane is not None:
        key = tracks[fx.key_track]
        lane_ok = key.pattern is not None and fx.key_lane in key.pattern.lanes
        if not lane_ok:
            issues.append(
                Issue(
                    path=f"tracks[{ti}].fx[{fi}].key_lane",
                    code="ref_not_found",
                    message=f"Sidechain key_lane {fx.key_lane!r} is not a "
                    f"lane of the pattern track {fx.key_track!r}.",
                    hint="key_lane must name a lane of a pattern key track "
                    "(e.g. 'kick'); omit it to use every event, or to "
                    "follow a clips track's audio envelope.",
                    value=fx.key_lane,
                )
            )
    return issues


def _check_fx_ids(fx_list: list[_FxBase], path: str) -> list[Issue]:
    issues = []
    seen: dict[str, int] = {}
    for i, fx in enumerate(fx_list):
        if fx.id is None:
            continue
        if fx.id in seen:
            issues.append(
                Issue(
                    path=f"{path}[{i}].id",
                    code="duplicate_id",
                    message=f"Duplicate fx id {fx.id!r} (first used at {path}[{seen[fx.id]}]).",
                    hint="Fx ids must be unique within the track (or master).",
                    value=fx.id,
                )
            )
        else:
            seen[fx.id] = i
    return issues


def _check_pattern(
    pattern: Pattern, path: str, length_bars: int, track: Track | None = None
) -> list[Issue]:
    """Check step-string lengths, per-bar overrides and free hits. ``length_bars``
    is the song length; patterns tile every ``bars`` bars and the last tile is
    truncated silently."""
    issues = []
    expected = pattern.steps_per_bar * pattern.bars
    at = track.at_bar if track is not None else 1
    until = (track.until_bar if track is not None else None) or length_bars + 1
    for lane, spec in pattern.lanes.items():
        n = len(spec.steps.replace(" ", ""))
        if n != expected and n != 0:
            issues.append(
                Issue(
                    path=f"{path}.lanes.{lane}.steps",
                    code="pattern_length",
                    message=f"Lane {lane!r} has {n} steps but the pattern "
                    f"needs steps_per_bar * bars = "
                    f"{pattern.steps_per_bar} * {pattern.bars} = {expected}.",
                    hint=f"Make the steps string {expected} characters long (spaces ignored), "
                    "or omit it for a lane with only overrides/hits.",
                    value=spec.steps,
                )
            )
        for bar, steps in spec.overrides.items():
            where = f"{path}.lanes.{lane}.overrides.{bar}"
            if not at <= bar < until:
                issues.append(
                    Issue(
                        path=where,
                        code="out_of_range",
                        message=f"Lane {lane!r} overrides bar {bar}, outside the track "
                        f"window (bars {at} to {until - 1}).",
                        hint="Override keys are SONG bars (1-indexed) inside the track window.",
                        value=bar,
                    )
                )
            m = len(steps.replace(" ", ""))
            if m != pattern.steps_per_bar:
                issues.append(
                    Issue(
                        path=where,
                        code="pattern_length",
                        message=f"Override of bar {bar} has {m} steps; one bar is "
                        f"steps_per_bar = {pattern.steps_per_bar}.",
                        hint=f"Write exactly {pattern.steps_per_bar} step chars for that bar.",
                        value=steps,
                    )
                )
    lo_beat = (at - 1) * BEATS_PER_BAR + 1
    hi_beat = (until - 1) * BEATS_PER_BAR + 1
    for i, hit in enumerate(pattern.hits):
        where = f"{path}.hits[{i}]"
        if hit.lane not in pattern.lanes:
            issues.append(
                Issue(
                    path=f"{where}.lane",
                    code="ref_not_found",
                    message=f"Hit {i} uses lane {hit.lane!r}, which the pattern does not have.",
                    hint="Use one of: " + ", ".join(pattern.lanes) + " (or add that lane).",
                    value=hit.lane,
                )
            )
        if not lo_beat - 1e-9 <= hit.beat < hi_beat - 1e-9:
            issues.append(
                Issue(
                    path=f"{where}.beat",
                    code="out_of_range",
                    message=f"Hit {i} at beat {hit.beat:g} is outside the track window "
                    f"(beats {lo_beat:g} to {hi_beat:g}, end exclusive).",
                    hint="Free hits use 1-indexed song beats inside the track window.",
                    value=hit.beat,
                )
            )
    return issues


def expand_phrases(song: Song, track: Track) -> list[MidiNote]:
    """Song-beat notes for ``track.phrases`` (unknown phrase ids are skipped:
    ``Song.check`` reports them). Passes are cut at the track window's end."""
    phrases = {p.id: p for p in song.phrases}
    until = track.until_bar or song.meta.length_bars + 1
    end_beat = (until - 1) * BEATS_PER_BAR + 1  # first song beat after the window
    out: list[MidiNote] = []
    for placement in track.phrases or []:
        phrase = phrases.get(placement.phrase)
        if phrase is None:
            continue
        at = placement.at_bar or track.at_bar
        span = phrase.length_bars * BEATS_PER_BAR
        first = (at - 1) * BEATS_PER_BAR + 1
        passes = placement.repeat
        if passes is None:
            passes = max(0, math.ceil((end_beat - first) / span))
        for k in range(int(passes)):
            origin = first + k * span
            for note in phrase.notes:
                beat = origin + note.beat - 1
                if beat >= end_beat - 1e-9:
                    continue
                pitch = note.pitch + placement.transpose_semitones
                if not 0 <= pitch <= 127:
                    continue
                out.append(
                    MidiNote(
                        beat=beat,
                        pitch=pitch,
                        length_beats=note.length_beats,
                        velocity=note.velocity,
                    )
                )
    return out


# ---------------------------------------------------------------------------
# event expansion (WP16): the ONE definition of "which hit/pass sounds when",
# shared by the renderer, ears, the musical diff and the editor.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DrumHit:
    """One drum hit of a pattern track, after tiling, overrides and swing.

    ``beat0`` is 0-based song beats (0 = bar 1 beat 1, swing included);
    ``origin`` is ``pattern`` (the tiled steps), ``override`` (a per-bar
    exception) or ``free`` (``pattern.hits[index]``). ``bar``/``step`` locate a
    grid hit (song bar, 0-based step in the bar); free hits have ``step=None``.
    """

    beat0: float
    lane: str
    velocity: float
    origin: Literal["pattern", "override", "free"]
    bar: int
    step: int | None
    index: int | None
    sample: str


def _lane_velocities(pattern: Pattern, lane: PatternLane) -> list[float | None]:
    v = parse_steps(lane.steps)
    return v if v else [None] * (pattern.steps_per_bar * pattern.bars)


def pattern_hits(song: Song, track: Track) -> list[DrumHit]:
    """Every hit of a pattern track inside its window, sorted by time.

    Grid positions use exactly the renderer's arithmetic of WP0-WP15 (tile
    start bar * 4 + index * step), so songs without overrides render
    bit-identically."""
    pattern = track.pattern
    if pattern is None:
        return []
    step = BEATS_PER_BAR / pattern.steps_per_bar
    until = track.until_bar or song.meta.length_bars + 1
    lo, hi = (track.at_bar - 1) * BEATS_PER_BAR, (until - 1) * BEATS_PER_BAR
    swing = pattern.swing * step / 3
    out: list[DrumHit] = []
    for name, lane in pattern.lanes.items():
        velocities = _lane_velocities(pattern, lane)
        for bar0 in range(track.at_bar - 1, song.meta.length_bars, pattern.bars):
            for index, velocity in enumerate(velocities):
                song_bar = bar0 + index // pattern.steps_per_bar + 1
                if velocity is None or song_bar in lane.overrides:
                    continue
                beat = bar0 * BEATS_PER_BAR + index * step + (swing if index % 2 else 0)
                if lo - 1e-9 <= beat < hi - 1e-9:
                    out.append(
                        DrumHit(
                            beat,
                            name,
                            velocity,
                            "pattern",
                            song_bar,
                            index % pattern.steps_per_bar,
                            None,
                            lane.sample,
                        )
                    )
        for song_bar, steps in sorted(lane.overrides.items()):
            for s_index, velocity in enumerate(parse_steps(steps)):
                if velocity is None:
                    continue
                beat = (
                    (song_bar - 1) * BEATS_PER_BAR + s_index * step + (swing if s_index % 2 else 0)
                )
                if lo - 1e-9 <= beat < hi - 1e-9:
                    out.append(
                        DrumHit(
                            beat, name, velocity, "override", song_bar, s_index, None, lane.sample
                        )
                    )
    for i, hit in enumerate(pattern.hits):
        lane = pattern.lanes.get(hit.lane)
        beat = hit.beat - 1
        if lane is None or not lo - 1e-9 <= beat < hi - 1e-9:
            continue
        bar = int(math.floor(beat / BEATS_PER_BAR + 1e-9)) + 1
        out.append(
            DrumHit(beat, hit.lane, hit.velocity, "free", bar, None, i, hit.sample or lane.sample)
        )
    return sorted(out, key=lambda h: (h.beat0, h.lane))


def lane_bar_steps(pattern: Pattern, lane: PatternLane, bar: int, at_bar: int) -> list:
    """Velocities of one SONG bar of a lane (override or the tiled pattern)."""
    if bar in lane.overrides:
        return parse_steps(lane.overrides[bar])
    velocities = _lane_velocities(pattern, lane)
    tile = (bar - at_bar) % pattern.bars
    return velocities[tile * pattern.steps_per_bar : (tile + 1) * pattern.steps_per_bar]


@dataclass(frozen=True)
class ClipPass:
    """One audible pass (repeat) of a clip, overrides resolved.

    ``start0`` is 0-based song beats; ``length`` grid beats; the pass plays
    material beats ``[material_start, material_start + length / stretch)`` of
    the chop/sample (0 = the chop's start)."""

    clip_index: int
    instance: int
    start0: float
    end0: float
    length: float
    material_start: float
    stretch: float
    gain_db: float
    pitch_semitones: float
    reverse: bool
    mute: bool
    fade_in_ms: float | None
    fade_out_ms: float | None
    chop: str | None
    sample: str | None


def clip_passes(song: Song, track: Track) -> list[ClipPass]:
    """Every pass of every clip of a clip track (muted passes included, flagged)."""
    if track.clips is None:
        return []
    chops = {c.id: c for c in song.chops}
    out: list[ClipPass] = []
    for ci, clip in enumerate(track.clips):
        if clip.chop is not None:
            chop = chops.get(clip.chop)
            if chop is None:
                continue
            base = chop.length_beats
        else:
            base = clip.length_beats or 0.0
        length = clip.pass_beats(base)
        if length <= 0:
            continue
        start = (clip.at_bar - 1) * BEATS_PER_BAR + clip.offset_beats
        for r in range(clip.repeat):
            o = clip.instances.get(r + 1)
            out.append(
                ClipPass(
                    clip_index=ci,
                    instance=r + 1,
                    start0=start + r * length,
                    end0=start + (r + 1) * length,
                    length=length,
                    material_start=clip.trim_start_beats,
                    stretch=clip.stretch,
                    gain_db=clip.gain_db + (o.gain_db if o else 0.0),
                    pitch_semitones=o.pitch_semitones if o else 0.0,
                    reverse=clip.reverse != (o.reverse if o else False),
                    mute=bool(o and o.mute),
                    fade_in_ms=clip.fade_in_ms,
                    fade_out_ms=clip.fade_out_ms,
                    chop=clip.chop,
                    sample=clip.sample,
                )
            )
    return out
