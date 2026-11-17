"""WP16 per-event control through the real renderer: per-bar lane overrides,
free hits, per-pass clip overrides, trims, stretch, reverse, gain and fades.

Audio assertions measure the lane/track stems the renderer produces (pre-fx
lanes for drums, the raw arrangement for clips) at the exact frames involved.
"""

from __future__ import annotations

import numpy as np
import pytest

from muestra.render.core import arrange, beat_frame, render_audio
from muestra.schemas.song import Song, clip_passes, pattern_hits

SR = 48000


def _song(song: Song, **changes) -> Song:
    data = song.model_dump(mode="json")
    for track in data["tracks"]:
        if track["id"] in changes:
            changes[track["id"]](track)
    # bass is a MIDI instrument: drop it, these tests are about drums/clips
    data["tracks"] = [t for t in data["tracks"] if t["id"] != "bass"]
    out = Song.model_validate(data)
    assert out.check() == []
    return out


def _peak(audio: np.ndarray, beat0: float, bpm: float, ms: float = 40) -> float:
    a = beat_frame(beat0, bpm, SR)
    return float(np.max(np.abs(audio[a : a + round(SR * ms / 1000)])))


def _lanes(project, song):
    n = beat_frame(song.meta.length_bars * 4, song.meta.bpm, SR)
    raw, lanes, events = arrange(project, song, SR, n)
    return raw, lanes, events


def test_override_removes_one_kick_only_in_that_bar(project, song):
    def edit(t):
        t["pattern"]["lanes"]["kick"]["overrides"] = {7: "x...x.......x..."}

    edited = _song(song, drums=edit)
    _, lanes, events = _lanes(project, edited)
    kick = lanes["drums", "kick"]
    bpm = song.meta.bpm
    bar7_beat3 = 6 * 4 + 2  # 0-based song beat of bar 7, beat 3
    assert _peak(kick, bar7_beat3, bpm) < 1e-6  # removed exactly there
    assert _peak(kick, 5 * 4 + 2, bpm) > 0.05  # bar 6 beat 3 still plays
    assert _peak(kick, 7 * 4 + 2, bpm) > 0.05  # bar 8 beat 3 still plays
    assert _peak(kick, 6 * 4 + 1, bpm) > 0.05  # bar 7 beat 2 still plays
    # sidechain triggers follow the edit too
    assert beat_frame(bar7_beat3, bpm, SR) not in events["drums", "kick"]
    hits = [h for h in pattern_hits(edited, edited.tracks[1]) if h.lane == "kick"]
    assert len(hits) == 16 * 4 - 1
    assert {h.origin for h in hits if h.bar == 7} == {"override"}


def test_override_can_add_and_change_velocity(project, song):
    def edit(t):
        t["pattern"]["lanes"]["clap"]["overrides"] = {3: "..9.4.......x..."}

    edited = _song(song, drums=edit)
    _, lanes, _ = _lanes(project, edited)
    clap = lanes["drums", "clap"]
    bpm = song.meta.bpm
    bar3 = 2 * 4
    full = _peak(clap, bar3 + 3, bpm)
    assert _peak(clap, bar3 + 0.5, bpm) == pytest.approx(full, rel=1e-4)  # 9 = 1.0
    assert _peak(clap, bar3 + 1, bpm) == pytest.approx(full * 4 / 9, rel=1e-3)


def test_free_hits_play_off_grid_with_their_own_sample(project, song):
    def edit(t):
        t["pattern"]["lanes"]["perc"] = {"sample": "library://kits/disco-01/ohat.wav"}
        t["pattern"]["hits"] = [
            {"beat": 9.37, "lane": "perc", "velocity": 0.5},
            {"beat": 13.1, "lane": "kick", "sample": "library://kits/disco-01/clap.wav"},
        ]

    edited = _song(song, drums=edit)
    _, lanes, events = _lanes(project, edited)
    bpm = song.meta.bpm
    perc = lanes["drums", "perc"]
    onset = beat_frame(8.37, bpm, SR)
    assert np.max(np.abs(perc[: onset - 1])) == 0
    assert np.max(np.abs(perc[onset : onset + 2000])) > 0.02
    assert onset in events["drums", "perc"]
    # the kick-lane free hit uses the clap sample (lane gain/pan)
    k = beat_frame(12.1, bpm, SR)
    assert k in events["drums", "kick"]


def test_muted_pass_is_silent_only_there(project, song):
    def edit(t):
        t["clips"][0]["instances"] = {3: {"mute": True}}

    base = _song(song)
    edited = _song(song, sample=edit)
    raw_a, _, _ = _lanes(project, base)
    raw_b, _, _ = _lanes(project, edited)
    a, b = raw_a["sample"], raw_b["sample"]
    bpm = song.meta.bpm
    lo, hi = beat_frame(16, bpm, SR), beat_frame(24, bpm, SR)
    edge = round(0.006 * SR)  # neighbours fade to silence at the muted pass
    assert np.max(np.abs(b[lo + edge : hi - edge])) == 0
    assert np.max(np.abs(a[lo:hi])) > 0.05
    # everything outside the muted pass (and its two boundary fades) is unchanged
    assert np.array_equal(a[: lo - edge], b[: lo - edge])
    assert np.array_equal(a[hi + edge :], b[hi + edge :])


