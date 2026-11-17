"""DSP primitives against known signals (EBU Tech 3341/3342, BS.1770-4)."""

from __future__ import annotations

import numpy as np
import pytest

from muestra.ears import dsp

SR = 48000


def sine(freq, dbfs, seconds, sr=SR, phase=0.0):
    t = np.arange(int(seconds * sr)) / sr
    s = 10 ** (dbfs / 20) * np.sin(2 * np.pi * freq * t + phase)
    return np.column_stack([s, s])


def test_k_weighting_matches_bs1770_table_at_48k():
    sos = dsp.k_weighting_sos(48000)
    shelf_b = [1.53512485958697, -2.69169618940638, 1.19839281085285]
    shelf_a = [1.0, -1.69065929318241, 0.73248077421585]
    hp_a = [1.0, -1.99004745483398, 0.99007225036621]
    np.testing.assert_allclose(sos[0, :3], shelf_b, atol=1e-7)
    np.testing.assert_allclose(sos[0, 3:], shelf_a, atol=1e-7)
    np.testing.assert_allclose(sos[1, :3], [1.0, -2.0, 1.0])
    np.testing.assert_allclose(sos[1, 3:], hp_a, atol=1e-7)


@pytest.mark.parametrize("sr", [48000, 44100])
def test_ebu_3341_case_1_and_2(sr):
    for level in (-23.0, -33.0):
        x = sine(1000, level, 20, sr)
        assert dsp.integrated_loudness(x, sr) == pytest.approx(level, abs=0.1)


def test_ebu_3341_case_3_relative_gate():
    x = np.concatenate([sine(1000, -36, 10), sine(1000, -23, 60), sine(1000, -36, 10)])
    assert dsp.integrated_loudness(x, SR) == pytest.approx(-23.0, abs=0.1)


def test_ebu_3341_case_4_absolute_gate():
    parts = [(-72, 10), (-36, 10), (-23, 60), (-36, 10), (-72, 10)]
    x = np.concatenate([sine(1000, lv, d) for lv, d in parts])
    assert dsp.integrated_loudness(x, SR) == pytest.approx(-23.0, abs=0.1)


@pytest.mark.parametrize(("a", "b", "lra"), [(-20, -30, 10.0), (-20, -15, 5.0), (-40, -20, 20.0)])
def test_ebu_3342_loudness_range(a, b, lra):
    x = np.concatenate([sine(1000, a, 20), sine(1000, b, 20)])
    assert dsp.loudness_range(dsp.short_term_loudness(x, SR)) == pytest.approx(lra, abs=1.0)


def test_silence_is_floor_not_nan():
    x = np.zeros((SR, 2))
    assert dsp.integrated_loudness(x, SR) == dsp.FLOOR_DB
    assert dsp.loudness_range(dsp.short_term_loudness(np.zeros((4 * SR, 2)), SR)) is None


def test_true_peak_finds_intersample_peak():
    # fs/4 at 45 degrees: every sample is at 0.707, the waveform peaks at 1.0
    x = sine(SR / 4, 0.0, 1.0, phase=np.pi / 4)
    assert dsp.db(np.max(np.abs(x))) == pytest.approx(-3.01, abs=0.01)
    assert dsp.db(dsp.true_peak(x, SR)) == pytest.approx(0.0, abs=0.2)


def test_clipped_samples_counts_flat_tops_only():
    x = np.zeros((100, 1))
    x[10, 0] = 1.0  # isolated full-scale sample: hot, not clipped
    x[50:55, 0] = 1.0  # flat top
    total, in_runs = dsp.clipped_samples(x)
    assert (total, in_runs) == (6, 5)


def test_tuning_cents_on_detuned_tones():
    t = np.arange(2 * SR) / SR
    for cents in (0.0, 30.0, -15.0):
        f = 440.0 * 2 ** (cents / 1200)
        y = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 8))
        measured, strength = dsp.tuning_cents(y, SR)
        assert measured == pytest.approx(cents, abs=3.0)
        assert strength > 0.3


def test_key_estimate_on_harmonic_triads():
    t = np.arange(4 * SR) / SR

    def chord(pcs):
        return sum(
            np.sin(2 * np.pi * 440 * 2 ** ((60 + pc - 69) / 12) * k * t) / k
            for pc in pcs
            for k in range(1, 6)
        )

    # i - VI - VII - i in A minor (A C E / F A C / G B D)
    y = np.concatenate([chord(c) for c in ((9, 12, 16), (5, 9, 12), (7, 11, 14), (9, 12, 16))])
    pc, mode, _r, conf = dsp.estimate_key(dsp.chroma(y, SR))
    assert (pc, mode) in {(9, "minor"), (0, "major")}  # A minor or its relative C major
    assert conf > 0.2


def test_attack_times_on_one_shots():
    y = np.zeros(SR)
    rng = np.random.default_rng(1)
    hits = [0.1, 0.35, 0.6]
    for h in hits:
        i = int(h * SR)
        n = int(0.05 * SR)
        y[i : i + n] += rng.standard_normal(n) * np.exp(-np.arange(n) / (0.01 * SR))
    found = dsp.attack_times(y, SR, np.array(hits) - 0.01)  # expected 10 ms early
    np.testing.assert_allclose(found, hits, atol=0.0015)


def test_edge_discontinuity_step_vs_smooth():
    t = np.arange(SR) / SR
    smooth = 0.3 * np.sin(2 * np.pi * 220 * t)
    step = smooth.copy()
    i = SR // 2
    step[i:] = 0.3 * np.sin(2 * np.pi * 220 * t[i:] + 2.0)  # phase jump = hard cut
    r_smooth, j_smooth, _ = dsp.edge_discontinuity(smooth, i, SR)
    r_step, j_step, at = dsp.edge_discontinuity(step, i, SR)
    assert r_smooth < 2.0
    assert r_step > 20.0 and abs(at - i) <= 2
