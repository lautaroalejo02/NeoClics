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
    assert validate_song(example_project, check_files=True) == []


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
    song = Song.model_validate({"meta": {"title": "x", "bpm": 100, "key": "f#M", "length_bars": 4}})
    assert song.meta.key == "F#m"


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