def test_pass_gain_reverse_and_pitch(project, song):
    def edit(t):
        t["clips"][0]["instances"] = {
            2: {"gain_db": -6},
            4: {"reverse": True},
            6: {"pitch_semitones": 12},
        }

    base = _song(song)
    edited = _song(song, sample=edit)
    a = _lanes(project, base)[0]["sample"]
    b = _lanes(project, edited)[0]["sample"]
    bpm = song.meta.bpm
    f = lambda beat: beat_frame(beat, bpm, SR)  # noqa: E731
    mid = slice(f(8) + 2000, f(16) - 2000)
    expected = np.max(np.abs(a[mid])) * 10 ** (-6 / 20)
    assert np.max(np.abs(b[mid])) == pytest.approx(expected, rel=1e-3)
    rev = slice(f(24) + 2000, f(32) - 2000)
    lo, hi = f(24), f(32)
    assert np.allclose(b[rev], a[lo:hi][::-1][2000:-2000], atol=1e-6)
    # +12 st: the dominant frequency doubles over pass 6
    seg_a = a[f(40) + 4000 : f(48) - 4000, 0]
    seg_b = b[f(40) + 4000 : f(48) - 4000, 0]
    freqs = np.fft.rfftfreq(len(seg_a), 1 / SR)
    pa = freqs[np.argmax(np.abs(np.fft.rfft(seg_a * np.hanning(len(seg_a)))))]
    pb = freqs[np.argmax(np.abs(np.fft.rfft(seg_b * np.hanning(len(seg_b)))))]
    assert pb == pytest.approx(2 * pa, rel=0.03)


def test_trim_plays_a_sub_range_of_the_chop(project, song):
    def edit(t):
        t["clips"] = [
            {"chop": "c1", "at_bar": 1, "repeat": 4, "trim_start_beats": 2, "length_beats": 4}
        ]

    base = _song(song)
    edited = _song(song, sample=edit)
    a = _lanes(project, base)[0]["sample"]
    b = _lanes(project, edited)[0]["sample"]
    bpm = song.meta.bpm
    f = lambda beat: beat_frame(beat, bpm, SR)  # noqa: E731
    # pass 1 of the trimmed clip (beats 0-4) == chop beats 2-6 (beats 2-6 of the base)
    assert np.allclose(b[f(0) + 600 : f(4) - 600], a[f(2) + 600 : f(6) - 600], atol=1e-6)
    # it ends after 4 passes of 4 beats: silence after beat 16
    assert np.max(np.abs(b[f(16) + 600 :])) == 0
    passes = clip_passes(edited, edited.tracks[0])
    assert [p.start0 for p in passes] == [0, 4, 8, 12]


def test_trim_past_chop_end_uses_the_record_and_stretch_keeps_length(project, song):
    def edit(t):
        t["clips"] = [
            {"chop": "c1", "at_bar": 1, "length_beats": 10},  # 2 beats of source past the chop
            {"chop": "c1", "at_bar": 5, "length_beats": 8, "stretch": 2},  # 4 beats over 8
        ]

    edited = _song(song, sample=edit)
    b = _lanes(project, edited)[0]["sample"]
    bpm = song.meta.bpm
    f = lambda beat: beat_frame(beat, bpm, SR)  # noqa: E731
    assert np.max(np.abs(b[f(8.5) : f(9.5)])) > 0.01  # the record continues
    assert np.max(np.abs(b[f(16) + 2000 : f(24) - 2000])) > 0.01
    assert np.max(np.abs(b[f(24) + 600 :])) == 0


def test_clip_fades_and_gain(project, song):
    def edit(t):
        t["clips"][0].update({"gain_db": -12, "fade_in_ms": 200, "fade_out_ms": 200, "repeat": 2})

    base = _song(song)
    edited = _song(song, sample=edit)
    a = _lanes(project, base)[0]["sample"]
    b = _lanes(project, edited)[0]["sample"]
    bpm = song.meta.bpm
    f = lambda beat: beat_frame(beat, bpm, SR)  # noqa: E731
    g = 10 ** (-12 / 20)
    mid = slice(f(2), f(6))
    assert np.allclose(b[mid], a[mid] * g, atol=1e-6)
    # the 200 ms fade-in is still rising 100 ms in; the seam between the passes is faded
    early = np.max(np.abs(b[round(0.09 * SR) : round(0.1 * SR)]))
    assert early < 0.8 * g * np.max(np.abs(a[round(0.09 * SR) : round(0.1 * SR)])) + 1e-9
    seam = f(8)
    assert np.max(np.abs(b[seam - 30 : seam + 30])) < 0.02


def test_defaults_render_bit_identically_through_the_new_paths(project, song):
    """No override = the WP0-WP15 audio (render_signature/prune rely on it)."""
    base = _song(song)
    data = base.model_dump(mode="json")
    data["tracks"][0]["clips"][0]["instances"] = {}
    data["tracks"][1]["pattern"]["hits"] = []
    same = Song.model_validate(data)
    a = render_audio(project, base, (1, 17))[0]
    b = render_audio(project, same, (1, 17))[0]
    assert np.array_equal(a, b)
