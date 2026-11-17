"""A take -> song.edit ops (WP17): what "usar esta toma" writes into song.yaml.

Drum/slice pads become free hits (``pattern.hits``) of a pattern track — a
new pads track, or layered into an existing drum track (a pad whose sound
already is a lane of that track plays on that lane; others get a new lane).
Note pads become ``notes`` of their instrument track. The result is plain
song material: the arrangement editor shows it hit by hit, the renderer
renders it, ``versions.diff`` describes it, the agent can edit it.
Mirrored by ``ui/src/pads/commit.ts`` only for display; the UI asks this
module (``pads.commit``, ``dry_run``) for the ops it applies.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..errors import MuestraError
from ..schemas.pads import Pad, Take, TakeQuantize
from ..schemas.song import BEATS_PER_BAR, PatternLane, Song
from .process import Cleanup, ProcessedEvent, process_events, take_span_beats

__all__ = ["CommitPlan", "commit_ops", "lane_name", "pad_lane", "pads_from_track"]

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
#: note pads in one-shot mode: written note length (beats)
ONESHOT_NOTE_BEATS = 0.5


def lane_name(pad: Pad) -> str:
    """The lane a pad's hits go to: ``pad.lane`` or a slug of its name."""
    if pad.lane:
        return pad.lane
    base = pad.name.lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        base = base.replace(a, b)
    slug = _SLUG_RE.sub("-", base).strip("-_")[:40] or "pad"
    if not slug[0].isalpha():
        slug = "p" + slug
    return slug


def pad_lane(pad: Pad) -> dict:
    """The pattern lane (JSON, defaults omitted) that plays a drum/slice pad."""
    out: dict = {}
    if pad.sample is not None:
        out["sample"] = pad.sample
    else:
        out["slice"] = pad.slice.model_dump(mode="json", exclude_defaults=True)
        out["slice"]["length_beats"] = pad.slice.length_beats
    if pad.gain_db:
        out["gain_db"] = pad.gain_db
    if pad.pan:
        out["pan"] = pad.pan
    if pad.pitch_semitones:
        out["pitch_semitones"] = pad.pitch_semitones
    if pad.reverse:
        out["reverse"] = True
    if pad.choke is not None:
        out["choke"] = pad.choke
    return out


def _same_sound(lane: PatternLane, pad: Pad) -> bool:
    if lane.sample != pad.sample or lane.slice != pad.slice:
        return False
    return lane.pitch_semitones == pad.pitch_semitones and lane.reverse == pad.reverse


@dataclass
class CommitPlan:
    ops: list[dict] = field(default_factory=list)
    track: str = ""
    new_track: bool = False
    lanes: dict[str, str] = field(default_factory=dict)  # pad key -> lane
    new_lanes: list[str] = field(default_factory=list)
    hits: int = 0
    notes: int = 0
    dropped: int = 0
    repeat: int = 1
    span_beats: float = 4.0
    messages: list[str] = field(default_factory=list)


