"""WP11 regressions found on real renderer output (docs/ears.md)."""

from __future__ import annotations

import numpy as np

from muestra.ears.clicks import _onset_rise_db
from muestra.ears.config import Thresholds


def test_note_onset_out_of_a_release_tail_is_an_attack_not_a_click():
    sr = 48000
    t = np.arange(int(0.05 * sr)) / sr
    tail = 0.005 * np.sin(2 * np.pi * 78 * t)  # previous note's release, ~-49 dBFS
    note = 0.3 * np.cos(2 * np.pi * 78 * t)  # hard start: a real step
    y = np.concatenate([tail, note])
    rise = _onset_rise_db(y, tail.size, sr)
    assert rise >= Thresholds().click_onset_rise_db  # skipped: the preset's attack


def test_retrigger_over_a_sustained_note_is_still_scanned():
    sr = 48000
    t = np.arange(int(0.05 * sr)) / sr
    sustained = 0.3 * np.sin(2 * np.pi * 78 * t)
    retrigger = 0.3 * np.cos(2 * np.pi * 78 * t)  # phase jump at full level
    y = np.concatenate([sustained, retrigger])
    assert _onset_rise_db(y, sustained.size, sr) < Thresholds().click_onset_rise_db
