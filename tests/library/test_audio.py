"""Probe measurements against synthesised signals with known properties."""

from __future__ import annotations

import numpy as np
import pytest

from muestra.library.audio import count_clipped, detect_bpm, detect_key, integrated_lufs, probe

from .conftest import SR, chord_loop, click_loop, hat, kick, snare, write


def test_kick_fundamental_and_decay_are_measured(tmp_path):
    # 60 Hz sine decaying exp(-t/0.08): -30 dB at 0.08*ln(31.6) = 276 ms
    p = probe(write(tmp_path / "k.wav", kick(60.0, 0.08)))
    f = p.features
    assert f.fundamental_hz == pytest.approx(60.0, abs=1.5)
    assert f.note == "B1"
    assert f.decay_ms == pytest.approx(276, abs=45)
    assert f.attack_ms is not None and f.attack_ms < 6
    assert f.sub > 0.95  # nearly all energy under 120 Hz
    assert f.spectral_centroid_hz < 100
    assert f.tonality > 0.6  # a pure tone


def test_longer_kick_has_longer_decay_and_lower_pitch(tmp_path):
    short = probe(write(tmp_path / "a.wav", kick(60.0, 0.08, dur=0.9))).features
    long = probe(write(tmp_path / "b.wav", kick(52.0, 0.30, dur=1.6))).features
    assert long.decay_ms > 2.5 * short.decay_ms
    assert long.fundamental_hz < short.fundamental_hz


def test_hat_is_bright_noisy_and_has_no_pitch(tmp_path):
    f = probe(write(tmp_path / "h.wav", hat(0.02, 0.2))).features
    assert f.spectral_centroid_hz > 8000
    assert f.high + f.air > 0.9
    assert f.tonality < 0.15  # noise
    assert f.fundamental_hz is None
    assert f.decay_ms < 120


def test_snare_has_a_pitched_body_and_a_noisy_top(tmp_path):
    f = probe(write(tmp_path / "s.wav", snare())).features
    k = probe(write(tmp_path / "k.wav", kick())).features
    assert f.fundamental_hz == pytest.approx(190.0, abs=6)  # the 190 Hz body
    assert f.spectral_centroid_hz > 10 * k.spectral_centroid_hz
    assert f.sub < 0.2 and f.high + f.air > 0.3


def test_levels_and_container_facts(tmp_path):
    x = kick(60.0, 0.08, peak=0.5)
    p = probe(write(tmp_path / "k.wav", x, subtype="PCM_16"))
    assert p.sample_rate == SR and p.channels == 1 and p.bit_depth == 16
    assert p.peak_dbfs == pytest.approx(20 * np.log10(np.abs(x).max()), abs=0.05)
    assert p.rms_dbfs < p.peak_dbfs
    assert not p.clipping
    assert p.duration_s == pytest.approx(0.5, abs=0.001)


def test_stereo_is_reported(tmp_path):
    x = kick()
    st = np.stack([x, x * 0.5], axis=1)
    p = probe(write(tmp_path / "st.wav", st))
    assert p.channels == 2 and p.side_ratio > 0.1


def test_clipping_needs_runs_of_full_scale_samples(tmp_path):
    clipped = np.clip(kick(60.0, 0.1) * 3.0, -1.0, 1.0)
    assert probe(write(tmp_path / "c.wav", clipped)).clipping
    # a normalised peak (one full-scale sample) is NOT clipping
    x = kick(60.0, 0.1)
    x = x / np.abs(x).max()
    assert not probe(write(tmp_path / "n.wav", x)).clipping
    assert count_clipped(np.array([[1.0], [0.0], [1.0], [0.2]])) == 0
    assert count_clipped(np.array([[1.0], [1.0], [1.0], [0.2]])) == 0  # too short
    assert count_clipped(np.array([[1.0], [1.0], [1.0], [1.0], [0.2]])) == 4
    # four hot samples that are not flat (a natural peak) do not count
    assert count_clipped(np.array([[0.9995], [0.9999], [0.9996], [0.9992], [0.2]])) == 0


def test_loudness_orders_with_level():
    t = np.arange(2 * SR) / SR
    loud = (np.sin(2 * np.pi * 440 * t) * 0.5)[:, None]
    quiet = loud * 0.1
    a, b = integrated_lufs(loud, SR), integrated_lufs(quiet, SR)
    assert a - b == pytest.approx(20.0, abs=0.2)
    # BS.1770: a full-scale-ish 1 kHz tone reads about -3 LUFS per channel pair
    assert -20 < a < 0


def test_digital_silence_has_no_lufs():
    assert integrated_lufs(np.zeros((SR, 1)), SR) is None


@pytest.mark.parametrize("bpm", [90.0, 120.0, 128.0, 140.0])
def test_click_loop_bpm_is_detected(bpm):
    x = click_loop(bpm, bars=4)
    got, conf, periodic = detect_bpm(x, SR)
    assert got == pytest.approx(bpm, abs=0.7)
    assert conf > 0.5 and periodic


def test_noise_has_no_confident_tempo():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(5 * SR) * 0.1
    _bpm, conf, periodic = detect_bpm(x, SR)
    assert not periodic or conf < 0.5


def test_key_of_a_minor_triad():
    key, conf = detect_key(chord_loop(220.0, True, 6.0), SR)
    assert key == "Am"
    assert conf > 0.3


def test_key_of_a_major_triad():
    key, _conf = detect_key(chord_loop(220.0, False, 6.0), SR)
    assert key == "A"


def test_probe_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav at all")
    with pytest.raises(Exception):  # noqa: B017 - whatever soundfile raises; the indexer reports it
        probe(bad)


def test_probe_computes_bpm_only_when_asked(tmp_path):
    f = write(tmp_path / "l.wav", click_loop(120.0, 4))
    assert probe(f).bpm is None
    p = probe(f, want_bpm=True)
    assert p.bpm == pytest.approx(120.0, abs=0.7) and p.periodic
