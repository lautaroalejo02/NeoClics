"""Take processing (WP17): quantise, clean-up and placement — pure functions.

Mirrored line for line by ``ui/src/pads/quantize.ts`` (shared test vectors:
``fixtures/pads/quantize-cases.json``), so what the browser plays for a take
is what ``pads.commit`` writes into song.yaml.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..schemas.pads import Take, TakeEvent, TakeQuantize

__all__ = [
    "GRID_BEATS",
    "quantize_beat",
    "ProcessedEvent",
    "process_events",
    "Cleanup",
]

#: grid step in beats (quarter notes)
GRID_BEATS: dict[str, float | None] = {
    "off": None,
    "1/16": 0.25,
    "1/8": 0.5,
    "1/8t": 1.0 / 3.0,
    "1/16t": 1.0 / 6.0,
}


def _round(x: float, digits: int) -> float:
    """Half-up rounding like JavaScript's Math.round (Python's round() is
    half-to-even): the browser mirror gets the same numbers."""
    k = 10.0**digits
    return math.floor(x * k + 0.5) / k


def _grid_point(i: int, step: float, swing: float, straight: bool) -> float:
    return i * step + (swing * step / 3.0 if straight and i % 2 else 0.0)


def quantize_beat(beat: float, q: TakeQuantize, wrap: float | None = None) -> float:
    """``beat`` moved ``strength`` of the way to the nearest (swung) grid point.

    ``wrap`` (loop takes): the result is folded into ``[0, wrap)`` so a press a
    hair before the loop start lands on beat 0 of the cycle. Rounded to 1e-6
    beats (the precision ``song.edit`` stores)."""
    step = GRID_BEATS[q.grid]
    target = beat
    if step is not None and q.strength > 0:
        straight = q.grid in ("1/16", "1/8")
        i0 = math.floor(beat / step)
        best = None
        for i in (i0 - 1, i0, i0 + 1, i0 + 2):
            p = _grid_point(i, step, q.swing, straight)
            d = abs(p - beat)
            if best is None or d < best[0] - 1e-12:
                best = (d, p)
        target = best[1]
    out = beat + q.strength * (target - beat)
    if wrap:
        out = out % wrap
        if out > wrap - 1e-6:
            out = 0.0
    return _round(out, 6)


@dataclass(frozen=True)
class Cleanup:
    """Optional clean-up of a played take ("limpiame el ritmo").

    ``dedupe_ms``: two presses of the same pad closer than this (as played)
    are one hit (the louder is kept: a double trigger / flam by accident).
    ``velocity``: ``as_played`` | ``fixed`` (every hit at ``velocity_value``) |
    ``even`` (halve the spread around the take's mean: keeps accents, removes
    unevenness)."""

    dedupe_ms: float = 0.0
    velocity: str = "as_played"
    velocity_value: float = 0.8


@dataclass(frozen=True)
class ProcessedEvent:
    beat: float  # 0-based take beats, quantised
    raw_beat: float
    pad: str
    velocity: float
    length_beats: float | None
    cycle: int


def process_events(
    take: Take, quantize: TakeQuantize | None = None, cleanup: Cleanup | None = None
) -> list[ProcessedEvent]:
    """Raw events -> dedupe -> quantise -> velocity, sorted by beat then pad."""
    q = quantize or take.quantize
    c = cleanup or Cleanup()
    events: list[TakeEvent] = sorted(take.events, key=lambda e: (e.beat, e.pad))
    if c.dedupe_ms > 0:
        window = c.dedupe_ms / 1000.0 * take.bpm / 60.0  # ms -> beats
        kept: list[TakeEvent] = []
        last: dict[str, int] = {}
        for e in events:
            j = last.get(e.pad)
            if j is not None and e.beat - kept[j].beat < window - 1e-12:
                if e.velocity > kept[j].velocity:
                    kept[j] = kept[j].model_copy(update={"velocity": e.velocity})
                continue
            last[e.pad] = len(kept)
            kept.append(e)
        events = kept
    vels = [e.velocity for e in events]
    mean = sum(vels) / len(vels) if vels else 0.0
    out = []
    for e in events:
        v = e.velocity
        if c.velocity == "fixed":
            v = c.velocity_value
        elif c.velocity == "even":
            v = mean + (v - mean) * 0.5
        v = min(1.0, max(0.01, _round(v, 4)))
        out.append(
            ProcessedEvent(
                beat=quantize_beat(e.beat, q, take.loop_beats),
                raw_beat=e.beat,
                pad=e.pad,
                velocity=v,
                length_beats=None if e.length_beats is None else _round(e.length_beats, 6),
                cycle=e.cycle,
            )
        )
    out.sort(key=lambda e: (e.beat, e.pad))
    return out


def take_span_beats(take: Take) -> float:
    """Beats one pass of the take occupies when placed: its loop, else its
    length rounded up to whole bars."""
    if take.loop_beats:
        return take.loop_beats
    return max(4.0, math.ceil(take.length_beats / 4.0 - 1e-9) * 4.0)
