"""Structured diff: ID-based paths, no phantom changes (pure, no git)."""

from __future__ import annotations

import random
import time

import yaml

from muestra.schemas.song import Song
from muestra.versions.diff import song_diff

from .helpers import EXAMPLE_SONG, make_song, song_dict, track


def diff(mut_a=None, mut_b=None):
    return song_diff(make_song(mut_a), make_song(mut_b))


def paths(mut_a=None, mut_b=None):
    return [e.path for e in diff(mut_a, mut_b)]


def entry(mut_a, mut_b, path):
    return next(e for e in diff(mut_a, mut_b) if e.path == path)


# three distinguishable clips on the sample track
def three_clips(d):
    track(d, "sample")["clips"] = [
        {"chop": "c1", "at_bar": 1, "repeat": 2},
        {"chop": "c1", "at_bar": 5, "repeat": 2},
        {"chop": "c1", "at_bar": 9, "repeat": 2},
    ]


def melody(d, pitches=("C3", "E3", "G3", "B3"), n=1):
    track(d, "bass").pop("midi")
    track(d, "bass")["notes"] = [
        {"beat": 1 + i, "pitch": p, "length_beats": 1} for i, p in enumerate(pitches * n)
    ]


# --------------------------------------------------------------------------
# contract grammar
# --------------------------------------------------------------------------


def test_contract_path_examples():
    assert paths(None, lambda d: d["chops"][0]["warp"].update(pitch_semitones=2)) == [
        "chops.c1.warp.pitch_semitones"
    ]
    e = diff(None, lambda d: track(d, "sample")["fx"][1].update(cutoff_hz=12000))
    assert [(x.path, x.from_, x.to) for x in e] == [("tracks.sample.fx.lp.cutoff_hz", 18000, 12000)]
    assert paths(
        None,
        lambda d: track(d, "drums")["pattern"]["lanes"]["kick"].update(steps="x.x.x.x.x.x.x.x."),
    ) == ["tracks.drums.pattern.lanes.kick.steps"]
    assert paths(None, lambda d: d["meta"].update(bpm=128)) == ["meta.bpm"]
    assert paths(
        None, lambda d: track(d, "bass")["instrument"].update(preset="presets/b2.fxp")
    ) == ["tracks.bass.instrument.preset"]
    assert paths(None, lambda d: d["master"]["fx"][1].update(ceiling_db=-2)) == [
        "master.fx[1].ceiling_db"
    ]


def test_id_less_fx_use_their_chain_index():
    assert paths(None, lambda d: d["master"]["fx"][0].update(ratio=3)) == ["master.fx[0].ratio"]
    assert paths(
        None, lambda d: track(d, "bass")["fx"].append({"type": "saturation", "drive_db": 6})
    ) == ["tracks.bass.fx[0]"]


def test_json_alias_from_is_serialised_as_from():
    e = diff(None, lambda d: d["meta"].update(bpm=128))[0]
    assert e.model_dump(by_alias=True) == {"path": "meta.bpm", "from": 120.0, "to": 128.0}


def test_identical_songs_have_no_diff():
    assert diff() == []


def test_formatting_comments_and_explicit_defaults_are_not_changes():
    text = EXAMPLE_SONG.read_text(encoding="utf-8")
    noisy = (
        "# un comentario\n"
        + text.replace("fade_ms: 5", "fade_ms: 5.0")
        .replace("gain_db: -2", "gain_db: -2.0\n    pan: 0.0\n    mute: false")
        .replace("swing: 0.0", "swing: 0")
        + "\n# fin\n"
    )
    a = Song.model_validate(yaml.safe_load(text))
    b = Song.model_validate(yaml.safe_load(noisy))
    assert song_diff(a, b) == []


# --------------------------------------------------------------------------
# insertion / deletion / reordering: no phantom changes
# --------------------------------------------------------------------------


def test_track_inserted_before_others_is_one_entry():
    assert paths(None, lambda d: d["tracks"].insert(0, {"id": "perc", "clips": []})) == [
        "tracks.perc"
    ]


def test_track_deleted_from_the_middle_is_one_entry():
    e = diff(None, lambda d: d["tracks"].pop(1))
    assert [x.path for x in e] == ["tracks.drums"]
    assert e[0].to is None and e[0].from_["id"] == "drums"


def test_reordering_tracks_reports_the_order_only():
    e = diff(None, lambda d: d["tracks"].reverse())
    assert [(x.path, x.from_, x.to) for x in e] == [
        ("tracks", ["sample", "drums", "bass"], ["bass", "drums", "sample"])
    ]


def test_inserting_a_track_does_not_report_an_order_change():
    assert paths(None, lambda d: d["tracks"].insert(1, {"id": "new", "clips": []})) == [
        "tracks.new"
    ]


def test_reordering_chops_lanes_and_automation_is_silent():
    def two_chops(d):
        d["chops"].append({**d["chops"][0], "id": "c2"})
        d["automation"].append(
            {"target": "tracks.drums.fx.comp.threshold_db", "points": [[1, -20], [4, -10]]}
        )

    def shuffled(d):
        two_chops(d)
        d["chops"].reverse()
        d["automation"].reverse()
        lanes = track(d, "drums")["pattern"]["lanes"]
        track(d, "drums")["pattern"]["lanes"] = dict(reversed(list(lanes.items())))

    assert paths(two_chops, shuffled) == []


