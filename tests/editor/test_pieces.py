"""song.pieces: what the browser editor plays, and how exact it is."""

from __future__ import annotations

import numpy as np
import soundfile as sf

from muestra.project import load_song
from muestra.registry import run_tool
from muestra.render.automation import pan_gain
from muestra.render.core import chop_audio, render_audio, xfade_frames


def pieces(project, **kw):
    r = run_tool("song.pieces", {"project": str(project.root), **kw})
    assert r.ok, r.errors
    return r.data


def audio(project, data, piece_id):
    piece = next(p for p in data["pieces"] if p["id"] == piece_id)
    a, sr = sf.read(project.path(piece["path"]), dtype="float32", always_2d=True)
    assert sr == data["sample_rate"] and len(a) == piece["frames"]
    return a


def test_dry_pieces_are_the_renderers_own_audio(editor_project, fake_instrument):
    data = pieces(editor_project)
    song = load_song(editor_project)
    sr = data["sample_rate"]
    base = next(m for m in data["materials"] if m["base"])
    assert base["chop"] == "c1" and base["length"] == 8
    chop = song.chops[0]
    expected = chop_audio(editor_project, chop, sr, song.meta.bpm, xfade_frames(sr, chop.fade_ms))
    assert np.array_equal(audio(editor_project, data, base["piece"]), expected)
    assert set(data["oneshots"]) == {
        "library://kits/disco-01/kick.wav",
        "library://kits/disco-01/clap.wav",
        "library://kits/disco-01/ohat.wav",
    }
    bass = next(t for t in data["tracks"] if t["id"] == "bass")
    stem = audio(editor_project, data, bass["dry_stem"])
    assert len(stem) == data["frames"] and np.max(np.abs(stem)) > 0.01
    # all pieces are served by /files (renders/ or cache/previews/)
    assert all(p["path"].startswith("cache/previews/pieces/") for p in data["pieces"])


def test_wet_stem_through_the_browser_fader_equals_the_render_stem(editor_project, fake_instrument):
    song = load_song(editor_project)
    data = pieces(editor_project, wet=True, dry=False, tracks=["sample", "drums"])
    _, stems, _, _ = render_audio(editor_project, song, (1, song.meta.length_bars + 1))
    for tid in ("sample", "drums"):
        track = next(t for t in song.tracks if t.id == tid)
        wet = audio(editor_project, data, next(t for t in data["tracks"] if t["id"] == tid)["wet"])
        # the editor's fader on a wet stem: gain + pan RELATIVE to the centre (the
        # renderer's -3 dB centre law is already in the wet stem)
        browser = pan_gain(wet, track.gain_db, track.pan) / np.float32(np.cos(np.pi / 4))
        ref = stems[tid]
        err = np.max(np.abs(browser - ref))
        assert err < 1e-5, (tid, err)


def test_variant_passes_get_their_own_material(editor_project, fake_instrument):
    ops = [
        {
            "op": "set",
            "path": "tracks.sample.clips[0].instances.2",
            "value": {"pitch_semitones": 5},
        },
        {
            "op": "set",
            "path": "tracks.sample.clips[0].instances.4",
            "value": {"reverse": True, "gain_db": -3},
        },
    ]
    assert run_tool("song.edit", {"project": str(editor_project.root), "ops": ops}).ok
    data = pieces(editor_project)
    variants = [m for m in data["materials"] if not m["base"]]
    # pitch needs a render; reverse/gain are done by the browser on the base material
    assert [(m["chop"], m["pitch"]) for m in variants] == [("c1", 5.0)]


def test_stale_revision_is_reported(editor_project):
    r = run_tool("song.pieces", {"project": str(editor_project.root), "revision": "0" * 16})
    assert not r.ok and r.errors[0].code == "stale_revision"


def test_unchanged_wet_is_not_rendered_twice(editor_project):
    first = pieces(editor_project, wet=True, dry=False, tracks=["drums"])
    again = pieces(editor_project, wet=True, dry=False, tracks=["drums"])
    assert first["tracks"][0]["wet"] == again["tracks"][0]["wet"]
    assert again["tracks"][0]["wet_render_s"] < 0.5


def test_a_failing_track_does_not_block_the_others(editor_project):
    data = pieces(editor_project)  # surge-xt is not installed for this fixture
    bass = next(t for t in data["tracks"] if t["id"] == "bass")
    assert bass["error"].startswith("No pude preparar el audio")
    assert data["materials"] and data["oneshots"]
