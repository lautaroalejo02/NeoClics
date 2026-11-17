import numpy as np
import pytest
import soundfile as sf

from muestra.errors import MuestraError
from muestra.plugins.core import render_instrument
from muestra.plugins.sfz import parse_sfz, render_sfz
from muestra.schemas.song import Instrument, MidiNote


def sample_map(tmp_path, body="", stereo=False):
    sample_rate = 24000
    time = np.arange(sample_rate * 2) / sample_rate
    wave = (0.2 * np.sin(2 * np.pi * 440 * time)).astype(np.float32)
    if stereo:
        wave = np.column_stack([wave, wave * 0.5])
    sf.write(tmp_path / "tone with spaces.wav", wave, sample_rate, subtype="FLOAT")
    path = tmp_path / "instrument.sfz"
    path.write_text(
        "<global> ampeg_release=0.1\n<group> pitch_keycenter=A4\n"
        '<region> sample="tone with spaces.wav" ' + body,
        encoding="utf-8",
    )
    return path


def fft_pitch(audio, rate, low, high):
    wave = audio.mean(axis=1)
    spectrum = np.abs(np.fft.rfft(wave * np.hanning(len(wave)), n=262144))
    frequency = np.fft.rfftfreq(262144, 1 / rate)
    spectrum[(frequency < low) | (frequency > high)] = 0
    return frequency[np.argmax(spectrum)]


@pytest.mark.parametrize("pitch,expected", [(45, 110), (69, 440), (72, 523.251)])
def test_timing_pitch_frames_release_and_determinism(project, pitch, expected):
    path = sample_map(project.root, stereo=True)
    notes = [MidiNote(beat=2, pitch=pitch, length_beats=0.5)]
    instrument = Instrument(plugin="sfz", preset=str(path))
    a = render_instrument(project, instrument, notes, 48000, 96000, bpm=90)
    b = render_instrument(project, instrument, notes, 48000, 96000, bpm=90)
    onset = round(48000 * 60 / 90)
    assert a.shape == (96000, 2) and a.dtype == np.float32
    assert np.array_equal(a, b)
    assert np.all(a[:onset] == 0)
    assert np.flatnonzero(np.abs(a[:, 0]) > 1e-5)[0] - onset < 20
    assert np.max(np.abs(a[onset + 1000 : onset + 10000])) > 0.01
    assert (
        abs(
            fft_pitch(a[onset + 500 : onset + 15000], 48000, expected * 0.95, expected * 1.05)
            - expected
        )
        < 0.3
    )
    release_end = onset + round(48000 * (60 / 90 * 0.5 + 0.1))
    assert np.all(a[release_end:] == 0)
    assert np.allclose(a[:, 1], a[:, 0] * 0.5)


def test_velocity_zones_and_global_group_inheritance(tmp_path):
    path = sample_map(tmp_path)
    path.write_text(
        "<global> ampeg_release=.02\n<group> key=69\n"
        "<region> sample=tone with spaces.wav lovel=1 hivel=63 volume=-20\n"
        "<region> sample=tone with spaces.wav lovel=64 hivel=127 volume=0\n"
    )

    def render(velocity):
        return render_sfz(
            path,
            [MidiNote(beat=1, pitch=69, length_beats=1, velocity=velocity)],
            24000,
            24000,
            bpm=120,
        )

    assert np.max(np.abs(render(100))) > np.max(np.abs(render(50))) * 20
    assert all(r["lokey"] == 69 and r["ampeg_release"] == ".02" for r in parse_sfz(path))


def test_explicit_snapshot_seed_is_independent_of_live_song(project):
    from muestra.project import load_song, save_song

    path = sample_map(project.root)
    path.write_text(
        "<group> key=69\n<region> sample=tone with spaces.wav lorand=0 hirand=.5\n"
        "<region> sample=tone with spaces.wav lorand=.5 hirand=1 volume=-20\n"
    )
    instrument = Instrument(plugin="sfz", preset=str(path))
    notes = [MidiNote(beat=i + 1, pitch=69, length_beats=0.5) for i in range(8)]
    before = render_instrument(project, instrument, notes, 24000, 96000, bpm=120, seed=42)
    live = load_song(project)
    live.meta.seed = 73
    save_song(project, live)
    after = render_instrument(project, instrument, notes, 24000, 96000, bpm=120, seed=42)
    live_seed = render_instrument(project, instrument, notes, 24000, 96000, bpm=120)
    assert np.array_equal(before, after)
    assert not np.array_equal(before, live_seed)