def test_clip_inserted_first_is_one_entry_not_a_cascade():
    def add_front(d):
        three_clips(d)
        track(d, "sample")["clips"].insert(0, {"chop": "c1", "at_bar": 13})

    e = diff(three_clips, add_front)
    assert [x.path for x in e] == ["tracks.sample.clips[0]"]
    assert e[0].from_ is None and e[0].to["at_bar"] == 13


def test_clip_deleted_and_modified_are_found_by_content():
    def del_middle(d):
        three_clips(d)
        track(d, "sample")["clips"].pop(1)

    assert paths(three_clips, del_middle) == ["tracks.sample.clips[1]"]  # index in the OLD version

    def move_second(d):
        three_clips(d)
        track(d, "sample")["clips"][1]["at_bar"] = 6

    e = diff(three_clips, move_second)
    assert [(x.path, x.from_, x.to) for x in e] == [("tracks.sample.clips[1].at_bar", 5, 6)]

    def front_and_edit(d):
        three_clips(d)
        c = track(d, "sample")["clips"]
        c[2]["repeat"] = 3
        c.insert(0, {"chop": "c1", "at_bar": 13})

    assert sorted(paths(three_clips, front_and_edit)) == [
        "tracks.sample.clips[0]",
        "tracks.sample.clips[3].repeat",
    ]


def test_reordered_clips_are_not_a_change():
    def reordered(d):
        three_clips(d)
        track(d, "sample")["clips"].reverse()

    assert paths(three_clips, reordered) == []


def test_clip_warp_edit_has_nested_path():
    def pitched(d):
        three_clips(d)
        track(d, "sample")["clips"][0] = {
            "sample": "library://kits/disco-01/kick.wav",
            "length_beats": 4,
            "warp": {"mode": "stretch", "pitch_semitones": 1},
            "at_bar": 1,
        }

    def pitched2(d):
        pitched(d)
        track(d, "sample")["clips"][0]["warp"]["pitch_semitones"] = 3

    e = diff(pitched, pitched2)
    assert [x.path for x in e] == ["tracks.sample.clips[0].warp.pitch_semitones"]


def test_notes_transposed_added_removed_and_reordered():
    a = melody
    assert paths(a, lambda d: melody(d, ("D3", "F#3", "A3", "C#4"))) == [
        f"tracks.bass.notes[{i}].pitch" for i in range(4)
    ]

    def extra_first(d):
        melody(d)
        track(d, "bass")["notes"].insert(0, {"beat": 9, "pitch": "C5", "length_beats": 2})

    e = diff(a, extra_first)
    assert [x.path for x in e] == ["tracks.bass.notes[0]"] and e[0].from_ is None

    def reordered(d):
        melody(d)
        track(d, "bass")["notes"].reverse()

    assert paths(a, reordered) == []

    def one_longer(d):
        melody(d)
        track(d, "bass")["notes"][2]["length_beats"] = 2

    assert paths(a, one_longer) == ["tracks.bass.notes[2].length_beats"]


def test_midi_to_notes_is_one_kind_change():
    e = paths(None, lambda d: melody(d))
    assert "tracks.bass.midi" in e and "tracks.bass.notes" in e


def test_id_less_fx_inserted_first_does_not_shift_the_others():
    def chain(d):
        track(d, "bass")["fx"] = [
            {"type": "compressor", "threshold_db": -18, "ratio": 4},
            {"type": "saturation", "drive_db": 6},
        ]

    def inserted(d):
        chain(d)
        track(d, "bass")["fx"].insert(0, {"type": "limiter"})

    e = diff(chain, inserted)
    assert [x.path for x in e] == ["tracks.bass.fx[0]"]  # compressor/saturation untouched

    def inserted_and_edited(d):
        inserted(d)
        track(d, "bass")["fx"][2]["drive_db"] = 12

    assert sorted(paths(chain, inserted_and_edited)) == [
        "tracks.bass.fx[0]",
        "tracks.bass.fx[2].drive_db",
    ]


def test_fx_chain_order_is_reported_because_it_changes_the_sound():
    e = diff(None, lambda d: track(d, "sample")["fx"].reverse())
    assert [(x.path, x.from_, x.to) for x in e] == [
        ("tracks.sample.fx", ["hp", "lp", "sc"], ["sc", "lp", "hp"])
    ]


def test_fx_added_removed_by_id():
    def add(d):
        track(d, "drums")["fx"].insert(0, {"id": "hp2", "type": "highpass", "cutoff_hz": 40})

    assert paths(None, add) == ["tracks.drums.fx.hp2"]
    e = diff(None, lambda d: track(d, "sample")["fx"].pop(0))
    assert [x.path for x in e] == ["tracks.sample.fx.hp"]
    assert e[0].to is None


