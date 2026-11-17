"""song.yaml contract (spec 7.3) — the arrangement source of truth.

Positions are musical: bars and beats are 1-indexed, never seconds.
The renderer converts to seconds using ``meta.bpm``. v0 supports 4/4 only
(``meta.time_signature``); extending it requires a ``schema_version`` bump.

Semantics pinned down here (see docs/decisions.md):

* ``chop.start_downbeat`` is a 1-based index into the source's
  ``analysis.json`` ``downbeats_s`` list.
* A chop occupies exactly ``length_beats`` project beats on the grid.
  With ``warp.mode == "resample"`` the playback rate is
  ``project_bpm / source_local_bpm * 2**(pitch_semitones / 12)``; the
  pitch rides along with the tempo (vinyl style). With ``"stretch"``
  the audio is time-stretched to ``length_beats`` at project tempo while
  preserving pitch, then pitch-shifted by ``pitch_semitones``.
* Clips sit at ``(at_bar - 1) * 4 + offset_beats`` project beats and may
  not overrun ``meta.length_bars``; clips on one track may overlap (they
  are summed).
* Drum-pattern step strings use one char per step: ``x`` = hit (velocity
  1.0), ``o`` = ghost hit (0.6), ``1``-``9`` = hit at velocity n/9,
  ``.``/``-`` = rest, spaces ignored. String length must equal
  ``steps_per_bar * bars``; the pattern tiles every ``bars`` bars.
* ``pattern.swing`` in [0, 1] delays every odd-indexed step by
  ``swing * half`` a step duration (0 = straight, 1 = triplet swing).
* Automation ``target`` grammar:
  ``tracks.<track_id>.gain_db | tracks.<track_id>.pan |
  tracks.<track_id>.fx.<fx_id>.<param> | master.gain_db |
  master.fx.<fx_id>.<param>``. ``points`` are ``[bar, value]`` pairs with
  fractional 1-indexed bar positions in [1, length_bars + 1]; value units
  are the target parameter's own units.
"""

from __future__ import annotations

import re
import types
from typing import Annotated, Literal, Union, get_args, get_origin  # noqa: UP035

from pydantic import Field, model_validator
from pydantic.functional_validators import AfterValidator
from pydantic_core import PydanticCustomError

