"""WP13 finding 3: chop repeats join without a level dip (sustained material)."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from muestra.render.core import arrange, beat_frame, render_audio, render_chop_loop


def _sustained(project):
    """Replace the 'other' stem with a sustained, dense pad (no attacks): 40
    partials 0.8-4 kHz, so a 1 ms RMS window is steady and any dip shows."""
    path = project.path("cache/stems/src1/other.wav")
    sr = sf.info(path).samplerate
    t = np.arange(round(5.7 * sr)) / sr
    rng = np.random.default_rng(3)
    tone = sum(
        0.02 * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
        for f in rng.uniform(800, 4000, 40)
    )
    sf.write(path, np.column_stack([tone, tone]), sr, subtype="FLOAT")


def _dip_db(x, at, sr):
    """Min 1 ms RMS within +-5 ms of ``at`` against the median over +-60 ms."""
    w = sr // 1000
    m = x.mean(axis=1)
    seg = m[at - 60 * w : at + 60 * w]
    env = np.sqrt(np.mean(seg[: len(seg) // w * w].reshape(-1, w) ** 2, axis=1))
    return 20 * np.log10(env[55:65].min() / np.median(env))


@pytest.mark.parametrize("mode", ["resample", "stretch"])
def test_no_rms_dip_at_repeat_seams_or_loop_wrap(project, song, mode):
    _sustained(project)
    song = song.model_copy(deep=True)
    song.chops[0].warp.mode = mode
    sample = song.tracks[0].model_copy(update={"fx": [], "gain_db": 0})
    song.tracks, song.automation = [sample], []
    sr = project.config.sample_rate
    n = beat_frame(song.meta.length_bars * 4, song.meta.bpm, sr)
    raw, _, _ = arrange(project, song, sr, n)
    x = raw["sample"]
    slot = beat_frame(song.chops[0].length_beats, song.meta.bpm, sr)
    dips = [_dip_db(x, k * slot, sr) for k in range(1, 8)]
    # interior (non-seam) spots of this pad measure about -4 dB on this metric;
    # before WP13 every repeat dipped -29 dB (stretch) / -47..-71 dB (resample)
    assert min(dips) > -6.0, dips
    wrap = np.concatenate([x[-sr // 10 :], x[: sr // 10]])
    assert _dip_db(wrap, sr // 10, sr) > -6.0
    # the full render (stems and mix) is a seamless loop too: no end fade
    mix, tracks, _, _ = render_audio(project, song, (1, song.meta.length_bars + 1))
    for audio in (mix, tracks["sample"]):
        seam = np.concatenate([audio[-sr // 10 :], audio[: sr // 10]])
        assert _dip_db(seam, sr // 10, sr) > -6.0
    # a partial range still ends with the 5 ms fade
    part = render_audio(project, song, (1, 3))[0]
    assert np.max(np.abs(part[-1])) < 1e-6


def test_chop_loop_preview_is_seamless_and_deterministic(project, song):
    _sustained(project)
    chop = song.chops[0]
    sr = project.config.sample_rate
    a = render_chop_loop(project, chop, sr, 120, 3)
    slot = beat_frame(chop.length_beats, 120, sr)
    assert len(a) == 3 * slot
    assert min(_dip_db(a, k * slot, sr) for k in (1, 2)) > -6.0
    for path in (project.cache_dir / "render/chops").glob("*.npy"):
        path.unlink()
    assert np.array_equal(a, render_chop_loop(project, chop, sr, 120, 3))
