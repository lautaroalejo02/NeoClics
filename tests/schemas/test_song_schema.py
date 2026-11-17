"""Schema-level tests: the valid fixture parses clean, and each validation
rule produces issues with a field path, the offending value and a hint."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from muestra.errors import issues_from_validation_error
from muestra.project import dump_song, load_song, validate_song
from muestra.schemas.song import (
    Clip,
    Song,
    clip_span_beats,
    parse_automation_target,
    parse_steps,
)


def test_example_song_parses_and_validates(example_project):
    song = load_song(example_project)
    assert song.meta.bpm == 120
    assert song.chops[0].id == "c1"
    # The fixture names a Surge XT path that is machine-specific (WP13 plugin check).
    assert validate_song(example_project, check_files=True, check_plugins=False) == []


def test_round_trip(example_project):
    song = load_song(example_project)
    reparsed = Song.model_validate(yaml.safe_load(dump_song(song)))
    assert reparsed == song


def test_unknown_fields_are_errors():
    with pytest.raises(ValidationError) as exc:
        Song.model_validate(
            {"meta": {"title": "x", "bpm": 1, "key": "Am", "length_bars": 8, "bogus": 1}}
        )
    issues = issues_from_validation_error(exc.value)
    assert issues[0].code == "unknown_field"
    assert issues[0].path == "meta.bogus"
    assert issues[0].hint


def test_semantic_checks_collect_all_errors():
    song = Song.model_validate(
        {
            "meta": {"title": "x", "bpm": 120, "key": "Am", "length_bars": 16},
            "sources": [{"id": "src1", "file": "sources/a.wav"}],
            "chops": [
                {
                    "id": "c1",
                    "source": "nope",
                    "stem": "mix",
                    "start_downbeat": 1,
                    "length_beats": 8,
                },
            ],
            "tracks": [
                {"id": "a", "clips": [{"chop": "c9", "at_bar": 1}]},
                {"id": "a", "clips": [{"chop": "c1", "at_bar": 17}]},
            ],
        }
    )
    issues = song.check()
    paths = {i.path for i in issues}
    codes = {i.code for i in issues}
    # all three error classes collected, not just the first
    assert "duplicate_id" in codes and "ref_not_found" in codes
    assert "tracks[1].id" in paths
    assert "chops[0].source" in paths
    assert "tracks[0].clips[0].chop" in paths
    assert "tracks[1].clips[0].at_bar" in paths
    assert all(i.hint for i in issues)


def test_clip_span_and_overrun():
    clip = Clip.model_validate({"chop": "c1", "at_bar": 15, "repeat": 2})
    assert clip_span_beats(clip, 8.0) == (56.0, 72.0)
    song = Song.model_validate(
        {
            "meta": {"title": "x", "bpm": 120, "key": "Am", "length_bars": 16},
            "chops": [
                {"id": "c1", "source": "s", "stem": "mix", "start_downbeat": 1, "length_beats": 8}
            ],
            "sources": [{"id": "s", "file": "sources/a.wav"}],
            "tracks": [{"id": "t", "clips": [clip.model_dump(mode="json")]}],
        }
    )
    (issue,) = [i for i in song.check() if i.code == "clip_overrun"]
    assert issue.path == "tracks[0].clips[0].repeat"


def test_clip_requires_exactly_one_source():
    with pytest.raises(ValidationError):
        Clip.model_validate({"at_bar": 1})
    with pytest.raises(ValidationError):
        Clip.model_validate({"chop": "c1", "sample": "library://a/b.wav", "at_bar": 1})
    with pytest.raises(ValidationError):
        Clip.model_validate({"sample": "library://a/b.wav", "at_bar": 1})


def test_automation_target_grammar():
    assert parse_automation_target("tracks.sample.fx.lp.cutoff_hz") == (
        "tracks",
        "sample",
        "lp",
        "cutoff_hz",
    )
    assert parse_automation_target("tracks.drums.gain_db") == ("tracks", "drums", None, "gain_db")
    assert parse_automation_target("master.fx.lim.ceiling_db") == (
        "master",
        None,
        "lim",
        "ceiling_db",
    )
    assert parse_automation_target("master.gain_db") == ("master", None, None, "gain_db")
    assert parse_automation_target("tracks.no-hyphens_ok.pan") is not None
    for bad in (
        "tracks.x.bogus",
        "master.fx",
        "foo.bar",
        "tracks.x.fx.a",
        "tracks.x.fx.a.b.c",
        "master.gain_db.extra",
    ):
        assert parse_automation_target(bad) is None, bad


def test_parse_steps():
    assert parse_steps("x...o...9...-...") == [
        1.0,
        None,
        None,
        None,
        0.6,
        None,
        None,
        None,
        1.0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    assert parse_steps("x x") == [1.0, 1.0]


def test_key_normalisation():
    # canonical form: 'F#m' minor, 'F#' major; uppercase 'M' = MAJOR
    assert (
        Song.model_validate(
            {"meta": {"title": "x", "bpm": 100, "key": "f#M", "length_bars": 4}}
        ).meta.key
        == "F#"
    )
    assert (
        Song.model_validate(
            {"meta": {"title": "x", "bpm": 100, "key": "amin", "length_bars": 4}}
        ).meta.key
        == "Am"
    )
    assert (
        Song.model_validate(
            {"meta": {"title": "x", "bpm": 100, "key": "a", "length_bars": 4}}
        ).meta.key
        == "Am"
    )
    assert (
        Song.model_validate(
            {"meta": {"title": "x", "bpm": 100, "key": "A minor", "length_bars": 4}}
        ).meta.key
        == "Am"
    )
    assert (
        Song.model_validate(
            {"meta": {"title": "x", "bpm": 100, "key": "Bbmaj", "length_bars": 4}}
        ).meta.key
        == "Bb"
    )


def test_parse_key_shared_helper():
    from muestra.music import parse_key

    assert parse_key("F#m") == (6, "minor")
    assert parse_key("Bbm") == (10, "minor")
    assert parse_key("A#m") == (10, "minor")  # enharmonic
    assert parse_key("CM") == (0, "major")
    assert parse_key("C") == (0, "major")


def test_parse_note_name():
    from muestra.music import note_name, parse_note_name

    assert parse_note_name("F#2") == 42
    assert parse_note_name("C4") == 60
    assert note_name(42) == "F#2"


def test_time_signature_v0_is_44():
    with pytest.raises(ValidationError):
        Song.model_validate(
            {
                "meta": {
                    "title": "x",
                    "bpm": 100,
                    "key": "Am",
                    "length_bars": 4,
                    "time_signature": "3/4",
                }
            }
        )


def _song_with(**over):
    base = {
        "meta": {"title": "x", "bpm": 120, "key": "Am", "length_bars": 16},
        "sources": [],
        "chops": [],
        "tracks": [],
        "automation": [],
    }
    base.update(over)
    return base


def test_track_at_until_bar():
    song = Song.model_validate(
        _song_with(
            tracks=[
                {
                    "id": "drums",
                    "at_bar": 5,
                    "until_bar": 9,
                    "pattern": {
                        "lanes": {
                            "kick": {"sample": "library://a/kick.wav", "steps": "x...x...x...x..."}
                        },
                    },
                }
            ]
        )
    )
    t = song.tracks[0]
    assert t.at_bar == 5 and t.until_bar == 9
    assert song.check() == []
    # until_bar must be after at_bar
    with pytest.raises(ValidationError):
        Song.model_validate(
            _song_with(
                tracks=[
                    {
                        "id": "drums",
                        "at_bar": 9,
                        "until_bar": 5,
                        "pattern": {"lanes": {"k": {"sample": "library://a/k.wav", "steps": "x"}}},
                    }
                ]
            )
        )
    # until_bar past the song end is an issue
    song = Song.model_validate(
        _song_with(
            meta={"title": "x", "bpm": 120, "key": "Am", "length_bars": 8},
            tracks=[
                {
                    "id": "drums",
                    "at_bar": 1,
                    "until_bar": 20,
                    "pattern": {"lanes": {"k": {"sample": "library://a/k.wav", "steps": "x"}}},
                }
            ],
        )
    )
    assert any(i.code == "out_of_range" and i.path.endswith("until_bar") for i in song.check())
    # clips tracks cannot use at_bar/until_bar
    song = Song.model_validate(
        _song_with(
            chops=[{"id": "c1", "source": "s1", "start_downbeat": 1, "length_beats": 4}],
            sources=[{"id": "s1", "file": "sources/a.wav"}],
            tracks=[
                {"id": "t", "at_bar": 5, "clips": [{"chop": "c1", "at_bar": 1}]},
            ],
        )
    )
    assert any(i.code == "invalid_value" and "at_bar" in i.path for i in song.check())


def test_sidechain_key_lane_and_cycles():
    # key_lane must name a real lane of the key track's pattern
    song = Song.model_validate(
        _song_with(
            tracks=[
                {
                    "id": "drums",
                    "pattern": {
                        "lanes": {
                            "kick": {"sample": "library://a/k.wav", "steps": "x...x...x...x..."}
                        }
                    },
                },
                {
                    "id": "bass",
                    "instrument": {"plugin": "p"},
                    "notes": [{"beat": 1, "pitch": "C2", "length_beats": 4}],
                    "fx": [
                        {
                            "type": "sidechain",
                            "key_track": "drums",
                            "key_lane": "hihat",
                            "depth": 0.5,
                        }
                    ],
                },
            ]
        )
    )
    assert any(i.code == "ref_not_found" and "key_lane" in i.path for i in song.check())
    # valid key_lane passes
    song2 = Song.model_validate(
        _song_with(
            tracks=[
                {
                    "id": "drums",
                    "pattern": {
                        "lanes": {
                            "kick": {"sample": "library://a/k.wav", "steps": "x...x...x...x..."}
                        }
                    },
                },
                {
                    "id": "bass",
                    "instrument": {"plugin": "p"},
                    "notes": [{"beat": 1, "pitch": "C2", "length_beats": 4}],
                    "fx": [
                        {
                            "type": "sidechain",
                            "key_track": "drums",
                            "key_lane": "kick",
                            "depth": 0.5,
                        }
                    ],
                },
            ]
        )
    )
    assert song2.check() == []
    # cycles are rejected: a keys on b, b keys on a
    song3 = Song.model_validate(
        _song_with(
            tracks=[
                {
                    "id": "a",
                    "instrument": {"plugin": "p"},
                    "notes": [{"beat": 1, "pitch": "C2", "length_beats": 4}],
                    "fx": [{"type": "sidechain", "key_track": "b", "depth": 0.5}],
                },
                {
                    "id": "b",
                    "instrument": {"plugin": "p"},
                    "notes": [{"beat": 1, "pitch": "C2", "length_beats": 4}],
                    "fx": [{"type": "sidechain", "key_track": "a", "depth": 0.5}],
                },
            ]
        )
    )
    assert any(i.code == "sidechain_cycle" for i in song3.check())


def test_instrument_notes_inline():
    song = Song.model_validate(
        _song_with(
            tracks=[
                {
                    "id": "bass",
                    "instrument": {"plugin": "surge-xt"},
                    "notes": [
                        {"beat": 1, "pitch": "F#2", "length_beats": 2},
                        {"beat": 3, "pitch": 42, "length_beats": 2, "velocity": 90},
                    ],
                }
            ]
        )
    )
    assert song.tracks[0].notes[0].pitch == 42  # 'F#2' parsed to MIDI
    assert song.check() == []
    # midi and notes are mutually exclusive
    with pytest.raises(ValidationError):
        Song.model_validate(
            _song_with(
                tracks=[
                    {
                        "id": "bass",
                        "instrument": {"plugin": "p"},
                        "midi": "midi/a.mid",
                        "notes": [{"beat": 1, "pitch": 42, "length_beats": 2}],
                    }
                ]
            )
        )


def test_automation_duplicate_target_and_bounds():
    song = Song.model_validate(
        _song_with(
            tracks=[
                {
                    "id": "t",
                    "clips": [],
                    "fx": [{"id": "lp", "type": "lowpass", "cutoff_hz": 1000}],
                }
            ],
            automation=[
                {"target": "tracks.t.fx.lp.cutoff_hz", "points": [[1, 200], [9, 2000]]},
                {"target": "tracks.t.fx.lp.cutoff_hz", "points": [[1, 400], [9, 800]]},
            ],
        )
    )
    assert any(i.code == "duplicate_target" for i in song.check())
    # value outside the param's own bounds is an issue
    song = Song.model_validate(
        _song_with(
            tracks=[
                {
                    "id": "t",
                    "clips": [],
                    "fx": [{"id": "lp", "type": "lowpass", "cutoff_hz": 1000}],
                }
            ],
            automation=[
                {"target": "tracks.t.fx.lp.cutoff_hz", "points": [[1, 200], [9, 50000]]},
            ],
        )
    )
    assert any(i.code == "out_of_range" for i in song.check())
    # NaN is rejected outright
    with pytest.raises(ValidationError):
        Song.model_validate(
            _song_with(
                tracks=[{"id": "t", "clips": []}],
                automation=[
                    {"target": "tracks.t.gain_db", "points": [[1, 0.0], [9, float("nan")]]},
                ],
            )
        )


def test_automation_plugin_param_target():
    song = Song.model_validate(
        _song_with(
            tracks=[
                {
                    "id": "t",
                    "clips": [],
                    "fx": [
                        {
                            "id": "sx",
                            "type": "plugin",
                            "plugin": "surge-xt",
                            "params": {"cutoff": 0.5},
                        }
                    ],
                }
            ],
            automation=[
                {
                    "target": "tracks.t.fx.sx.params.cutoff",
                    "points": [[1, 0.1], [9, 0.9]],
                },
            ],
        )
    )
    assert song.check() == []
    # .params.<name> on a non-plugin fx is a target error
    song = Song.model_validate(
        _song_with(
            tracks=[
                {
                    "id": "t",
                    "clips": [],
                    "fx": [{"id": "lp", "type": "lowpass", "cutoff_hz": 1000}],
                }
            ],
            automation=[
                {"target": "tracks.t.fx.lp.params.cutoff", "points": [[1, 0.1], [9, 0.9]]},
            ],
        )
    )
    assert any(i.code == "automation_target" for i in song.check())


def test_ids_reject_reserved_names():
    for bad in ("con", "aux", "com1", "lpt9"):
        with pytest.raises(ValidationError):
            Song.model_validate(_song_with(sources=[{"id": bad, "file": "sources/a.wav"}]))


def test_relpath_rejects_unportable_names():
    with pytest.raises(ValidationError):
        Song.model_validate(_song_with(sources=[{"id": "s1", "file": "sources/con.wav"}]))
    with pytest.raises(ValidationError):
        Song.model_validate(_song_with(sources=[{"id": "s1", "file": "sources/a:b.wav"}]))
    with pytest.raises(ValidationError):
        Song.model_validate(_song_with(sources=[{"id": "s1", "file": "sources/trailing./x.wav"}]))


def test_start_offset_beats_in_schema():
    song = Song.model_validate(
        _song_with(
            sources=[{"id": "s1", "file": "sources/a.wav"}],
            chops=[
                {
                    "id": "c1",
                    "source": "s1",
                    "start_downbeat": 2,
                    "start_offset_beats": -0.5,
                    "length_beats": 4,
                }
            ],
        )
    )
    assert song.chops[0].start_offset_beats == -0.5
