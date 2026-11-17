from __future__ import annotations

import hashlib
import os

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly

from muestra.errors import MuestraError
from muestra.project import load_analysis, save_analysis
from muestra.render.audio import decode
from muestra.render.core import arrange, beat_frame, render_audio, render_chop, render_song
from muestra.schemas.song import Song


@pytest.mark.parametrize("mode", ["resample", "stretch"])
@pytest.mark.parametrize("pitch", [0, 1, -7])
def test_chop_frames_edges_and_determinism(project, song, mode, pitch):
    chop = song.chops[0].model_copy(deep=True)
    chop.warp.mode, chop.warp.pitch_semitones = mode, pitch
    a = render_chop(project, chop, 22050, 127.3)
    for path in (project.cache_dir / "render/chops").glob("*.npy"):
        path.unlink()  # Compare two independent R3 computations, not a cache hit.
    b = render_chop(project, chop, 22050, 127.3)
    assert a.dtype == np.float32 and a.shape == (beat_frame(8, 127.3, 22050), 2)
    assert np.array_equal(a, b)
    assert np.max(np.abs(a)) > 0.01
    assert np.max(np.abs(a[[0, -1]])) < 1e-7
    local = np.max(np.abs(a[:500]))
    assert np.max(np.abs(np.diff(a[:110], axis=0))) < local * 0.25


@pytest.mark.parametrize("mode,frequency", [("stretch", 220), ("resample", 275)])
def test_pitch_frequency_and_preserved_length(project, song, mode, frequency):
    chop = song.chops[0].model_copy(deep=True)
    chop.length_beats = 1.5
    chop.warp.mode, chop.warp.pitch_semitones = mode, 12
    audio = render_chop(project, chop, 22050, 150)
    assert len(audio) == 13230
    signal = audio[:, 0] * np.hanning(len(audio))
    spectrum = np.abs(np.fft.rfft(signal))
    peak_hz = np.fft.rfftfreq(len(signal), 1 / 22050)[np.argmax(spectrum)]
    assert peak_hz == pytest.approx(frequency * 2, abs=3)


def test_cache_invalidates_audio_even_same_stat(project, song):
    chop = song.chops[0].model_copy(deep=True)
    chop.warp.pitch_semitones = 0
    path = project.path("cache/stems/src1/other.wav")
    stat = path.stat()
    a = render_chop(project, chop, 22050, 120)
    audio, sr = sf.read(path)
    sf.write(path, audio * 0.5, sr, subtype=sf.info(path).subtype)
    assert path.stat().st_size == stat.st_size
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    b = render_chop(project, chop, 22050, 120)
    assert np.max(np.abs(a - b)) > 0.01
    assert np.allclose(b, a * 0.5, atol=4e-5)
    assert len(list((project.cache_dir / "render/chops").glob("*.npy"))) == 2


def test_cache_invalidates_grid_and_params(project, song):
    chop = song.chops[0]
    a = render_chop(project, chop, 22050, 120)
    analysis = load_analysis(project, "src1")
    analysis.beats_s[2] += 0.04
    save_analysis(project, analysis)
    render_chop(project, chop, 22050, 120)
    shifted = chop.model_copy(deep=True)
    shifted.warp.pitch_semitones = -1
    b = render_chop(project, shifted, 22050, 120)
    assert not np.array_equal(a, b)
    assert len(list((project.cache_dir / "render/chops").glob("*.npy"))) == 3


def test_fractional_chop_and_bounds(project, song):
    chop = song.chops[0].model_copy(update={"start_offset_beats": -0.25, "length_beats": 1.5})
    a = render_chop(project, chop, 22050, 123)
    assert len(a) == beat_frame(1.5, 123, 22050)
    with pytest.raises(MuestraError) as exc:
        render_chop(project, chop.model_copy(update={"length_beats": 128}), 22050, 120)
    assert exc.value.issues[0].path == "chops.c1"
    assert exc.value.issues[0].hint


