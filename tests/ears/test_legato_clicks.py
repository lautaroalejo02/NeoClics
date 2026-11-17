"""WP12 (skill finding 10): an FM-slap retrigger over a still-sounding note is the
preset's attack, not a click; a hard cut is still a click."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from muestra.ears.clicks import measure_clicks
from muestra.ears.config import EarsConfig
from muestra.ears.context import Event

SR = 48000


def _slap(freq: float, dur: float) -> np.ndarray:
    """A bass note with a bright, fast 'slap' transient (like Surge's FM Slap)."""
    t = np.arange(int(dur * SR)) / SR
    body = 0.25 * np.sin(2 * np.pi * freq * t) * np.exp(-t * 3)
    click_like = 0.12 * np.sin(2 * np.pi * 2500 * t) * np.exp(-t * 900)
    return (body + click_like).astype(np.float64)


def _ctx(y: np.ndarray, notes: list[Event]):
    track = SimpleNamespace(id="bass", mute=False, pattern=None, clips=None, instrument=object())
    timeline = SimpleNamespace(
        notes=lambda tid: notes,
        clip_instances=lambda tid: [],
        where=lambda t: (int(t // 2) + 1, 1.0),
    )
    song = SimpleNamespace(tracks=[track], chops=[])
    return SimpleNamespace(
        config=EarsConfig(),
        sr=SR,
        timeline=timeline,
        song=song,
        stems={"bass": True},
        mono=lambda tid: y,
        track=lambda tid: track,
    )


def _note(y, t0, f, d):
    seg = _slap(f, d + 0.2)
    seg[int(d * SR) :] *= np.linspace(1, 0, seg.size - int(d * SR))  # natural release
    i = int(t0 * SR)
    y[i : i + seg.size] += seg
    return Event(t=t0, pitch=0, dur_s=d)


def _line(cut_last: bool = False, cut_legato: bool = False):
    """Notes out of silence, then a legato pair (end == next start), then optionally a
    note cut dead while loud, or a legato pair whose first note is cut dead."""
    y = np.zeros(int(6 * SR))
    notes = [_note(y, t0, f, d) for t0, f, d in ((0.5, 55, 0.3), (1.25, 73, 0.3), (2.0, 82, 0.3))]
    # legato: the previous note is still sounding (releasing) when the next attack lands
    notes += [_note(y, 3.0, 65, 0.25), _note(y, 3.25, 98, 0.4)]
    if cut_last:
        sustained = 0.3 * np.sin(2 * np.pi * 55 * np.arange(int(0.5 * SR)) / SR + 1.0)
        y[int(4.5 * SR) : int(4.5 * SR) + sustained.size] = sustained  # ends dead at 5.0 s
        notes.append(Event(t=4.5, pitch=0, dur_s=0.5))
    if cut_legato:
        held = 0.3 * np.sin(2 * np.pi * 55 * np.arange(int(0.25 * SR)) / SR)  # ends at -0.3
        y[int(5.25 * SR) : int(5.5 * SR)] = held  # cut dead at 5.5 s ...
        nxt = _slap(98, 0.3)
        y[int(5.5 * SR) : int(5.5 * SR) + nxt.size] += nxt  # ... where the next note starts
        notes += [Event(t=5.25, pitch=0, dur_s=0.25), Event(t=5.5, pitch=0, dur_s=0.3)]
    return y, notes


def test_legato_retrigger_with_the_presets_own_transient_is_not_a_click():
    y, notes = _line(cut_last=False)
    report, alarms = measure_clicks(_ctx(y, notes))
    assert report.count == 0, report.events
    assert not alarms


def test_a_hard_cut_is_still_a_click():
    y, notes = _line(cut_last=True)
    report, alarms = measure_clicks(_ctx(y, notes))
    assert report.count >= 1
    assert any(abs(e.at_s - 5.0) < 0.01 for e in report.events), report.events
    assert alarms and alarms[0].code == "click"


def test_a_note_cut_dead_under_a_legato_attack_is_still_a_click():
    y, notes = _line(cut_legato=True)
    report, _ = measure_clicks(_ctx(y, notes))
    assert any(abs(e.at_s - 5.5) < 0.01 for e in report.events), report.events
