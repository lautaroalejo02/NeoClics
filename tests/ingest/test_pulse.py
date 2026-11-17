import numpy as np
import pytest

from muestra.ingest import grid
from muestra.ingest.pulse import PulseEvidence, pulse_evidence, supported_half_phase
from muestra.ingest.reliability import assess_reliability
from muestra.schemas.analysis import Analysis


def drum_signal(beats, duration=24, sr=8000, shuffle=False):
    audio = np.zeros(round(duration * sr))
    generator = np.random.default_rng(19)
    times = list(beats)
    if shuffle:
        times += [
            first + (last - first) * 2 / 3 for first, last in zip(beats, beats[1:], strict=False)
        ]
    for index, beat in enumerate(times):
        position = round(beat * sr)
        local = np.arange(round(sr * 0.06)) / sr
        pulse = (
            np.sin(2 * np.pi * 70 * local) if index % 2 == 0 else generator.normal(size=len(local))
        )
        pulse = pulse * np.exp(-local * 65)
        audio[position : position + len(pulse)] += pulse
    return np.column_stack([audio, audio]), sr


@pytest.mark.parametrize("shuffle", [False, True])
def test_onset_evidence_preserves_drift_pickup_and_shuffle(shuffle):
    beats = 4.25 + np.r_[0, np.cumsum(np.linspace(0.48, 0.56, 31))]
    audio, sr = drum_signal(beats, shuffle=shuffle)
    pulse = pulse_evidence(audio, sr)
    assert not pulse.supports(np.arange(0.25, 4, 0.5), 0.25, 2.25)
    for first in (0, 4, 8, 12):
        assert pulse.supports(beats, beats[first], beats[first + 4])
    restored = PulseEvidence.from_dict(pulse.to_dict())
    assert np.allclose(restored.at(beats), pulse.at(beats), atol=1e-6)


def test_a_sustained_tone_does_not_make_a_reliable_intro():
    beats = np.arange(0.25, 23, 0.5)
    time = np.arange(24 * 8000) / 8000
    audio = 0.1 * np.sin(2 * np.pi * 440 * time)
    pulse = pulse_evidence(np.column_stack([audio, audio]), 8000)
    analysis = Analysis(
        source_id="src1",
        file="sources/test.wav",
        duration_s=24,
        sample_rate=8000,
        bpm_global=120,
        beats_s=beats.tolist(),
        downbeats_s=beats[::4].tolist(),
    )
    assert assess_reliability(analysis).confidence == 1
    assert assess_reliability(analysis, pulse).confidence == 0


def test_half_time_phase_uses_attacks_not_mislabelled_downbeats():
    raw_beats = np.arange(0.25, 20, 0.25)
    audio, sr = drum_signal(raw_beats[1::2])
    pulse = pulse_evidence(audio, sr)
    analysis = Analysis(
        source_id="src1",
        file="sources/test.wav",
        duration_s=24,
        sample_rate=sr,
        bpm_global=240,
        beats_s=raw_beats.tolist(),
        downbeats_s=raw_beats[::4].tolist(),
    )
    corrected = grid.auto_correct(analysis, beat_strength=pulse.at(raw_beats))
    assert corrected.grid.beat_phase == 1
    assert np.allclose(corrected.beats_s, raw_beats[1::2])
    assert corrected.grid.ask_human


def test_ambiguous_or_short_evidence_does_not_invent_parity():
    assert supported_half_phase(np.ones(64), 1) == (1, False)
    assert supported_half_phase(np.tile([0, 1], 4), 0) == (0, False)
    assert supported_half_phase(np.zeros(64), 0) == (0, False)


def test_regular_meter_is_not_a_verified_bar_phase():
    beats = np.arange(0.25, 20, 0.5)
    analysis = Analysis(
        source_id="src1",
        file="sources/test.wav",
        duration_s=24,
        sample_rate=8000,
        bpm_global=120,
        beats_s=beats.tolist(),
        downbeats_s=beats[::4].tolist(),
    )
    corrected = grid.auto_correct(analysis)
    assert corrected.grid.bar_phase_confidence <= 0.55
    assert corrected.grid.ask_human