def _unique(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def commit_ops(
    song: Song,
    take: Take,
    *,
    target: str = "new_track",
    track: str | None = None,
    at_bar: int = 1,
    repeat: int | None = None,
    until_bar: int | None = None,
    quantize: TakeQuantize | None = None,
    cleanup: Cleanup | None = None,
    new_track_id: str | None = None,
) -> CommitPlan:
    """The song.edit ops that place ``take`` at ``at_bar`` (``repeat`` passes,
    or looped until ``until_bar``)."""
    plan = CommitPlan()
    events = process_events(take, quantize, cleanup)
    span = take_span_beats(take)
    plan.span_beats = span
    song_end0 = song.meta.length_bars * BEATS_PER_BAR
    start0 = (at_bar - 1) * BEATS_PER_BAR
    if at_bar > song.meta.length_bars:
        raise MuestraError.one(
            "out_of_range",
            f"La canción tiene {song.meta.length_bars} compases: no puedo poner la toma "
            f"en el compás {at_bar}.",
            path="at_bar",
            value=at_bar,
            hint="Use a bar inside the song, or extend meta.length_bars first.",
        )
    by_id = {t.id: t for t in song.tracks}
    drum_events = [e for e in events if take.pads[e.pad].note is None]
    note_events = [e for e in events if take.pads[e.pad].note is not None]

    # ---- where the drum/slice hits go
    lo0, hi0 = 0.0, float(song_end0)
    existing = None
    if drum_events:
        if target == "track":
            existing = by_id.get(track or "")
            if existing is None or existing.pattern is None:
                raise MuestraError.one(
                    "ref_not_found",
                    f"«{track}» no es un track de batería (patrón) de la canción.",
                    path="track",
                    value=track,
                    hint="Pick a pattern track id (song.state) or target new_track.",
                )
            plan.track = existing.id
            lo0 = (existing.at_bar - 1) * BEATS_PER_BAR
            hi0 = ((existing.until_bar or song.meta.length_bars + 1) - 1) * BEATS_PER_BAR
        else:
            plan.track = new_track_id or _unique("pads", set(by_id))
            if plan.track in by_id:
                raise MuestraError.one(
                    "duplicate_id",
                    f"Ya hay un track «{plan.track}».",
                    path="new_track_id",
                    value=plan.track,
                    hint="Choose another id, or omit it (pads, pads-2, ...).",
                )
            plan.new_track = True
    end0 = hi0
    if until_bar is not None:
        end0 = min(end0, (until_bar - 1) * BEATS_PER_BAR)
    if repeat is None:
        repeat = max(1, math.ceil((end0 - start0) / span - 1e-9)) if until_bar else 1
    plan.repeat = repeat
    limit0 = min(end0, start0 + repeat * span, float(song_end0))

    def placed(evs: list[ProcessedEvent], lo: float, hi: float):
        out = []
        for k in range(repeat):
            for e in evs:
                if e.beat < -1e-9 or e.beat >= span - 1e-9:
                    if k == 0:
                        plan.dropped += 1
                    continue
                b0 = start0 + k * span + e.beat
                if b0 < lo - 1e-9 or b0 >= min(hi, limit0) - 1e-9:
                    plan.dropped += 1
                    continue
                out.append((round(b0 + 1, 6), e))
        return out

    # ---- lanes
    if drum_events:
        lanes_now = dict(existing.pattern.lanes) if existing is not None else {}
        taken = set(lanes_now)
        lane_specs: dict[str, dict] = {}
        for key in sorted({e.pad for e in drum_events}):
            pad = take.pads[key]
            hit_lane = next((n for n, ln in lanes_now.items() if _same_sound(ln, pad)), None)
            if hit_lane is None:
                hit_lane = _unique(lane_name(pad), taken)
                taken.add(hit_lane)
                lane_specs[hit_lane] = pad_lane(pad)
                plan.new_lanes.append(hit_lane)
            plan.lanes[key] = hit_lane
        hits = []
        for beat, e in placed(drum_events, lo0, hi0):
            pad = take.pads[e.pad]
            h: dict = {"beat": beat, "lane": plan.lanes[e.pad]}
            if e.velocity != 1.0:
                h["velocity"] = e.velocity
            if pad.mode == "gate" and e.length_beats:
                h["length_beats"] = min(64.0, e.length_beats)
            hits.append(h)
        hits.sort(key=lambda h: (h["beat"], h["lane"]))
        plan.hits = len(hits)
        if plan.new_track:
            plan.ops.append(
                {
                    "op": "insert",
                    "path": "tracks",
                    "value": {
                        "id": plan.track,
                        "pattern": {
                            "steps_per_bar": 16,
                            "bars": 1,
                            "lanes": lane_specs,
                            "hits": hits,
                        },
                    },
                }
            )
        else:
            for name, spec in lane_specs.items():
                plan.ops.append(
                    {
                        "op": "set",
                        "path": f"tracks.{plan.track}.pattern.lanes.{name}",
                        "value": spec,
                    }
                )
            for h in hits:
                plan.ops.append(
                    {"op": "insert", "path": f"tracks.{plan.track}.pattern.hits", "value": h}
                )

    # ---- notes
    for tid in sorted({take.pads[e.pad].note.track for e in note_events}):
        t = by_id.get(tid)
        evs = [e for e in note_events if take.pads[e.pad].note.track == tid]
        if t is None or t.instrument is None:
            plan.messages.append(f"El track «{tid}» no es un instrumento: dejé afuera sus notas.")
            plan.dropped += len(evs)
            continue
        if t.midi is not None:
            plan.messages.append(
                f"«{tid}» toca un archivo MIDI: no puedo sumarle notas; pasalo a notas primero."
            )
            plan.dropped += len(evs)
            continue
        lo = (t.at_bar - 1) * BEATS_PER_BAR
        hi = ((t.until_bar or song.meta.length_bars + 1) - 1) * BEATS_PER_BAR
        if t.notes is None:
            plan.ops.append({"op": "set", "path": f"tracks.{tid}.notes", "value": []})
        for beat, e in placed(evs, lo, hi):
            pad = take.pads[e.pad]
            length = e.length_beats if pad.mode == "gate" and e.length_beats else None
            length = length or ONESHOT_NOTE_BEATS
            for pitch in pad.note.pitches:
                p = pitch + round(pad.pitch_semitones)
                if not 0 <= p <= 127:
                    continue
                plan.ops.append(
                    {
                        "op": "insert",
                        "path": f"tracks.{tid}.notes",
                        "value": {
                            "beat": beat,
                            "pitch": p,
                            "length_beats": round(length, 6),
                            "velocity": max(1, min(127, round(e.velocity * 127))),
                        },
                    }
                )
                plan.notes += 1
        plan.track = plan.track or tid
    if plan.dropped:
        plan.messages.append(
            f"Dejé afuera {plan.dropped} "
            + ("golpe" if plan.dropped == 1 else "golpes")
            + " que caían fuera del lugar elegido (antes del comienzo o después del final)."
        )
    return plan


def pads_from_track(song: Song, track_id: str, keys: list[str]) -> list[Pad]:
    """ "Armar pads desde este track": one pad per lane of a drum track, in lane
    order, on ``keys`` (the UI passes its layout's key order)."""
    t = next((x for x in song.tracks if x.id == track_id), None)
    if t is None or t.pattern is None:
        raise MuestraError.one(
            "ref_not_found",
            f"«{track_id}» no es un track de batería.",
            path="track",
            value=track_id,
            hint="Use a pattern track id.",
        )
    out = []
    for key, (name, lane) in zip(keys, t.pattern.lanes.items(), strict=False):
        out.append(
            Pad(
                key=key,
                name=name,
                lane=name,
                sample=lane.sample,
                slice=lane.slice,
                gain_db=lane.gain_db,
                pan=lane.pan,
                pitch_semitones=lane.pitch_semitones,
                reverse=lane.reverse,
                choke=lane.choke,
            )
        )
    return out