def test_variable_beat_stretch_maps_each_beat(project, song):
    from muestra.render.audio import vinyl

    source, sr = sf.read(project.path("sources/fake-source.wav"), dtype="float32", always_2d=True)
    source = np.repeat(source, 2, axis=1)
    analysis = load_analysis(project, "src1")
    original = analysis.beats_s.copy()
    intervals = [0.7, 0.3, 0.65, 0.35, 0.6, 0.4, 0.55, 0.45, 0.5, 0.5]
    grid = np.r_[0.25, 0.25 + np.cumsum(intervals)]
    variable = np.zeros_like(source)
    for i in range(10):
        a, b = round(original[i] * sr), round(original[i + 1] * sr)
        c, d = round(grid[i] * sr), round(grid[i + 1] * sr)
        variable[c:d] = vinyl(source[a:b], d - c)
    sf.write(project.path("sources/fake-source.wav"), variable, sr, subtype="FLOAT")
    analysis.beats_s = grid.tolist()
    analysis.downbeats_s = grid[[0, 4, 8]].tolist()
    analysis.tempo_variable = True
    save_analysis(project, analysis)
    chop = song.chops[0].model_copy(deep=True)
    chop.stem = "mix"
    chop.warp.mode, chop.warp.pitch_semitones = "stretch", 0
    audio = render_chop(project, chop, sr, 120)
    # Native R3 preserves transient timing approximately; buffer placements
    # themselves are exact. Each fixture noise burst should remain near its
    # target beat despite strongly alternating source beat lengths.
    onsets = []
    for i in range(1, 8):
        expected = beat_frame(i, 120, sr)
        radius = round(0.04 * sr)
        piece = audio[expected - radius : expected + radius, 0]
        actual = expected - radius + int(np.argmax(np.abs(np.diff(piece))))
        onsets.append((actual - expected) / sr)
    print("variable_warp_onset_offsets_s", onsets)
    assert max(abs(x) for x in onsets) < 0.02


@pytest.mark.parametrize("mode", ["resample", "stretch"])
def test_indexed_sample_warp(project, mode):
    from muestra.render.core import clip_audio
    from muestra.schemas.song import Clip

    index = project.library_dirs()[0] / ".muestra-index.json"
    index.write_text(
        '{"entries": [{"path": "kits/disco-01/kick.wav", "bpm": 100}]}', encoding="utf-8"
    )
    clip = Clip.model_validate(
        {
            "sample": "library://kits/disco-01/kick.wav",
            "length_beats": 1,
            "at_bar": 1,
            "warp": {"mode": mode, "pitch_semitones": 2},
        }
    )
    song = Song.model_validate(
        {"meta": {"title": "warp", "bpm": 150, "key": "Am", "length_bars": 1}, "tracks": []}
    )
    audio, length = clip_audio(project, song, clip, 22050)
    assert length == 1 and len(audio) == 8820
    assert np.max(np.abs(audio)) > 0.01
    assert np.max(np.abs(audio[[0, -1]])) < 1e-7


def test_repeat_positions_do_not_accumulate_rounding(project, song):
    song = song.model_copy(deep=True)
    song.meta.bpm = 127.123
    song.chops[0].length_beats = 0.333
    song.chops[0].warp.pitch_semitones = 0
    sample = song.tracks[0]
    sample.fx = []
    sample.clips[0].repeat = 180
    song.tracks = [sample]
    song.automation = []
    sr = 22050
    raw, _, _ = arrange(project, song, sr, beat_frame(64, song.meta.bpm, sr))
    piece = render_chop(project, song.chops[0], sr, song.meta.bpm)
    for r in range(180):
        start = beat_frame(r * 0.333, song.meta.bpm, sr)
        assert np.array_equal(raw["sample"][start : start + 20], piece[:20])
        if r:
            local = raw["sample"][start - 100 : start + 100]
            jump = np.max(np.abs(np.diff(local, axis=0)))
            assert jump < np.max(np.abs(local)) * 0.3


