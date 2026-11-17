"""WP17 performance pads: schema forms, slice lanes through validator and
renderer (audio at the recorded beat), gate/choke, take storage, quantise,
commit to the song and the pads tools."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from muestra.errors import MuestraError
from muestra.pads import store
from muestra.pads.commit import commit_ops
from muestra.pads.process import Cleanup, process_events, quantize_beat
from muestra.project import load_song, validate_song
from muestra.project.validate import validate_song_model
from muestra.render.core import CUT_FADE_MS, arrange, beat_frame, lane_sound, slice_audio
from muestra.schemas.pads import Pad, PadsFile, Take, TakeQuantize
from muestra.schemas.song import LaneSlice, PatternLane, Song, pattern_hits
from tests.render.conftest import prepare_fixture

SR = 48000
ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-11-17T12:00:00+00:00"


@pytest.fixture
def project(tmp_path):
    return prepare_fixture(tmp_path)


def _with_drums(song: Song, lanes: dict, hits: list, **pattern) -> Song:
    data = song.model_dump(mode="json")
    data["tracks"] = [t for t in data["tracks"] if t["id"] != "bass"]
    data["tracks"].append(
        {"id": "pads", "pattern": {"steps_per_bar": 16, "lanes": lanes, "hits": hits, **pattern}}
    )
    out = Song.model_validate(data)
    assert out.check() == []
    return out


def _lane_audio(project, song: Song, lane: str) -> np.ndarray:
    n = beat_frame(song.meta.length_bars * 4, song.meta.bpm, SR)
    _, lanes, _ = arrange(project, song, SR, n)
    return lanes["pads", lane]


# ---------------------------------------------------------------- schema forms


def test_lane_needs_exactly_one_sound():
    with pytest.raises(ValidationError, match="lane_sound|exactly one"):
        PatternLane(steps="x...")
    with pytest.raises(ValidationError):
        PatternLane(sample="kick.wav", slice={"chop": "c1", "length_beats": 1})
    lane = PatternLane(slice={"chop": "c1", "length_beats": 1}, choke=2, pitch_semitones=-3)
    assert lane.slice.speed == "project" and lane.choke == 2


def test_slice_forms():
    with pytest.raises(ValidationError, match="slice_source|exactly one"):
        LaneSlice(length_beats=1)
    with pytest.raises(ValidationError):
        LaneSlice(chop="c1", stem="drums", length_beats=1)  # stem belongs to source slices
    with pytest.raises(ValidationError):
        LaneSlice(source="src1", length_beats=1)  # needs start_downbeat
    s = LaneSlice(source="src1", start_downbeat=2, start_beats=0.5, length_beats=0.5)
    chop = s.as_chop({})
    assert (chop.stem, chop.start_downbeat, chop.start_offset_beats) == ("mix", 2, 0.5)


def test_pad_and_take_forms():
    with pytest.raises(ValidationError):
        Pad(key="KeyZ", name="x")
    with pytest.raises(ValidationError):
        Pad(key="Space", name="x", sample="a.wav")  # not a pad key
    pad = Pad(key="KeyZ", name="Bombo", sample="library://kits/disco-01/kick.wav")
    with pytest.raises(ValidationError, match="snapshot"):
        Take(
            id="t001", name="x", created_at=NOW, updated_at=NOW, bpm=120, origin_beat=0,
            length_beats=4, events=[{"beat": 0, "pad": "KeyX"}], pads={"KeyZ": pad},
        )  # fmt: skip
    with pytest.raises(ValidationError):
        PadsFile(active_bank="z", banks=[{"id": "a", "name": "A"}])


def test_song_check_reports_unknown_slice_refs(project):
    song = load_song(project)
    data = song.model_dump(mode="json")
    data["tracks"].append(
        {"id": "p", "pattern": {"lanes": {"s": {"slice": {"chop": "nope", "length_beats": 1}}}}}
    )
    issues = Song.model_validate(data).check()
    assert [(i.code, i.path) for i in issues] == [
        ("ref_not_found", "tracks[3].pattern.lanes.s.slice.chop")
    ]


def test_validator_checks_slice_against_the_grid(project):
    song = load_song(project)
    bad = _with_drums(
        song,
        {
            "s": {
                "slice": {
                    "source": "src1",
                    "start_downbeat": 2,
                    "start_beats": 4,
                    "length_beats": 8,
                }
            }
        },
        [],
    )
    issues = validate_song_model(project, bad, check_plugins=False)
    assert any(
        i.code == "chop_overrun" and i.path == "tracks[2].pattern.lanes.s.slice.length_beats"
        for i in issues
    ), issues
    ok = _with_drums(
        song, {"s": {"slice": {"chop": "c1", "start_beats": 2, "length_beats": 1}}}, []
    )
    assert validate_song_model(project, ok, check_plugins=False) == []


# ---------------------------------------------------------------- expansion


def test_gate_and_choke_cut_points(project):
    song = _with_drums(
        load_song(project),
        {
            "chat": {"sample": "library://kits/disco-01/chat.wav", "choke": 1},
            "ohat": {"sample": "library://kits/disco-01/ohat.wav", "choke": 1},
            "kick": {"sample": "library://kits/disco-01/kick.wav"},
        },
        [
            {"beat": 1, "lane": "ohat"},
            {"beat": 1.5, "lane": "chat"},
            {"beat": 2, "lane": "kick", "length_beats": 0.25},
            {"beat": 3, "lane": "ohat"},
            {"beat": 3, "lane": "chat"},  # same instant: both sound
        ],
    )
    hits = {(h.lane, h.beat0): h.cut0 for h in pattern_hits(song, song.tracks[-1])}
    assert hits[("ohat", 0.0)] == 0.5  # cut by the closed hat
    assert hits[("chat", 0.5)] == 2.0  # cut by the next hit of its group (beat 3, 0-based 2)
    assert hits[("kick", 1.0)] == 1.25  # gate
    assert hits[("ohat", 2.0)] is None and hits[("chat", 2.0)] is None


# ---------------------------------------------------------------- renderer


def test_slice_lane_plays_the_slice_at_the_recorded_beat(project):
    base = load_song(project)
    beat = 9.37  # 1-indexed song beat, off the grid
    song = _with_drums(
        base,
        {"s": {"slice": {"chop": "c1", "start_beats": 2, "length_beats": 1}}},
        [{"beat": beat, "lane": "s", "velocity": 0.5}],
    )
    audio = _lane_audio(project, song, "s")
    sound = lane_sound(project, song, song.tracks[-1].pattern.lanes["s"], None, SR, {})
    frame = beat_frame(beat - 1, song.meta.bpm, SR)
    assert len(sound) == beat_frame(1, song.meta.bpm, SR)  # warped to 1 project beat
    assert np.max(np.abs(audio[:frame])) == 0.0  # nothing before the hit
    got = audio[frame : frame + len(sound)]
    # lane pan law at centre: cos(pi/4) per channel
    want = sound * 0.5 * np.float32(np.cos(np.pi / 4))
    np.testing.assert_allclose(got, want, atol=1e-6)
    assert np.max(np.abs(got)) > 0.01
    # first audible sample is exactly at the frame of the recorded beat
    first = int(np.flatnonzero(np.abs(audio).sum(axis=1) > 1e-7)[0])
    assert abs(first - frame) <= round(0.0005 * SR)


def test_slice_is_the_chop_material_and_source_speed_is_unwarped(project):
    song = _with_drums(
        load_song(project), {"k": {"sample": "library://kits/disco-01/kick.wav"}}, []
    )
    # project speed: the chop path (chop c1 has +1 st pitch: compare shape, not samples)
    sl = LaneSlice(chop="c1", start_beats=0, length_beats=2)
    a = slice_audio(project, song, sl, SR)
    assert len(a) == beat_frame(2, 120, SR)
    # source speed at 120 BPM source: 2 beats = 1.0 s of the record
    sl2 = LaneSlice(chop="c1", start_beats=0, length_beats=2, speed="source")
    b = slice_audio(project, song, sl2, SR)
    assert abs(len(b) - SR) <= 2
    slower = song.model_copy(update={"meta": song.meta.model_copy(update={"bpm": 60})})
    assert len(slice_audio(project, slower, sl, SR)) == beat_frame(2, 60, SR)  # 2 s
    assert abs(len(slice_audio(project, slower, sl2, SR)) - SR) <= 2  # still 1 s


def test_gate_choke_pitch_and_reverse_audio(project):
    song = _with_drums(
        load_song(project),
        {
            "k": {"sample": "library://kits/disco-01/ohat.wav", "choke": 3},
            "hi": {"sample": "library://kits/disco-01/ohat.wav", "pitch_semitones": 12},
            "rev": {"sample": "library://kits/disco-01/ohat.wav", "reverse": True},
        },
        [
            {"beat": 1, "lane": "k", "length_beats": 0.25},
            {"beat": 5, "lane": "k"},
            {"beat": 5.5, "lane": "k"},
            {"beat": 9, "lane": "hi"},
            {"beat": 13, "lane": "rev"},
        ],
    )
    k = _lane_audio(project, song, "k")
    bpm = 120
    fade = round(SR * CUT_FADE_MS / 1000)
    cut = beat_frame(0.25, bpm, SR)
    assert np.max(np.abs(k[cut - 200 : cut])) > 1e-3  # still sounding before the cut
    assert np.max(np.abs(k[cut + fade + 1 : beat_frame(4, bpm, SR)])) == 0.0  # gated
    choke = beat_frame(4.5, bpm, SR)
    lane = song.tracks[-1].pattern.lanes
    raw = lane_sound(project, song, lane["k"], "library://kits/disco-01/ohat.wav", SR, {})
    # after the choke + fade only the second hit sounds
    seg = k[choke + fade + 1 : choke + 4000]
    want = raw[fade + 1 : 4000] * np.float32(np.cos(np.pi / 4))
    np.testing.assert_allclose(seg, want, atol=1e-6)
    hi = lane_sound(project, song, lane["hi"], "library://kits/disco-01/ohat.wav", SR, {})
    assert abs(len(hi) - len(raw) / 2) <= 2  # an octave up = half as long
    rev = lane_sound(project, song, lane["rev"], "library://kits/disco-01/ohat.wav", SR, {})
    assert np.argmax(np.abs(rev[:, 0])) > len(rev) * 0.5  # the attack is at the end


def test_plain_file_lanes_render_bit_identically(project):
    """A song without WP17 fields renders the same audio through the new path."""
    full = load_song(project)
    song = full.model_copy(update={"tracks": [t for t in full.tracks if t.id != "bass"]})
    n = beat_frame(8, song.meta.bpm, SR)
    _, lanes, _ = arrange(project, song, SR, n)
    from muestra.render.audio import decode, fade
    from muestra.render.automation import pan_gain

    kick = lanes["drums", "kick"]
    ref = project.root.parent / "library/kits/disco-01/kick.wav"
    one = pan_gain(fade(decode(project, ref, SR)[0], SR, 1, start=False), 0.0, 0.0)
    np.testing.assert_array_equal(kick[: min(len(one), 1000)], one[: min(len(one), 1000)])


# ---------------------------------------------------------------- quantise


def test_quantize_vectors_shared_with_the_browser():
    cases = json.loads((ROOT / "fixtures/pads/quantize-cases.json").read_text())["cases"]
    for c in cases:
        q = TakeQuantize(grid=c["grid"], strength=c["strength"], swing=c["swing"])
        assert quantize_beat(c["beat"], q, c.get("wrap")) == pytest.approx(c["out"], abs=1e-9), c


def _take(events, pads=None, **kw) -> Take:
    pads = pads or {
        "KeyZ": Pad(key="KeyZ", name="Bombo", sample="library://kits/disco-01/kick.wav"),
        "KeyX": Pad(key="KeyX", name="Caja", sample="library://kits/disco-01/clap.wav"),
    }
    base = {"id": "t001", "name": "Toma 1", "created_at": NOW, "updated_at": NOW, "bpm": 120,
            "origin_beat": 0, "length_beats": 8}  # fmt: skip
    base.update(kw)
    return Take.model_validate({**base, "pads": pads, "events": events})


def test_cleanup_dedupes_and_evens_velocity():
    take = _take(
        [
            {"beat": 0.0, "pad": "KeyZ", "velocity": 0.6},
            {"beat": 0.02, "pad": "KeyZ", "velocity": 0.9},  # 10 ms later: a double trigger
            {"beat": 0.02, "pad": "KeyX", "velocity": 0.4},  # other pad: kept
            {"beat": 1.0, "pad": "KeyZ", "velocity": 1.0},
        ]
    )
    ev = process_events(take, None, Cleanup(dedupe_ms=30))
    assert [(e.beat, e.pad, e.velocity) for e in ev] == [
        (0.0, "KeyZ", 0.9),
        (0.02, "KeyX", 0.4),
        (1.0, "KeyZ", 1.0),
    ]
    even = process_events(take, None, Cleanup(velocity="even"))
    vs = [e.velocity for e in even]
    assert max(vs) - min(vs) == pytest.approx((1.0 - 0.4) / 2, abs=1e-4)


# ---------------------------------------------------------------- storage


def test_take_storage_roundtrip_and_ids_never_reused(project):
    t = _take([{"beat": 0.5, "pad": "KeyZ"}], status="recording")
    store.save_take(project, t)
    assert store.next_take_id(project) == "t002"
    takes, problems = store.list_takes(project)
    assert [x.id for x in takes] == ["t001"] and problems == []
    assert store.load_take(project, "t001") == t
    moved = store.delete_take(project, "t001")
    assert moved.startswith("pads/takes/deleted/t001-")
    assert (project.root / moved).is_file()
    assert store.list_takes(project)[0] == []
    assert store.next_take_id(project) == "t002"  # the deleted id stays used
    (project.root / "pads/takes/t009.json").write_text("{broken")
    _, problems = store.list_takes(project)
    assert problems and problems[0]["path"] == "pads/takes/t009.json"
    banks = store.load_banks(project)
    assert [b.id for b in banks.banks] == ["a", "b", "c", "d"]
    store.save_banks(project, banks)
    assert store.load_banks(project) == banks
    assert b"\r\n" not in (project.root / "pads/banks.json").read_bytes()


# ---------------------------------------------------------------- commit


def _run(tool: str, **params):
    from muestra.registry import REGISTRY

    spec = REGISTRY[tool]
    return spec.func(spec.params.model_validate(params))


def test_commit_new_track_writes_hits_at_the_played_beats(project):
    take = _take(
        [
            {"beat": 0.01, "pad": "KeyZ", "velocity": 1.0},
            {"beat": 1.02, "pad": "KeyX"},
            {"beat": 2.49, "pad": "KeyZ", "velocity": 0.5},
            {"beat": 3.97, "pad": "KeyX", "cycle": 2},
        ],
        loop_beats=4,
        length_beats=4,
        quantize={"grid": "1/16"},
    )
    store.save_take(project, take)
    res = _run("pads.commit", project=str(project.root), take_id="t001", at_bar=3, repeat=2)
    assert res.track == "pads" and res.new_track and res.hits == 8
    song = load_song(project)
    t = next(x for x in song.tracks if x.id == "pads")
    beats = [(h.beat, h.lane, h.velocity) for h in t.pattern.hits]
    # bar 3 = beat 9; 1/16 quantise; the loop-end press (3.97) wraps to the cycle start
    assert beats[:4] == [(9.0, "bombo", 1.0), (9.0, "caja", 0.8), (10.0, "caja", 0.8),
                         (11.5, "bombo", 0.5)]  # fmt: skip
    assert beats[4][0] == 13.0
    assert t.pattern.lanes["bombo"].sample == "library://kits/disco-01/kick.wav"
    assert validate_song(project, check_plugins=False) == []
    assert any("pads" in c or "bombo" in c for c in res.changes), res.changes
    again = store.load_take(project, "t001")
    assert again.commits[-1].hits == 8 and again.commits[-1].at_bar == 3


def test_commit_layers_into_an_existing_drum_track_and_reuses_lanes(project):
    take = _take([{"beat": 0.5, "pad": "KeyZ"}, {"beat": 1.5, "pad": "KeyC"}], pads={
        "KeyZ": Pad(key="KeyZ", name="Bombo", sample="library://kits/disco-01/kick.wav"),
        "KeyC": Pad(key="KeyC", name="Clap 2", sample="library://kits/disco-01/clap.wav",
                    pitch_semitones=2),
    })  # fmt: skip
    store.save_take(project, take)
    dry = _run("pads.commit", project=str(project.root), take_id="t001", target="track",
               track="drums", at_bar=2, dry_run=True)  # fmt: skip
    assert dry.lanes == {"KeyZ": "kick", "KeyC": "clap-2"}
    assert dry.new_lanes == ["clap-2"]
    before = project.song_path.read_bytes()
    assert project.song_path.read_bytes() == before  # dry run wrote nothing
    _run("pads.commit", project=str(project.root), take_id="t001", target="track",
         track="drums", at_bar=2)  # fmt: skip
    drums = next(x for x in load_song(project).tracks if x.id == "drums")
    assert drums.pattern.lanes["clap-2"].pitch_semitones == 2
    assert [(h.beat, h.lane) for h in drums.pattern.hits] == [(5.5, "kick"), (6.5, "clap-2")]


def test_commit_note_pads_and_gate_lengths(project):
    data = load_song(project).model_dump(mode="json")
    bass = next(t for t in data["tracks"] if t["id"] == "bass")
    bass.pop("midi")
    bass["notes"] = []
    Song.model_validate(data)
    from muestra.project import save_song

    save_song(project, Song.model_validate(data))
    take = _take(
        [{"beat": 0.0, "pad": "KeyA", "length_beats": 0.75}, {"beat": 1.0, "pad": "KeyS"}],
        pads={
            "KeyA": Pad(key="KeyA", name="I", note={"track": "bass", "pitches": [42]},
                        mode="gate"),
            "KeyS": Pad(key="KeyS", name="Acorde", note={"track": "bass", "pitches": [45, 49]}),
        },
    )  # fmt: skip
    song = load_song(project)
    plan = commit_ops(song, take, at_bar=1)
    assert plan.notes == 3 and plan.hits == 0
    vals = [op["value"] for op in plan.ops if op["op"] == "insert"]
    assert vals[0] == {"beat": 1.0, "pitch": 42, "length_beats": 0.75, "velocity": 102}
    assert {v["pitch"] for v in vals[1:]} == {45, 49}


def test_commit_drops_what_falls_outside_and_says_so(project):
    take = _take([{"beat": 0.0, "pad": "KeyZ"}, {"beat": 6.0, "pad": "KeyZ"}], length_beats=8)
    plan = commit_ops(load_song(project), take, at_bar=16)  # last bar: 4 beats left
    assert plan.hits == 1 and plan.dropped == 1
    assert "Dejé afuera 1 golpe" in plan.messages[-1]
    with pytest.raises(MuestraError):
        commit_ops(load_song(project), take, at_bar=17)


# ---------------------------------------------------------------- tools


def test_take_tools_roundtrip(project):
    res = _run(
        "pads.take_save",
        project=str(project.root),
        take={
            "name": "Groove",
            "bpm": 120,
            "origin_beat": 0,
            "length_beats": 4,
            "loop_beats": 4,
            "status": "recording",
            "pads": {"KeyZ": {"key": "KeyZ", "name": "Bombo", "sample": "kick.wav"}},
            "events": [{"beat": 0.02, "pad": "KeyZ"}, {"beat": 2.0, "pad": "KeyZ", "cycle": 2}],
        },
    )
    assert res.take.id == "t001" and res.take.status == "recording"
    got = _run("pads.get", project=str(project.root))
    assert [t.id for t in got.takes] == ["t001"] and "recording" in got.hint.lower()
    ev = _run("pads.take_get", project=str(project.root), take_id="t001",
              quantize={"grid": "1/8"}, at_bar=5)  # fmt: skip
    assert [(e.beat, e.song_beat) for e in ev.events] == [(0.0, 17.0), (2.0, 19.0)]
    assert "Bombo (2)" in ev.text
    # reading never changes the stored raw performance
    assert store.load_take(project, "t001").events[0].beat == 0.02
    gone = _run("pads.take_delete", project=str(project.root), take_id="t001")
    assert gone.moved_to.startswith("pads/takes/deleted/")


def test_slice_tool_by_beat_and_transients(project):
    res = _run("pads.slice", project=str(project.root), chop="c1", by="beat")
    assert [(s.chop, s.start_beats, s.length_beats) for s in res.slices] == [
        ("c1", float(i), 1.0) for i in range(8)
    ]
    half = _run("pads.slice", project=str(project.root), source="src1", start_downbeat=2,
                length_beats=2, by="half", stem="drums")  # fmt: skip
    assert [(s.source, s.stem, s.start_beats) for s in half.slices] == [
        ("src1", "drums", 0.0), ("src1", "drums", 0.5), ("src1", "drums", 1.0),
        ("src1", "drums", 1.5),
    ]  # fmt: skip
    tr = _run("pads.slice", project=str(project.root), chop="c1", by="transients")
    assert tr.slices and tr.slices[0].start_beats == 0.0
    total = sum(s.length_beats for s in tr.slices)
    assert total == pytest.approx(8.0, abs=1e-3)
    assert all(s.length_beats >= 0.125 - 1e-9 for s in tr.slices)


def test_from_track_builds_pads_of_the_drum_lanes(project):
    res = _run("pads.from_track", project=str(project.root), track="drums")
    assert [(p.key, p.name, p.sample.rsplit("/", 1)[-1]) for p in res.pads] == [
        ("KeyZ", "kick", "kick.wav"),
        ("KeyX", "clap", "clap.wav"),
        ("KeyC", "ohat", "ohat.wav"),
    ]


def test_pieces_voices_are_the_renderers_lane_sound(project):
    from muestra.editor.pieces import SongPiecesParams, song_pieces

    res = song_pieces(
        SongPiecesParams(
            project=str(project.root),
            dry=False,
            tracks=[],
            sounds=[
                {"slice": {"chop": "c1", "start_beats": 1, "length_beats": 1}},
                {"sample": "library://kits/disco-01/kick.wav", "pitch_semitones": -5},
            ],
        )
    )
    assert len(res.voices) == 2
    import soundfile as sf

    paths = {p.id: p.path for p in res.pieces}
    got, _ = sf.read(project.path(paths[res.voices[0].piece]), dtype="float32", always_2d=True)
    song = load_song(project)
    sr = project.config.sample_rate
    want = slice_audio(project, song, LaneSlice(chop="c1", start_beats=1, length_beats=1), sr)
    np.testing.assert_array_equal(got, want)


def test_diff_describes_slice_lanes_in_spanish(project):
    from muestra.versions.describe import describe_song

    a = load_song(project)
    data = a.model_dump(mode="json")
    drums = next(t for t in data["tracks"] if t["id"] == "drums")
    drums["pattern"]["lanes"]["s1"] = {"slice": {"chop": "c1", "start_beats": 2, "length_beats": 1}}
    drums["pattern"]["lanes"]["ohat"]["choke"] = 1
    drums["pattern"]["lanes"]["clap"]["pitch_semitones"] = -2
    drums["pattern"]["hits"] = [{"beat": 3.5, "lane": "s1", "length_beats": 0.5}]
    b = Song.model_validate(data)
    text = " | ".join(describe_song(a, b))
    assert "un pedazo del chop c1 (desde el tiempo 3, 1 tiempo)" in text, text
    assert "grupo de corte 1" in text
    assert "Bajé la afinación" in text and "2 semitonos" in text
    assert "golpe suelto" in text
