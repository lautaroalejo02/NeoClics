"""WP13 finding 10: curated aliases share a loudness (per-alias trims)."""

from __future__ import annotations

import numpy as np
import pyloudnorm as pyln
import pytest

from muestra.errors import MuestraError
from muestra.plugins.core import instrument_spec, render_instrument
from muestra.plugins.presets import ALIAS_REFERENCE_LUFS, CURATED, CURATED_TRIM_DB
from muestra.schemas.song import Instrument, MidiNote

SR, BPM = 48000, 120
BASS = [MidiNote(beat=1 + i, pitch=(33, 36, 38, 40)[i % 4], length_beats=0.9) for i in range(16)]
KEYS = [MidiNote(beat=1 + 2 * i, pitch=n, length_beats=1.8) for i in range(8) for n in (60, 64, 67)]


def test_every_curated_alias_has_a_measured_trim():
    assert set(CURATED_TRIM_DB) == set(CURATED)


@pytest.mark.real_plugin
@pytest.mark.parametrize("alias", sorted(CURATED))
def test_alias_reference_phrase_loudness(project, alias):
    instrument = Instrument(plugin=alias)
    try:
        instrument_spec(project, instrument)
    except MuestraError:
        pytest.skip(f"{alias} is not installed here (Surge XT / SFZ samples).")
    notes = BASS if "bass" in alias else KEYS
    audio = render_instrument(project, instrument, notes, SR, round(17 * 60 / BPM * SR), bpm=BPM)
    loudness = pyln.Meter(SR).integrated_loudness(audio.astype(np.float64))
    # before WP13 the three basses spanned 15 LU (finger -34.3, slap -19.2, synth -31.7)
    assert loudness == pytest.approx(ALIAS_REFERENCE_LUFS, abs=1.0)
    assert np.max(np.abs(audio)) < 1.0