@pytest.mark.parametrize("mode", ["loop_continuous", "loop_sustain"])
def test_loop_points_and_release(tmp_path, mode):
    path = sample_map(tmp_path, f"loop_mode={mode} loop_start=12000 loop_end=14399")
    audio = render_sfz(path, [MidiNote(beat=1, pitch=69, length_beats=6)], 24000, 96000, bpm=120)
    assert np.sqrt(np.mean(audio[60000:66000] ** 2)) > 0.01
    assert np.all(audio[74400:] == 0)
    assert np.max(np.abs(np.diff(audio[30000:70000, 0]))) < 0.04


def test_adsr_and_early_noteoff(tmp_path):
    path = sample_map(tmp_path, "ampeg_attack=.2 ampeg_hold=.1 ampeg_decay=.2 ampeg_sustain=25")
    from muestra.plugins.sfz import envelope

    env = envelope(parse_sfz(path)[0], 24000, 12000, 24000)
    assert env[0] == 0 and env[4800] == pytest.approx(1)
    assert env[9600] == pytest.approx(0.625)
    assert env[12000] == pytest.approx(0.25, abs=0.001)
    assert env[14400] == 0
    early = envelope(parse_sfz(path)[0], 12000, 2400, 24000)
    assert early[2400] == pytest.approx(0.5, abs=0.001)
    assert np.all(early[4800:] == 0)


def test_round_robin_seed_and_one_shot(tmp_path):
    path = sample_map(tmp_path)
    text = path.read_text().replace("<region>", "<group> seq_length=2 loop_mode=one_shot\n<region>")
    text += " seq_position=1\n<region> sample=tone with spaces.wav seq_position=2 volume=-20\n"
    path.write_text(text)
    notes = [MidiNote(beat=i + 1, pitch=69, length_beats=0.1) for i in range(4)]
    a = render_sfz(path, notes, 24000, 60000, bpm=30, seed=42)
    b = render_sfz(path, notes, 24000, 60000, bpm=30, seed=42)
    assert np.array_equal(a, b)
    assert np.max(np.abs(a[12000:24000])) > 0.001  # one_shot ignores short gate
    assert np.max(np.abs(a[:24000])) != np.max(np.abs(a[48000:60000]))


@pytest.mark.parametrize(
    "body",
    [
        "cutoff=100",
        "loop_mode=loop_continuous",
        "ampeg_attack=-1",
        "seq_position=2 seq_length=1",
        "loop_start=20 loop_end=10 loop_mode=loop_sustain",
    ],
)
def test_invalid_sfz_fails_with_hint(tmp_path, body):
    path = sample_map(tmp_path, body)
    with pytest.raises(MuestraError) as exc:
        render_sfz(path, [MidiNote(beat=1, pitch=69, length_beats=1)], 24000, 24000, bpm=120)
    assert exc.value.hint


def test_empty_notes_and_explicit_tempo(project):
    empty = render_instrument(project, Instrument(plugin="missing"), [], 48000, 123, bpm=120)
    assert empty.shape == (123, 2) and not np.any(empty)
    with pytest.raises(MuestraError):
        render_instrument(project, Instrument(plugin="missing"), [], 48000, 123, bpm=0)


def test_high_quality_resampling_rejects_aliasing(tmp_path):
    path = sample_map(tmp_path)
    # Shift an 8 kHz recorded sine up an octave at 24 kHz output: it must
    # be filtered away, not fold back to an audible 8 kHz alias.
    t = np.arange(48000) / 24000
    sf.write(
        tmp_path / "tone with spaces.wav",
        0.2 * np.sin(2 * np.pi * 8000 * t),
        24000,
        subtype="FLOAT",
    )
    audio = render_sfz(path, [MidiNote(beat=1, pitch=81, length_beats=2)], 24000, 24000, bpm=120)
    assert np.sqrt(np.mean(audio[2400:18000] ** 2)) < 1e-6