from ..errors import Issue
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
    "parse_automation_target",
    "parse_steps",
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

    * ``resample``: vinyl-style playback-rate change; pitch rises with
      tempo. ``pitch_semitones`` multiplies into the rate, so it also
      changes the sounding duration (audio is truncated or padded to the
      clip's grid span).
    * ``stretch``: high-quality time-stretch preserving pitch
      (Rubber Band), then a separate pitch shift of ``pitch_semitones``.
    """

    mode: Literal["resample", "stretch"] = "resample"
    pitch_semitones: float = Field(default=0.0, ge=-24.0, le=24.0)


class Chop(ContractModel):
    """A loop cut from a source stem, aligned to the source's real grid."""

    id: IdStr
    source: IdStr = Field(description="id of a sources[] entry.")
    stem: StemName = "other"
    start_downbeat: int = Field(
        ge=1,
        description="1-based index into analysis.json downbeats_s.",
    )
    length_beats: float = Field(gt=0, le=128)
    warp: Warp = Field(default_factory=Warp)
    fade_ms: float = Field(default=5.0, ge=0.0, le=1000.0)


# ---------------------------------------------------------------------------
# clips / patterns / instruments
# ---------------------------------------------------------------------------


class Clip(ContractModel):
    """Places a chop or a library sample on a track's grid.

    Exactly one of ``chop`` / ``sample`` must be set. ``sample`` clips
    need ``length_beats`` (grid occupancy; audio is truncated at the
    boundary, never looped — use ``repeat`` to tile) and may carry their
    own ``warp``; the renderer resolves the source BPM from the library
    index.
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
            if self.length_beats is not None or self.warp is not None:
                raise PydanticCustomError(
                    "clip_source",
                    "'length_beats' and 'warp' are only valid on 'sample' clips; "
                    "a chop clip takes them from the chop.",
                    {"hint": "Remove 'length_beats'/'warp' or use 'sample'."},
                )
        elif self.length_beats is None:
            raise PydanticCustomError(
                "missing_field",
                "'sample' clips require 'length_beats' (grid occupancy in beats).",
                {"hint": "Add 'length_beats'; audio is truncated at the boundary."},
            )
        return self


def clip_span_beats(clip: Clip, chop_length_beats: float) -> tuple[float, float]:
    """Return (start, end) of a clip's placements in project beats.

    ``chop_length_beats`` is the chop's ``length_beats`` (or the clip's own
    ``length_beats`` for sample clips).
    """
    start = (clip.at_bar - 1) * BEATS_PER_BAR + clip.offset_beats
    return start, start + clip.repeat * chop_length_beats


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
            "Invalid step characters {bad!r}.",
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
    """One instrument lane of a drum pattern."""

    sample: FileRef
    steps: StepStr
    gain_db: float = Field(default=0.0, ge=-60.0, le=24.0)
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)


class Pattern(ContractModel):
    """Step-sequenced drum pattern, tiled across the song.

    Each lane's ``steps`` string must be exactly
    ``steps_per_bar * bars`` characters (ignoring spaces).
    """

    steps_per_bar: int = Field(default=16, ge=1, le=64)
    bars: int = Field(default=1, ge=1, le=4096)
    lanes: dict[LaneName, PatternLane] = Field(min_length=1)
    swing: float = Field(default=0.0, ge=0.0, le=1.0)


class Instrument(ContractModel):
    """A plugin instrument (VST3/AU or built-in) fed by a MIDI file."""

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
    cutoff_hz: float = Field(gt=0.0, le=22000.0)
    order: Literal[2, 4, 6, 8] = 4


class LowpassFx(_FxBase):
    type: Literal["lowpass"] = "lowpass"
    cutoff_hz: float = Field(gt=0.0, le=22000.0)
    order: Literal[2, 4, 6, 8] = 4


class SidechainFx(_FxBase):
    """Ducks this track when ``key_track`` is playing.

    ``depth`` is the fraction of gain removed: the key envelope scales the
    track by ``1 - depth`` (0 = off, 1 = full duck to silence).
    """

    type: Literal["sidechain"] = "sidechain"
    key_track: IdStr
    depth: float = Field(ge=0.0, le=1.0)
    attack_ms: float = Field(default=5.0, ge=0.0, le=500.0)
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
    rate_hz: float = Field(default=1.0, gt=0.0, le=20.0)
    depth: float = Field(default=0.5, ge=0.0, le=1.0)
    centre_delay_ms: float = Field(default=7.0, ge=0.0, le=100.0)
    feedback: float = Field(default=0.0, ge=-1.0, le=1.0)
    mix: float = Field(default=0.5, ge=0.0, le=1.0)


class PhaserFx(_FxBase):
    type: Literal["phaser"] = "phaser"
    rate_hz: float = Field(default=1.0, gt=0.0, le=20.0)
    depth: float = Field(default=0.5, ge=0.0, le=1.0)
    centre_frequency_hz: float = Field(default=1300.0, gt=0.0, le=20000.0)
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


# ---------------------------------------------------------------------------
# automation
# ---------------------------------------------------------------------------

_AUTOMATION_TARGET_DOC = (
    "tracks.<track_id>.gain_db | tracks.<track_id>.pan | "
    "tracks.<track_id>.fx.<fx_id>.<param> | master.gain_db | "
    "master.fx.<fx_id>.<param>"
)


def parse_automation_target(target: str) -> tuple[str, str | None, str | None, str] | None:
    """Parse an automation target path.

    Returns ``(scope, track_id, fx_id, param)`` where scope is "tracks" or
    "master"; for bare gain_db/pan targets fx_id is None and param is the
    leaf name. Returns None if the grammar doesn't match.
    """
    parts = target.split(".")
    if parts[0] == "master":
        if parts == ["master", "gain_db"]:
            return ("master", None, None, "gain_db")
        if (
            len(parts) == 4
            and parts[1] == "fx"
            and _ID_RE.match(parts[2])
            and _PARAM_NAME_RE.match(parts[3])
        ):
            return ("master", None, parts[2], parts[3])
        return None
    if parts[0] == "tracks" and len(parts) >= 3 and _ID_RE.match(parts[1]):
        if len(parts) == 3 and parts[2] in ("gain_db", "pan"):
            return ("tracks", parts[1], None, parts[2])
        if (
            len(parts) == 5
            and parts[2] == "fx"
            and _ID_RE.match(parts[3])
            and _PARAM_NAME_RE.match(parts[4])
        ):
            return ("tracks", parts[1], parts[3], parts[4])
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
    Value units are the target parameter's own units. ``curve``: ``lin``
    linear, ``exp`` exponential (values must be non-zero and same-sign),
    ``step`` holds the previous value.
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
    * ``instrument`` + ``midi`` — a plugin instrument playing a .mid file.
    """

    id: IdStr
    clips: list[Clip] | None = None
    pattern: Pattern | None = None
    instrument: Instrument | None = None
    midi: FileRef | None = None
    fx: list[Fx] = Field(default_factory=list)
    gain_db: float = Field(default=0.0, ge=-60.0, le=24.0)
    pan: float = Field(default=0.0, ge=-1.0, le=1.0)
    mute: bool = False

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> Track:
        n = sum(
            [
                self.clips is not None,
                self.pattern is not None,
                self.instrument is not None or self.midi is not None,
            ]
        )
        if n != 1:
            raise PydanticCustomError(
                "track_kind",
                "Track {tid!r} needs exactly one of 'clips', 'pattern', or 'instrument' + 'midi'.",
                {
                    "tid": self.id,
                    "hint": "Set exactly one content kind on the track.",
                },
            )
        if (self.instrument is None) != (self.midi is None):
            missing = "midi" if self.midi is None else "instrument"
            raise PydanticCustomError(
                "track_kind",
                "Track {tid!r}: 'instrument' and 'midi' must be set together; "
                "'{missing}' is missing.",
                {
                    "tid": self.id,
                    "missing": missing,
                    "hint": f"Add '{missing}' or remove the other field.",
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
        issues += self._check_tracks()
        issues += self._check_automation()
        return issues

    def _check_unique_ids(self) -> list[Issue]:
        issues = []
        for coll_name, coll in (
            ("sources", self.sources),
            ("chops", self.chops),
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
        song_beats = self.meta.length_bars * BEATS_PER_BAR

        for ti, track in enumerate(self.tracks):
            # fx ids unique within the track
            issues += _check_fx_ids(track.fx, f"tracks[{ti}].fx")

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
            if track.pattern is not None:
                issues += _check_pattern(
                    track.pattern, f"tracks[{ti}].pattern", self.meta.length_bars
                )
            for fi, fx in enumerate(track.fx):
                if isinstance(fx, SidechainFx):
                    if fx.key_track not in track_ids:
                        issues.append(
                            Issue(
                                path=f"tracks[{ti}].fx[{fi}].key_track",
                                code="ref_not_found",
                                message=f"Sidechain key_track {fx.key_track!r} is not a track id.",
                                hint="Point 'key_track' at an existing track id "
                                "(e.g. the drums track).",
                                value=fx.key_track,
                            )
                        )
                    elif fx.key_track == track.id:
                        issues.append(
                            Issue(
                                path=f"tracks[{ti}].fx[{fi}].key_track",
                                code="ref_not_found",
                                message=f"Track {track.id!r} cannot sidechain to itself.",
                                hint="Point 'key_track' at a different track.",
                                value=fx.key_track,
                            )
                        )
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
        return issues

    def _check_automation(self) -> list[Issue]:
        issues: list[Issue] = []
        tracks = {t.id: t for t in self.tracks}
        song_end_bar = self.meta.length_bars + 1

        for ai, auto in enumerate(self.automation):
            base = f"automation[{ai}]"
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
                container_path = f"tracks[{self._track_index(track_id)}].fx"
            else:
                track = None
                container = self.master.fx
                container_path = "master.fx"

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


def _check_pattern(pattern: Pattern, path: str, length_bars: int) -> list[Issue]:
    """Check step-string lengths. ``length_bars`` is the song length; patterns
    tile every ``bars`` bars and the last tile is truncated silently."""
    issues = []
    expected = pattern.steps_per_bar * pattern.bars
    for lane, spec in pattern.lanes.items():
        n = len(spec.steps.replace(" ", ""))
        if n != expected:
            issues.append(
                Issue(
                    path=f"{path}.lanes.{lane}.steps",
                    code="pattern_length",
                    message=f"Lane {lane!r} has {n} steps but the pattern "
                    f"needs steps_per_bar * bars = "
                    f"{pattern.steps_per_bar} * {pattern.bars} = {expected}.",
                    hint=f"Make the steps string {expected} characters long (spaces ignored).",
                    value=spec.steps,
                )
            )
    return issues