def test_plugin_params_and_instrument_params():
    def with_plugin(d):
        track(d, "bass")["fx"] = [
            {"id": "pl", "type": "plugin", "plugin": "x", "params": {"cutoff": 0.2, "res": 0.1}}
        ]
        track(d, "bass")["instrument"]["params"] = {"cutoff": 0.4}

    def tweaked(d):
        with_plugin(d)
        track(d, "bass")["fx"][0]["params"]["cutoff"] = 0.9
        track(d, "bass")["instrument"]["params"]["cutoff"] = 0.5

    assert sorted(paths(with_plugin, tweaked)) == [
        "tracks.bass.fx.pl.params.cutoff",
        "tracks.bass.instrument.params.cutoff",
    ]


# --------------------------------------------------------------------------
# automation, pattern, master, chops, sources
# --------------------------------------------------------------------------


def test_automation_paths():
    base = "automation.tracks.sample.fx.lp.cutoff_hz"
    assert paths(None, lambda d: d["automation"][0].update(points=[[1, 800], [9, 18000]])) == [
        base + ".points"
    ]
    assert paths(None, lambda d: d["automation"][0].update(curve="lin")) == [base + ".curve"]
    e = diff(None, lambda d: d["automation"].clear())
    assert [x.path for x in e] == [base] and e[0].to is None
    e = diff(
        None,
        lambda d: d["automation"].append(
            {"target": "tracks.bass.gain_db", "points": [[1, -12], [4, 0]]}
        ),
    )
    assert [x.path for x in e] == ["automation.tracks.bass.gain_db"] and e[0].from_ is None


def test_pattern_edits():
    def mut(d):
        p = track(d, "drums")["pattern"]
        p["swing"] = 0.2
        p["lanes"]["clap"]["gain_db"] = -3
        del p["lanes"]["ohat"]
        p["lanes"]["rim"] = {"sample": "library://kits/disco-01/rim.wav", "steps": "x" * 16}

    assert sorted(paths(None, mut)) == [
        "tracks.drums.pattern.lanes.clap.gain_db",
        "tracks.drums.pattern.lanes.ohat",
        "tracks.drums.pattern.lanes.rim",
        "tracks.drums.pattern.swing",
    ]


def test_track_mixer_window_and_kind_change():
    assert sorted(
        paths(None, lambda d: track(d, "bass").update(gain_db=-3, pan=0.5, mute=True, at_bar=3))
    ) == [
        "tracks.bass.at_bar",
        "tracks.bass.gain_db",
        "tracks.bass.mute",
        "tracks.bass.pan",
    ]

    def to_clips(d):
        t = track(d, "bass")
        for k in ("instrument", "midi"):
            t.pop(k)
        t["clips"] = []

    p = paths(None, to_clips)
    assert "tracks.bass.instrument" in p and "tracks.bass.midi" in p and "tracks.bass.clips" in p


def test_master_chops_sources():
    assert sorted(
        paths(
            None,
            lambda d: (
                d["master"].update(gain_db=1, target_lufs=-12),
                d["sources"].append({"id": "src2", "file": "sources/b.wav"}),
                d["chops"].append({**d["chops"][0], "id": "c2"}),
            ),
        )
    ) == ["chops.c2", "master.gain_db", "master.target_lufs", "sources.src2"]
    assert paths(None, lambda d: d["chops"][0].update(length_beats=4, stem="bass")) == [
        "chops.c1.length_beats",
        "chops.c1.stem",
    ]


# --------------------------------------------------------------------------
# scale + randomized invariants
# --------------------------------------------------------------------------


def test_large_transposed_melody_is_found_quickly_as_modifications():
    n = 800

    def big(d, shift=0):
        track(d, "bass").pop("midi")
        track(d, "bass")["notes"] = [
            {"beat": 1 + i * 0.25, "pitch": 40 + (i % 12) + shift, "length_beats": 0.25}
            for i in range(n)
        ]
        d["meta"]["length_bars"] = 64

    start = time.perf_counter()
    e = diff(big, lambda d: big(d, 2))
    assert time.perf_counter() - start < 5
    assert len(e) == n and all(x.path.endswith(".pitch") for x in e)


def test_random_shuffles_and_insertions_never_create_phantoms():
    rng = random.Random(1234)
    for _ in range(25):
        n = rng.randint(2, 8)

        def make(d, n=n):
            track(d, "sample")["clips"] = [
                {"chop": "c1", "at_bar": i + 1, "repeat": 1, "offset_beats": (i % 3) * 0.5}
                for i in range(n)
            ]

        def shuffled(d, n=n):
            make(d, n)
            rng.shuffle(track(d, "sample")["clips"])

        assert paths(make, shuffled) == []

        pos = rng.randint(0, n)

        def inserted(d, n=n, pos=pos):
            make(d, n)
            track(d, "sample")["clips"].insert(pos, {"chop": "c1", "at_bar": 16})

        assert paths(make, inserted) == [f"tracks.sample.clips[{pos}]"]


def test_diff_does_not_mutate_its_inputs():
    a, b = make_song(), make_song(lambda d: d["meta"].update(bpm=99))
    before = (a.model_dump(), b.model_dump())
    song_diff(a, b)
    assert (a.model_dump(), b.model_dump()) == before
    assert song_dict()  # helper untouched
