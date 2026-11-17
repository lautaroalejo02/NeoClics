"""WP12: on dense live material the clip-track timing check says it could not
verify the grid (info) instead of a coincidental grid_offset warning."""

from __future__ import annotations

import numpy as np

from muestra.ears import dsp, timing
from muestra.ears.core import analyze
from muestra.ears.corpus import CorpusSpec, Defects, generate
from muestra.project import open_project

from .conftest import FAST


def _metrics(tmp_path, monkeypatch=None, onset_times=None):
    generate(CorpusSpec(defects=Defects(), **FAST), tmp_path)
    project = open_project(tmp_path)
    if monkeypatch is not None:
        real = dsp.onsets

        def fake(y, sr, **kw):
            out = real(y, sr, **kw)
            if len(y) and onset_times is not None:
                times = onset_times(len(y) / sr)
                return dsp.Onsets(times, np.ones_like(times))
            return out

        monkeypatch.setattr(timing.dsp, "onsets", fake)
    return analyze(project, "v001")


def test_clean_corpus_sample_is_verified_with_high_confidence(tmp_path):
    m = _metrics(tmp_path)
    al = m.alignment.tracks["sample"]
    assert al.verified is True and al.confidence is not None and al.confidence > 0.5
    assert not any(a.code == "grid_unverified" for a in m.alarms)


def test_dense_jittered_onsets_are_reported_unverified_not_alarmed(tmp_path, monkeypatch):
    rng = np.random.default_rng(3)

    def ragtime(duration):
        # an onset on every 16th, swung and jittered like a live 1928 band, plus
        # ornaments a 32nd before many beats: which ones fall inside the on-beat
        # window depends on the shift itself.
        spb = 60 / 120
        grid = np.arange(0, duration - 0.05, spb / 4)
        swing = np.where((np.arange(grid.size) % 2) == 1, 0.03, 0.0)
        orn = grid[::4][rng.random(grid[::4].size) < 0.6] - spb / 8 + 0.004
        t = np.concatenate([grid + swing + rng.normal(0, 0.012, grid.size), orn])
        return np.sort(t[(t > 0.01) & (t < duration - 0.01)])

    m = _metrics(tmp_path, monkeypatch, ragtime)
    al = m.alignment.tracks["sample"]
    assert al.verified is False, al
    assert al.onsets_per_beat is not None and al.onsets_per_beat >= 3
    notes = [a for a in m.alarms if a.code == "grid_unverified"]
    assert len(notes) == 1 and notes[0].severity == "info"
    assert "No pude verificar" in notes[0].message
    assert not any(a.code in ("grid_offset", "grid_drift") for a in m.alarms)