def test_full_export_stems_range_and_determinism(project, song, fake_instrument):
    # Keep actual song fixture (16 bars, pitch +1, MIDI, automation, all inserts).
    full, tracks, lanes, premaster = render_audio(project, song, (1, 17))
    summed = sum(tracks.values())
    assert np.allclose(summed, premaster, atol=2e-7)
    assert {"sample", "drums", "bass"} == set(tracks)
    assert ("drums", "kick") in lanes
    sub, subtracks, _, _ = render_audio(project, song, (3, 5))
    a, b = beat_frame(8, 120, 22050), beat_frame(16, 120, 22050)
    assert len(sub) == b - a
    assert np.array_equal(sub[:-110], full[a : b - 110])
    for key in tracks:
        assert np.array_equal(subtracks[key][:-110], tracks[key][a : b - 110])
    first = render_song(project, song, "v100", (1, 17))
    second = render_song(project, song, "v101", (1, 17))
    assert (first / "mix.wav").read_bytes() == (second / "mix.wav").read_bytes()
    assert sf.info(first / "mix.wav").subtype == "PCM_24"
    assert sf.info(first / "stems/sample.wav").subtype == "FLOAT"
    assert sf.info(first / "mix.wav").frames == beat_frame(64, 120, 22050)
    for path in (first / "stems").glob("*.wav"):
        assert sf.info(path).frames == len(full)
    # Golden tolerance: fixture + fake host, measured over first two bars.
    print("fixture_pcm_sha256", hashlib.sha256((first / "mix.wav").read_bytes()).hexdigest())
    # Tolerance permits platform-specific floating-point math in R3 while
    # catching mix/gain/routing regressions against the fixture arrangement.
    assert float(np.sqrt(np.mean(full**2))) == pytest.approx(0.2474482954, abs=2e-4)
    assert float(np.max(np.abs(full))) == pytest.approx(0.8844353557, abs=2e-4)
    exported_sum = sum(sf.read(first / f"stems/{key}.wav", dtype="float32")[0] for key in tracks)
    assert np.allclose(exported_sum, premaster, atol=2e-7)
    assert np.isfinite(full).all()
    assert np.max(np.abs(resample_poly(full, 4, 1, axis=0))) <= 10 ** (-1 / 20) + 1e-5


def test_muted_stems_and_stems_false_cleanup(project, song, fake_instrument):
    song = song.model_copy(deep=True)
    song.tracks[1].mute = True
    mix, tracks, lanes, _ = render_audio(project, song, (1, 2))
    assert np.max(np.abs(tracks["drums"])) == 0
    assert all(np.max(np.abs(a)) == 0 for a in lanes.values())
    assert np.max(np.abs(mix)) > 0
    directory = render_song(project, song, "v100", (1, 2))
    render_song(project, song, "v100", (1, 2), stems=False)
    assert not list((directory / "stems").glob("*.wav"))


def test_sample_native_speed_overlap_and_windows(project):
    song = Song.model_validate(
        {
            "meta": {"title": "samples", "bpm": 120, "key": "Am", "length_bars": 3},
            "tracks": [
                {
                    "id": "hit",
                    "at_bar": 2,
                    "until_bar": 3,
                    "clips": [
                        {"sample": "sources/fake-source.wav", "length_beats": 8, "at_bar": 1},
                        {"sample": "sources/fake-source.wav", "length_beats": 4, "at_bar": 2},
                    ],
                }
            ],
        }
    )
    raw, _, _ = arrange(project, song, 22050, 132300)
    assert np.count_nonzero(raw["hit"][:44100]) == 0
    assert np.count_nonzero(raw["hit"][88200:]) == 0
    native, _ = decode(project, project.path("sources/fake-source.wav"), 22050)
    assert np.allclose(raw["hit"][45000:45100], native[45000:45100] + native[900:1000])


def test_no_instrument_fallback(project, song):
    with pytest.raises(MuestraError) as exc:
        render_song(project, song, "v100", (1, 2))
    issue = exc.value.issues[0]
    # With the real plugin host integrated, a missing plugin is reported by
    # muestra.plugins itself; the renderer must surface it, never fall back
    # to a synthesized toy sound.
    assert issue.code in {"instrument_unavailable", "plugin_missing"}
    assert issue.hint


@pytest.mark.parametrize("bars", [(0, 2), (2, 2), (2, 18), (3, 1)])
def test_invalid_range(project, song, bars):
    with pytest.raises(MuestraError) as exc:
        render_song(project, song, "v100", bars)
    assert exc.value.issues[0].path == "range_bars"
