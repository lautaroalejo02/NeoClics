from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import butter, resample_poly, sosfilt

from muestra.plugins import core as plugins
from muestra.render.automation import Automation, pan_gain
from muestra.render.core import arrange, beat_frame, read_notes
from muestra.render.dsp import BLOCK, apply_fx, limiter_latency, master
from muestra.schemas.song import Song


def simple_song(fx=(), automation=(), bars=2):
    return Song.model_validate(
        {
            "meta": {"title": "test", "bpm": 120, "key": "Am", "length_bars": bars},
            "tracks": [{"id": "sample", "clips": [], "fx": list(fx)}],
            "automation": list(automation),
        }
    )


def run_fx(project, song, audio, sr=22050, events=None, tracks=None):
    return apply_fx(
        audio,
        song.tracks[0].fx,
        sr,
        song.meta.bpm,
        Automation(song, sr),
        "tracks.sample",
        project,
        events or {},
        tracks or {},
    )


@pytest.mark.parametrize("curve,expected", [("lin", 500), ("exp", 300), ("step", 100)])
def test_automation_holds_interpolates_and_jumps(curve, expected):
    song = simple_song(
        fx=[{"type": "lowpass", "id": "lp", "cutoff_hz": 2000}],
        automation=[
            {
                "target": "tracks.sample.fx.lp.cutoff_hz",
                "curve": curve,
                "points": [[1.5, 100], [2.5, 900], [2.5, 1000], [3, 2000]],
            }
        ],
    )
    auto = Automation(song, 22050)

    def frames(bar):
        return (bar - 1) * 44100

    target = "tracks.sample.fx.lp.cutoff_hz"
    assert auto.value(target, frames(1), 0) == 100
    assert auto.value(target, frames(2), 0) == pytest.approx(expected)
    assert auto.value(target, frames(2.5), 0) == 1000
    assert auto.value(target, frames(4), 0) == 2000


@pytest.mark.parametrize("kind", ["lowpass", "highpass"])
@pytest.mark.parametrize("order", [2, 4, 6, 8])
def test_filter_matches_butterworth_and_order(project, kind, order):
    sr = 22050
    audio = np.random.default_rng(42).normal(0, 0.1, (sr, 2)).astype(np.float32)
    song = simple_song(fx=[{"type": kind, "cutoff_hz": 1600, "order": order}])
    actual = run_fx(project, song, audio)
    expected = sosfilt(butter(order, 1600, kind, fs=sr, output="sos"), audio, axis=0)
    assert np.max(np.abs(actual - expected)) < 5e-8


