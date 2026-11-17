import numpy as np

from muestra.ingest.chops import loop_continuity, score_loop


def test_a_single_bad_join_sample_is_not_hidden_by_matching_continuation():
    sr = 8000
    time = np.arange(6 * sr) / sr
    tone = 0.1 * np.sin(2 * np.pi * 220 * time)
    good = np.column_stack([tone, tone])
    bad = good.copy()
    bad[3 * sr - 1] += 0.7
    beats = np.arange(0, 6, 0.5)
    assert loop_continuity(good, sr, 1, 3) > 0.95
    assert loop_continuity(bad, sr, 1, 3) < 0.02
    scores = [score_loop(audio, sr, 1, 3, beats, "mix").value for audio in (good, bad)]
    assert scores[0] > scores[1] + 0.2


def test_longer_horizon_catches_a_delayed_harmonic_mismatch():
    sr = 8000
    time = np.arange(6 * sr) / sr
    tone = 0.1 * np.sin(2 * np.pi * 220 * time)
    good = np.column_stack([tone, tone])
    bad = good.copy()
    first, last = round(3.12 * sr), round(3.32 * sr)
    bad[first:last] = (0.1 * np.sin(2 * np.pi * 800 * time[first:last]))[:, None]
    assert np.array_equal(good[3 * sr : round(3.08 * sr)], bad[3 * sr : round(3.08 * sr)])
    assert loop_continuity(good, sr, 1, 3) > loop_continuity(bad, sr, 1, 3)
