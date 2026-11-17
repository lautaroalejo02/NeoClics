"""song.edit: every op, its inverse (byte-identical undo), atomicity, conflicts."""

from __future__ import annotations

import pytest

from muestra.registry import run_tool
from muestra.schemas.song import pattern_hits


def edit(project, ops, **kw):
    r = run_tool("song.edit", {"project": str(project.root), "ops": ops, **kw})
    return r


def ok(project, ops, **kw):
    r = edit(project, ops, **kw)
    assert r.ok, r.errors
    return r.data


def roundtrip(project, ops):
    """Apply ops, then their inverse: song.yaml must come back byte for byte."""
    before = project.song_path.read_bytes()
    data = ok(project, ops)
    assert data["changed"]
    after = project.song_path.read_bytes()
    assert after != before
    undo = ok(project, data["inverse"], base_revision=data["revision"])
    assert project.song_path.read_bytes() == before
    # redo (the applied primitives) gives the edited bytes back too
    ok(project, data["applied"], base_revision=undo["revision"])
    assert project.song_path.read_bytes() == after
    ok(project, data["inverse"])
    assert project.song_path.read_bytes() == before
    return data


CASES = {
    "set_field": [{"op": "set", "path": "tracks.sample.gain_db", "value": -6}],
    "set_new_key": [{"op": "set", "path": "tracks.drums.pan", "value": 0.25}],
    "unset": [{"op": "unset", "path": "tracks.sample.gain_db"}],
    "chop_pitch": [{"op": "set", "path": "chops.c1.warp.pitch_semitones", "value": 3}],
    "fx_param": [{"op": "set", "path": "tracks.sample.fx.hp.cutoff_hz", "value": 300}],
    "add_fx": [
        {
            "op": "insert",
            "path": "tracks.drums.fx",
            "value": {"id": "sat", "type": "saturation", "drive_db": 6},
        }
    ],
    "remove_fx": [{"op": "remove", "path": "tracks.drums.fx.comp"}],
    "reorder_fx": [{"op": "move", "path": "tracks.sample.fx[0]", "to": 1}],
    "move_clip": [
        {"op": "set", "path": "tracks.sample.clips[0].at_bar", "value": 3},
        {"op": "set", "path": "tracks.sample.clips[0].repeat", "value": 4},
    ],
    "duplicate_clip": [
        {"op": "set", "path": "tracks.sample.clips[0].repeat", "value": 2},
        {"op": "duplicate_clip", "track": "sample", "clip": 0},
    ],
    "remove_clip": [{"op": "remove", "path": "tracks.sample.clips[0]"}],
    "trim_clip": [
        {"op": "set", "path": "tracks.sample.clips[0].trim_start_beats", "value": 2},
        {"op": "set", "path": "tracks.sample.clips[0].length_beats", "value": 4},
    ],
    "split_clip_inside": [{"op": "split_clip", "track": "sample", "clip": 0, "at_beat": 11}],
    "split_clip_boundary": [{"op": "split_clip", "track": "sample", "clip": 0, "at_beat": 17}],
    "mute_instance": [
        {"op": "set", "path": "tracks.sample.clips[0].instances.3", "value": {"mute": True}}
    ],
    "remove_hit": [
        {"op": "hit", "track": "drums", "lane": "kick", "bar": 7, "step": 8, "velocity": None}
    ],
    "add_hit": [
        {"op": "hit", "track": "drums", "lane": "clap", "bar": 2, "step": 14, "velocity": 0.6}
    ],
    "free_hits": [
        {
            "op": "add_hits",
            "track": "drums",
            "hits": [
                {"beat": 9.37, "lane": "kick"},
                {"beat": 12.0, "lane": "clap", "velocity": 0.5},
            ],
        }
    ],
    "add_track": [
        {
            "op": "insert",
            "path": "tracks",
            "value": {
                "id": "perc",
                "pattern": {
                    "lanes": {"rim": {"sample": "library://kits/disco-01/clap.wav"}},
                    "hits": [{"beat": 2.5, "lane": "rim"}],
                },
            },
        }
    ],
    "remove_track": [{"op": "remove", "path": "tracks.bass"}],
    "automation_point": [
        {"op": "add_point", "target": "tracks.sample.fx.lp.cutoff_hz", "bar": 3, "value": 4000}
    ],
    "new_automation": [
        {"op": "add_point", "target": "tracks.drums.gain_db", "bar": 2, "value": -3}
    ],
    "move_point": [
        {
            "op": "set",
            "path": "automation.tracks.sample.fx.lp.cutoff_hz.points[1]",
            "value": [6, 18000],
        }
    ],
    "remove_point_and_curve": [
        {"op": "set", "path": "automation.tracks.sample.fx.lp.cutoff_hz.curve", "value": "lin"}
    ],
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_op_and_inverse_are_byte_identical(editor_project, name):
    roundtrip(editor_project, CASES[name])


def test_hit_ops_edit_exactly_one_hit(editor_project):
    from muestra.project import load_song

    data = ok(editor_project, CASES["remove_hit"])
    assert data["changes"] == ["Saqué el bombo de la batería del tercer tiempo del compás 7"]
    song = load_song(editor_project)
    hits = [h for h in pattern_hits(song, song.tracks[1]) if h.lane == "kick"]
    assert len(hits) == 63
    # restoring the hit by a hit op drops the override entirely (clean YAML)
    ok(
        editor_project,
        [{"op": "hit", "track": "drums", "lane": "kick", "bar": 7, "step": 8, "velocity": 1}],
    )
    assert "overrides" not in editor_project.song_path.read_text("utf-8")


def test_atomic_all_or_nothing(editor_project):
    before = editor_project.song_path.read_bytes()
    r = edit(
        editor_project,
        [
            {"op": "set", "path": "tracks.sample.gain_db", "value": -3},
            {
                "op": "set",
                "path": "tracks.sample.clips[0].at_bar",
                "value": 15,
            },  # overruns the song
        ],
    )
    assert not r.ok
    assert r.errors[0].code == "edit_rejected"
    assert "inválida" in r.errors[0].message
    assert any(e.code == "clip_overrun" for e in r.errors)
    assert editor_project.song_path.read_bytes() == before


def test_bad_path_is_a_spanish_error(editor_project):
    r = edit(editor_project, [{"op": "set", "path": "tracks.nope.gain_db", "value": 1}])
    assert not r.ok and r.errors[0].code == "invalid_path"
    assert "«nope»" in r.errors[0].message


def test_conflict_when_the_agent_changed_the_same_element(editor_project):
    data = ok(editor_project, [{"op": "set", "path": "tracks.sample.gain_db", "value": -6}])
    # the agent edits the same field by hand
    text = editor_project.song_path.read_text("utf-8").replace("gain_db: -6", "gain_db: -9")
    editor_project.song_path.write_text(text, "utf-8", newline="\n")
    r = edit(editor_project, data["inverse"], base_revision=data["revision"])
    assert not r.ok and r.errors[0].code == "edit_conflict"
    assert "gain_db: -9" in editor_project.song_path.read_text("utf-8")


def test_independent_edits_rebase(editor_project):
    data = ok(editor_project, [{"op": "set", "path": "tracks.sample.gain_db", "value": -6}])
    text = editor_project.song_path.read_text("utf-8").replace("bpm: 120", "bpm: 122")
    editor_project.song_path.write_text(text, "utf-8", newline="\n")
    undo = ok(editor_project, data["inverse"], base_revision=data["revision"])
    assert undo["rebased"]
    after = editor_project.song_path.read_text("utf-8")
    assert "bpm: 122" in after and "gain_db: -2" in after


def test_comments_survive_edits(editor_project):
    ok(
        editor_project,
        [{"op": "set", "path": "tracks.sample.clips[0].instances.3", "value": {"mute": True}}],
    )
    text = editor_project.song_path.read_text("utf-8")
    assert "# 8 beats per instance * repeat 8" in text
    assert "instances: {3: {mute: true}}" in text


def test_dry_run_writes_nothing(editor_project):
    before = editor_project.song_path.read_bytes()
    data = ok(editor_project, CASES["remove_track"], dry_run=True)
    assert data["changes"] and editor_project.song_path.read_bytes() == before


def test_revert_to_version_writes_its_exact_bytes(versioned_project):
    project, v1_bytes = versioned_project
    ok(project, CASES["remove_hit"])
    ok(project, CASES["move_clip"])
    state = run_tool("song.state", {"project": str(project.root)}).data
    assert state["dirty"] and state["base_version"] == "v001"
    assert state["summary"].startswith("Edición manual: ")
    assert "saqué el bombo de la batería del tercer tiempo del compás 7" in state["summary"]
    assert "moví el clip del chop c1 del compás 1 al 3" in state["summary"]
    data = ok(project, [{"op": "revert_to", "version": "v001"}])
    assert project.song_path.read_bytes() == v1_bytes
    state = run_tool("song.state", {"project": str(project.root)}).data
    assert not state["dirty"]
    # discard is itself undoable
    ok(project, data["inverse"], base_revision=data["revision"])
    assert run_tool("song.state", {"project": str(project.root)}).data["dirty"]


def test_split_clip_keeps_the_audio_positions(editor_project):
    from muestra.project import load_song
    from muestra.schemas.song import clip_passes

    song0 = load_song(editor_project)
    ok(editor_project, CASES["split_clip_inside"])
    song = load_song(editor_project)
    passes = clip_passes(song, song.tracks[0])
    starts = [(p.start0, p.length, p.material_start) for p in passes]
    assert starts[:3] == [(0, 8, 0), (8, 2, 0), (10, 6, 2)]
    assert sum(p.length for p in passes) == sum(
        p.length for p in clip_passes(song0, song0.tracks[0])
    )