def test_filter_sweep_spectrum_and_control_boundary_clicks(project):
    sr = 22050
    t = np.arange(sr * 4) / sr
    audio = 0.05 * np.sin(2 * np.pi * 150 * t) + 0.1 * np.sin(2 * np.pi * 5000 * t)
    audio = np.column_stack([audio, audio]).astype(np.float32)
    song = simple_song(
        fx=[{"type": "lowpass", "id": "lp", "cutoff_hz": 10000}],
        automation=[
            {
                "target": "tracks.sample.fx.lp.cutoff_hz",
                "curve": "exp",
                "points": [[1, 400], [3, 10000]],
            }
        ],
    )
    result = run_fx(project, song, audio)

    def band(a):
        spectrum = np.abs(np.fft.rfft(a * np.hanning(len(a)))) ** 2
        hz = np.fft.rfftfreq(len(a), 1 / sr)
        return spectrum[(hz > 4800) & (hz < 5200)].sum()

    assert band(result[-sr // 2 :, 0]) > band(result[sr // 4 : sr // 2, 0]) * 1000
    jumps = np.abs(np.diff(result[:, 0]))
    at_boundaries = jumps[BLOCK - 1 :: BLOCK]
    elsewhere = np.delete(jumps, np.arange(BLOCK - 1, len(jumps), BLOCK))
    assert np.quantile(at_boundaries, 0.99) < np.quantile(elsewhere, 0.99) * 1.1


def test_sidechain_key_lane_and_recovery(project):
    sr = 22050
    song = simple_song(
        fx=[
            {
                "type": "sidechain",
                "key_track": "drums",
                "key_lane": "kick",
                "depth": 0.8,
                "attack_ms": 1,
                "release_ms": 100,
            }
        ]
    )
    audio = np.ones((sr * 2, 2), np.float32) * 0.2
    hits = [0, sr // 2, sr, 3 * sr // 2]
    events = {("drums", "kick"): hits, ("drums", None): sorted(hits + [sr // 4])}
    result = run_fx(project, song, audio, events=events, tracks={"drums": audio})
    for hit in hits:
        assert result[hit + 30, 0] < 0.05
        assert result[hit + 8000, 0] > 0.19
    assert result[sr // 4 + 30, 0] > 0.18  # clap must not trigger kick-only ducking


def test_sidechain_follower_fallback(project):
    sr = 22050
    song = simple_song(
        fx=[
            {
                "type": "sidechain",
                "key_track": "key",
                "depth": 0.8,
                "attack_ms": 1,
                "release_ms": 50,
            }
        ]
    )
    audio = np.ones((sr, 2), np.float32) * 0.2
    key = np.zeros_like(audio)
    key[1000:2000] = 1
    result = run_fx(project, song, audio, tracks={"key": key})
    assert result[1500, 0] < 0.05
    assert result[-1, 0] > 0.199


@pytest.mark.parametrize("sr", [22050, 44100, 48000])
@pytest.mark.parametrize("automated", [False, True])
def test_limiter_latency_alignment_and_peak(project, sr, automated):
    automation = (
        [
            {
                "target": "tracks.sample.fx.limit.ceiling_db",
                "curve": "lin",
                "points": [[1, -6], [3, -3]],
            }
        ]
        if automated
        else []
    )
    song = simple_song(
        fx=[{"type": "limiter", "id": "limit", "ceiling_db": -3}], automation=automation
    )
    audio = np.zeros((sr, 2), np.float32)
    audio[1000] = 0.1
    audio[-100] = 0.1
    result = run_fx(project, song, audio, sr)
    assert np.argmax(result[: sr // 2, 0]) == 1000
    assert result[-100, 0] == pytest.approx(0.1, abs=1e-6)  # flushed tail
    assert len(result) == len(audio)
    assert limiter_latency(sr) > round(sr * 0.005)
    audio[1000] = 3
    result = run_fx(project, song, audio, sr)
    assert np.max(np.abs(result)) <= 10 ** (-3 / 20) + 1e-6


@pytest.mark.parametrize(
    "fx",
    [
        {"type": "gain", "gain_db": -6},
        {"type": "compressor", "threshold_db": -24, "ratio": 4, "knee_db": 6},
        {"type": "delay", "time_beats": 0.25, "feedback": 0.4, "mix": 0.5},
        {"type": "reverb"},
        {"type": "chorus"},
        {"type": "phaser"},
        {"type": "saturation", "drive_db": 12},
    ],
)
def test_native_and_dynamics_effects_are_finite_deterministic(project, fx):
    rng = np.random.default_rng(42)
    audio = rng.normal(0, 0.2, (22050, 2)).astype(np.float32)
    song = simple_song(fx=[fx])
    a, b = run_fx(project, song, audio), run_fx(project, song, audio)
    assert np.isfinite(a).all() and a.shape == audio.shape
    assert np.array_equal(a, b)
    assert np.max(np.abs(a - audio)) > 0.001


def test_automated_delay_retains_state(project):
    song = simple_song(
        fx=[{"type": "delay", "id": "echo", "time_beats": 0.2, "feedback": 0.5, "mix": 0.5}],
        automation=[
            {"target": "tracks.sample.fx.echo.mix", "curve": "lin", "points": [[1, 0.5], [3, 0.6]]}
        ],
    )
    audio = np.zeros((22050, 2), np.float32)
    audio[200] = 0.2
    result = run_fx(project, song, audio)
    assert np.max(np.abs(result[2000:5000])) > 0.001


def test_plugin_fx_params_and_preserved_state(project, monkeypatch):
    calls = []

    class Fake:
        amount = 0

        def process(self, audio, sample_rate, reset=False):
            calls.append((reset, self.amount))
            return audio * self.amount

    monkeypatch.setattr(plugins, "build_plugin_fx", lambda project, fx: Fake())
    song = simple_song(
        fx=[{"type": "plugin", "id": "p", "plugin": "test", "params": {"amount": 0}}],
        automation=[
            {
                "target": "tracks.sample.fx.p.params.amount",
                "curve": "lin",
                "points": [[1, 0.1], [3, 0.9]],
            }
        ],
    )
    audio = np.ones((88200, 2), np.float32)
    result = run_fx(project, song, audio)
    assert result[0, 0] == pytest.approx(0.1)
    assert result[-1, 0] > 0.899
    assert sum(reset for reset, _ in calls) == 1


def test_fader_pan_law():
    audio = np.ones((10, 2), np.float32)
    center = pan_gain(audio, 0, 0)
    assert np.sum(center[0] ** 2) == pytest.approx(1)
    assert np.allclose(pan_gain(audio, 0, -1), [1, 0])
    assert np.allclose(pan_gain(audio, -6, 1), [0, 10 ** (-6 / 20)])


def test_master_lufs_and_true_peak(project):
    import pyloudnorm as pyln

    sr = 22050
    t = np.arange(sr * 4) / sr
    audio = np.column_stack([0.1 * np.sin(2 * np.pi * 440 * t)] * 2).astype(np.float32)
    song = simple_song()
    song.master.target_lufs = -14
    result = master(audio, song, sr, Automation(song, sr), project, {}, {})
    assert pyln.Meter(sr).integrated_loudness(result) == pytest.approx(-14, abs=0.2)
    assert np.max(np.abs(resample_poly(result, 4, 1, axis=0))) <= 10 ** (-1 / 20) + 1e-5


def test_pattern_swing_velocities_lanes_and_window(project):
    song = Song.model_validate(
        {
            "meta": {"title": "drums", "bpm": 127.123, "key": "Am", "length_bars": 4},
            "tracks": [
                {
                    "id": "drums",
                    "at_bar": 2,
                    "until_bar": 4,
                    "pattern": {
                        "steps_per_bar": 4,
                        "swing": 1,
                        "lanes": {
                            "kick": {"sample": "library://kits/disco-01/kick.wav", "steps": "xo.3"}
                        },
                    },
                }
            ],
        }
    )
    sr = 22050
    _, lanes, events = arrange(project, song, sr, beat_frame(16, song.meta.bpm, sr))
    expected_beats = [4, 5 + 1 / 3, 7 + 1 / 3, 8, 9 + 1 / 3, 11 + 1 / 3]
    assert events["drums", "kick"] == [beat_frame(b, song.meta.bpm, sr) for b in expected_beats]
    lane = lanes["drums", "kick"]
    hits = events["drums", "kick"]
    peaks = [np.max(np.abs(lane[h : h + 500])) for h in hits]
    assert peaks[1] / peaks[0] == pytest.approx(0.6, abs=0.01)
    assert peaks[2] / peaks[0] == pytest.approx(1 / 3, abs=0.01)
    assert not np.any(lane[: hits[0]])
    assert not np.any(lane[beat_frame(12, song.meta.bpm, sr) :])


def test_midi_ignores_tempo_and_uses_ppq(project, monkeypatch):
    import mido

    midi = mido.MidiFile(ticks_per_beat=960)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend(
        [
            mido.MetaMessage("set_tempo", tempo=2_000_000),
            mido.Message("note_on", note=45, velocity=90, time=480),
            mido.Message("note_off", note=45, time=960),
        ]
    )
    midi.save(project.path("midi/test.mid"))
    song = Song.model_validate(
        {
            "meta": {"title": "notes", "bpm": 133, "key": "Am", "length_bars": 4},
            "tracks": [{"id": "bass", "instrument": {"plugin": "host"}, "midi": "midi/test.mid"}],
        }
    )
    notes = read_notes(project, song.tracks[0])
    assert notes[0].beat == 1.5 and notes[0].length_beats == 1
    seen = []

    def host(project, instrument, notes, sample_rate, n_frames, *, bpm):
        seen.append((notes, bpm))
        return np.zeros((n_frames, 2), np.float32)

    monkeypatch.setattr(plugins, "render_instrument", host)
    arrange(project, song, 22050, 10000)
    assert seen[0][1] == 133
    assert seen[0][0][0].beat == 1.5
