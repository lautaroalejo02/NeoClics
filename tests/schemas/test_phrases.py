"""WP12: looping note phrases on instrument tracks."""

import pytest
from pydantic import ValidationError

from muestra.schemas.song import Song
from muestra.versions.describe import describe_song
from muestra.versions.diff import song_diff

BASE = {
    "schema_version": 0,
    "meta": {"title": "t", "bpm": 120, "key": "G", "length_bars": 16},
    "phrases": [
        {
            "id": "bassline",
            "length_bars": 2,
            "notes": [
                {"beat": 1.5, "pitch": "G1", "length_beats": 0.4},
                {"beat": 3.5, "pitch": "G2", "length_beats": 0.4},
                {"beat": 7.5, "pitch": "D2", "length_beats": 0.4, "velocity": 90},
            ],
        }
    ],
    "tracks": [
        {
            "id": "bass",
            "at_bar": 5,
            "instrument": {"plugin": "finger-bass"},
            "phrases": [{"phrase": "bassline"}],
        }
    ],
}


def song(**changes):
    import copy

    data = copy.deepcopy(BASE)
    for k, v in changes.items():
        data[k] = v
    return Song.model_validate(data)


def test_phrase_loops_until_the_window_ends():
    s = song()
    assert s.check() == []
    notes = s.track_notes(s.tracks[0])
    # bars 5..16 = 12 bars = 6 passes of 3 notes
    assert len(notes) == 18
    assert notes[0].beat == 17.5 and notes[0].pitch == 31  # G1
    assert notes[3].beat == 17.5 + 8
    assert max(n.beat for n in notes) < 16 * 4 + 1


def test_repeat_transpose_and_extra_notes():
    tracks = [
        {
            "id": "bass",
            "at_bar": 5,
            "until_bar": 13,
            "instrument": {"plugin": "finger-bass"},
            "phrases": [
                {"phrase": "bassline", "repeat": 2},
                {"phrase": "bassline", "at_bar": 9, "repeat": 2, "transpose_semitones": 5},
            ],
            "notes": [{"beat": 48, "pitch": "G3", "length_beats": 1}],
        }
    ]
    s = song(tracks=tracks)
    assert s.check() == []
    notes = s.track_notes(s.tracks[0])
    assert len(notes) == 13
    # +5 st (C2, C3, G2) plus the inline G3 at beat 48
    assert {n.pitch for n in notes if n.beat >= 33} == {36, 48, 43, 55}
    assert any(n.beat == 48 for n in notes)


def test_validation_errors_are_actionable():
    bad = song(
        tracks=[
            {
                "id": "bass",
                "at_bar": 5,
                "instrument": {"plugin": "finger-bass"},
                "phrases": [
                    {"phrase": "nope"},
                    {"phrase": "bassline", "at_bar": 15, "repeat": 2},
                    {"phrase": "bassline", "at_bar": 2},
                    {"phrase": "bassline", "transpose_semitones": 24},
                ],
            }
        ],
        phrases=[
            {
                "id": "bassline",
                "length_bars": 2,
                "notes": [
                    {"beat": 9, "pitch": "G1", "length_beats": 0.5},
                    {"beat": 1, "pitch": 120, "length_beats": 0.5},
                ],
            }
        ],
    )
    codes = {(i.path, i.code) for i in bad.check()}
    assert ("tracks[0].phrases[0].phrase", "ref_not_found") in codes
    assert ("tracks[0].phrases[1].repeat", "clip_overrun") in codes
    assert ("tracks[0].phrases[2].at_bar", "clip_position") in codes
    assert ("tracks[0].phrases[3].transpose_semitones", "out_of_range") in codes
    assert ("phrases[0].notes[0].beat", "out_of_range") in codes
    with pytest.raises(ValidationError, match="midi"):
        Song.model_validate(
            {
                **BASE,
                "tracks": [
                    {
                        "id": "bass",
                        "instrument": {"plugin": "x"},
                        "midi": "midi/a.mid",
                        "phrases": [{"phrase": "bassline"}],
                    }
                ],
            }
        )


def test_diff_paths_and_spanish_descriptions():
    a = song()
    phrases = [dict(BASE["phrases"][0])]
    phrases[0]["notes"] = [dict(n) for n in phrases[0]["notes"]]
    phrases[0]["notes"][1]["pitch"] = "A2"
    tracks = [dict(BASE["tracks"][0], phrases=[{"phrase": "bassline", "transpose_semitones": 2}])]
    b = song(phrases=phrases, tracks=tracks)
    paths = {d.path for d in song_diff(a, b)}
    assert "phrases.bassline.notes[1].pitch" in paths
    assert "tracks.bass.phrases[0].transpose_semitones" in paths
    text = describe_song(a, b)
    assert any("frase bassline" in s and "Subí" in s for s in text), text
    assert any("dos semitonos arriba" in s or "2 semitonos arriba" in s for s in text), text


def test_rewriting_notes_as_a_phrase_says_it_sounds_the_same():
    s = song()
    expanded = [
        {"beat": n.beat, "pitch": n.pitch, "length_beats": n.length_beats, "velocity": n.velocity}
        for n in s.track_notes(s.tracks[0])
    ]
    flat = song(
        phrases=[],
        tracks=[
            {
                "id": "bass",
                "at_bar": 5,
                "instrument": {"plugin": "finger-bass"},
                "notes": expanded,
            }
        ],
    )
    text = describe_song(flat, s)
    assert len(text) == 2, text  # wrote the phrase + rewrote the track (no 18 note edits)
    assert "Escribí la frase bassline (2 compases, 3 notas)" in text
    assert "repite la frase bassline de 2 compases desde el compás 5" in text[1]
    assert text[1].endswith("(suena igual)")
    added = describe_song(song(tracks=[]), s)
    assert any("repite la frase bassline" in x for x in added), added


def test_renderer_and_ears_read_the_expanded_notes():
    from muestra.render.core import read_notes

    s = song()
    notes = read_notes(None, s.tracks[0], s)
    assert len(notes) == 18 and notes == s.track_notes(s.tracks[0])
