"""Alarm construction helpers, track roles and song.yaml fix paths.

Alarms are the agent-facing product of ears: each one carries a stable
``code``, a severity, where it happens (seconds + bar/beat + ids), the
measured value against its threshold, a one-line Spanish explanation in
musical terms and a concrete ``song.yaml`` change to try.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..schemas.metrics import Alarm, AlarmLocation
from ..schemas.song import (
    HighpassFx,
    LowpassFx,
    SidechainFx,
    Song,
    Track,
)
from .context import RenderContext, Timeline

__all__ = [
    "SEVERITY_RANK",
    "make_alarm",
    "sort_alarms",
    "fx_param_path",
    "find_fx",
    "is_automated",
    "kick_lane",
    "bass_tracks",
    "sample_tracks",
    "fmt_where",
]

SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}
MAX_WHERE = 8


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(float(v), nd)


def make_alarm(
    tl: Timeline,
    code: str,
    severity: str,
    message: str,
    *,
    at_s: float | None = None,
    where: Iterable[float] = (),
    track: str | None = None,
    lane: str | None = None,
    clip: str | None = None,
    chop: str | None = None,
    value: float | None = None,
    threshold: float | None = None,
    unit: str | None = None,
    count: int | None = None,
    fix: str | None = None,
) -> Alarm:
    locs = []
    for t in sorted(set(round(float(x), 3) for x in where))[:MAX_WHERE]:
        bar, beat = tl.where(t)
        locs.append(AlarmLocation(at_s=max(0.0, t), bar=bar, beat=beat))
    bar = beat = None
    if at_s is not None:
        at_s = max(0.0, float(at_s))
        bar, beat = tl.where(at_s)
    return Alarm(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        track=track,
        at_s=_r(at_s, 3),
        bar=bar,
        beat=beat,
        lane=lane,
        clip=clip,
        chop=chop,
        value=_r(value),
        threshold=_r(threshold),
        unit=unit,
        count=count if count and count > 0 else None,
        where=locs,
        fix=fix,
    )


def sort_alarms(alarms: list[Alarm]) -> list[Alarm]:
    return sorted(alarms, key=lambda a: (SEVERITY_RANK[a.severity], a.code, a.track or ""))


def fmt_where(tl: Timeline, t: float) -> str:
    bar, beat = tl.where(t)
    return f"compás {bar} beat {beat:g}"


# ---------------------------------------------------------------------------
# song.yaml paths for fixes
# ---------------------------------------------------------------------------


def find_fx(track: Track, fx_type: type) -> tuple[int, object] | None:
    for i, fx in enumerate(track.fx):
        if isinstance(fx, fx_type):
            return i, fx
    return None


def fx_param_path(track: Track, index: int, param: str) -> str:
    fx = track.fx[index]
    fid = getattr(fx, "id", None)
    return (
        f"tracks.{track.id}.fx.{fid}.{param}" if fid else f"tracks.{track.id}.fx[{index}].{param}"
    )


def is_automated(song: Song, target_prefix: str) -> bool:
    return any(a.target.startswith(target_prefix) for a in song.automation)


def hp_fix(track: Track, to_hz: float) -> str:
    found = find_fx(track, HighpassFx)
    if found:
        i, fx = found
        return f"subí `{fx_param_path(track, i, 'cutoff_hz')}` de {fx.cutoff_hz:g} a ~{to_hz:g}"  # type: ignore[attr-defined]
    return f"agregá `{{id: hp, type: highpass, cutoff_hz: {to_hz:g}}}` a `tracks.{track.id}.fx`"


def lp_path(track: Track) -> tuple[str, float] | None:
    found = find_fx(track, LowpassFx)
    if not found:
        return None
    i, fx = found
    return fx_param_path(track, i, "cutoff_hz"), fx.cutoff_hz  # type: ignore[attr-defined]


def sidechain_fix(track: Track, key_track: str | None, key_lane: str | None, depth: float) -> str:
    found = find_fx(track, SidechainFx)
    if found:
        i, fx = found
        return f"subí `{fx_param_path(track, i, 'depth')}` de {fx.depth:g} a {depth:g}"  # type: ignore[attr-defined]
    lane = f", key_lane: {key_lane}" if key_lane else ""
    key = key_track or "drums"
    return (
        f"agregá `{{id: sc, type: sidechain, key_track: {key}{lane}, depth: {depth:g}, "
        f"release_ms: 120}}` a `tracks.{track.id}.fx`"
    )


# ---------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------

_KICK_NAMES = ("kick", "bombo", "bd", "kik", "bassdrum")
_BASS_NAMES = ("bass", "bajo", "sub")


def kick_lane(ctx: RenderContext) -> tuple[str, str] | None:
    """(track id, lane) of the kick: a lane named like a kick, else the
    pattern lane with the most 40-150 Hz energy."""
    best: tuple[float, str, str] | None = None
    for t in ctx.song.tracks:
        if t.pattern is None or t.mute:
            continue
        for lane in t.pattern.lanes:
            if lane.lower() in _KICK_NAMES or lane.lower().startswith("kick"):
                return t.id, lane
        for lane in ctx.lanes.get(t.id, {}):
            spec = ctx.spec(f"{t.id}.{lane}")
            e = float(spec.band(40.0, 150.0).mean())
            if best is None or e > best[0]:
                best = (e, t.id, lane)
    return (best[1], best[2]) if best else None


def bass_tracks(ctx: RenderContext) -> list[str]:
    out = []
    for t in ctx.song.tracks:
        if t.mute or t.id not in ctx.stems:
            continue
        if t.instrument is not None:
            notes = ctx.timeline.notes(t.id)
            low = notes and sorted(n.pitch or 0 for n in notes)[len(notes) // 2] < 55
            if low or any(k in t.id for k in _BASS_NAMES):
                out.append(t.id)
        elif t.clips is not None and any(k in t.id for k in _BASS_NAMES):
            out.append(t.id)
    return out


def sample_tracks(ctx: RenderContext) -> list[str]:
    return [
        t.id
        for t in ctx.song.tracks
        if t.clips is not None
        and not t.mute
        and t.id in ctx.stems
        and not any(k in t.id for k in _BASS_NAMES)
    ]
