"""The Spanish, producer-phrased description of a diff (pure, no git)."""

from __future__ import annotations

import re

import pytest

from muestra.versions.describe import _Describer, describe_song, musical_summary
from muestra.versions.diff import song_diff

from .helpers import make_song, track


def say(mut_a=None, mut_b=None):
    return describe_song(make_song(mut_a), make_song(mut_b))


def pitch0(d):
    d["chops"][0]["warp"]["pitch_semitones"] = 0


def lp_removed(d):
    t = track(d, "sample")
    t["fx"] = [f for f in t["fx"] if f.get("id") != "lp"]
    d["automation"].clear()


def four_bar_drums(d, kick_bar4=None):
    p = track(d, "drums")["pattern"]
    p["bars"] = 4
    for lane in p["lanes"].values():
        lane["steps"] = lane["steps"] * 4
    if kick_bar4:
        k = p["lanes"]["kick"]
        k["steps"] = k["steps"][:48] + kick_bar4


# --------------------------------------------------------------------------
# the phrases from the brief
# --------------------------------------------------------------------------


def test_semitone_up():
    assert say(pitch0, lambda d: (pitch0(d), d["chops"][0]["warp"].update(pitch_semitones=1))) == [
        "Subí el chop c1 un semitono"
    ]


def test_automated_lowpass_added_is_one_sentence():
    assert say(lp_removed, None) == [
        "Agregué un filtro pasa-bajos automatizado de 800 Hz a 18 kHz "
        "en los compases 1 a 4 del track sample"
    ]


def test_kick_pattern_changes_described_rhythmically():
    got = say(
        four_bar_drums,
        lambda d: four_bar_drums(d, kick_bar4="x..x..x...x.x..."),
    )
    assert got == ["El bombo pasa de negras a un patrón con síncopa en el compás 4"]


def test_kick_pattern_every_bar_has_no_bar_qualifier():
    def fill(d):
        track(d, "drums")["pattern"]["lanes"]["kick"]["steps"] = "x.x.x.x.x.x.x.x."

    assert say(None, fill) == ["El bombo pasa de negras a corcheas"]


def test_bass_down_2db():
    assert say(None, lambda d: track(d, "bass").update(gain_db=-2)) == ["Bajé el bajo 2 dB"]


def test_bass_preset_change():
    assert say(
        None, lambda d: track(d, "bass")["instrument"].update(preset="presets/slap-bass-02.fxp")
    ) == ["Cambié el preset del bajo de slap-bass-01 a slap-bass-02"]


def test_sidechain_deeper():
    (s,) = say(None, lambda d: track(d, "sample")["fx"][2].update(depth=0.8))
    assert s.startswith("Sidechain más profundo (0.6 a 0.8)")


# --------------------------------------------------------------------------
# grouping
# --------------------------------------------------------------------------


def test_new_track_with_everything_is_one_sentence():
    def lead(d):
        d["tracks"].insert(
            0,
            {
                "id": "lead",
                "instrument": {"plugin": "surge-xt", "preset": "presets/lead.fxp"},
                "notes": [
                    {"beat": 1 + i * 0.5, "pitch": 60 + i % 5, "length_beats": 0.5}
                    for i in range(40)
                ],
                "fx": [
                    {"id": "lp3", "type": "lowpass", "cutoff_hz": 4000},
                    {"type": "reverb"},
                    {"type": "delay", "time_beats": 0.75},
                ],
            },
        )
        d["automation"].append(
            {"target": "tracks.lead.fx.lp3.cutoff_hz", "points": [[1, 500], [5, 4000]]}
        )

    got = say(None, lead)
    assert len(got) == 1
    assert got[0].startswith("Agregué el lead (instrumento surge-xt, preset lead, 40 notas)")
    assert "1 automatización" in got[0]


def test_removed_track_absorbs_its_automation():
    def gone(d):
        d["tracks"].remove(track(d, "sample"))
        d["automation"].clear()

    assert say(None, gone) == ["Saqué el track sample"]


def test_removed_fx_mentions_its_automation():
    assert say(None, lp_removed) == [
        "Saqué el filtro pasa-bajos del track sample (y su automatización)"
    ]


def test_automation_edit_only():
    (s,) = say(None, lambda d: d["automation"][0].update(points=[[1, 800], [9, 18000]]))
    assert s == (
        "Rehice la automatización del corte del filtro pasa-bajos del track sample: "
        "ahora va de 800 Hz a 18 kHz en los compases 1 a 8"
    )


def test_automation_on_track_volume():
    def fade(d):
        d["automation"].append({"target": "tracks.bass.gain_db", "points": [[1, -30], [3, 0]]})

    assert say(None, fade) == [
        "Automaticé el volumen del bajo de -30 dB a 0 dB en los compases 1 y 2"
    ]


# --------------------------------------------------------------------------
# musical vocabulary
# --------------------------------------------------------------------------


def test_clip_sentences():
    def base(d):
        track(d, "sample")["clips"] = [{"chop": "c1", "at_bar": 1, "repeat": 2}]

    def added(d):
        base(d)
        track(d, "sample")["clips"].append({"chop": "c1", "at_bar": 9, "repeat": 2})

    def moved(d):
        track(d, "sample")["clips"] = [{"chop": "c1", "at_bar": 5, "repeat": 2}]

    assert say(base, added) == ["Agregué un clip del chop c1 en el compás 9 (×2) del track sample"]
    assert say(base, moved) == ["Moví el clip del chop c1 del compás 1 al 5 del track sample"]


def test_notes_transposed_added_removed():
    mel = [
        {"beat": 1 + i, "pitch": p, "length_beats": 1} for i, p in enumerate("C3 E3 G3 B3".split())
    ]

    def base(d):
        track(d, "bass").pop("midi")
        track(d, "bass")["notes"] = [dict(n) for n in mel]

    def up(d):
        base(d)
        for n in track(d, "bass")["notes"]:
            n["pitch"] = {"C3": "D3", "E3": "F#3", "G3": "A3", "B3": "C#4"}[n["pitch"]]

    def more(d):
        base(d)
        track(d, "bass")["notes"] += [
            {"beat": 9, "pitch": "C3", "length_beats": 1},
            {"beat": 10, "pitch": "G3", "length_beats": 1},
        ]

    def fewer(d):
        base(d)
        track(d, "bass")["notes"] = track(d, "bass")["notes"][:2]

    assert say(base, up) == ["Subí el bajo 2 semitonos"]
    (added,) = say(base, more)
    assert added == "Agregué 2 notas al bajo en el compás 3"
    (removed,) = say(base, fewer)
    assert removed == "Saqué 2 notas del bajo del compás 1"


def test_delay_time_is_in_note_values():
    def delay(d, beats):
        track(d, "drums")["fx"].append({"id": "dl", "type": "delay", "time_beats": beats})

    assert say(lambda d: delay(d, 1), lambda d: delay(d, 0.5)) == [
        "El delay de la batería ahora es de corcheas (antes negras)"
    ]


def test_filters_open_and_close():
    (s,) = say(None, lambda d: track(d, "sample")["fx"][1].update(cutoff_hz=4000))
    assert s == "Cerré el filtro pasa-bajos del track sample de 18 kHz a 4 kHz"
    (s,) = say(None, lambda d: track(d, "sample")["fx"][0].update(cutoff_hz=300))
    assert s == "Subí el corte del filtro pasa-altos del track sample de 150 Hz a 300 Hz"


def test_mixer_and_master_sentences():
    assert say(None, lambda d: track(d, "bass").update(mute=True)) == ["Muteé el bajo"]
    assert say(None, lambda d: track(d, "bass").update(pan=-0.4)) == [
        "Paneé el bajo a la izquierda (0 a -0.4)"
    ]
    assert say(None, lambda d: track(d, "drums").update(at_bar=5)) == [
        "La batería ahora entra en el compás 5 (antes en el 1)"
    ]
    assert say(None, lambda d: d["master"].update(gain_db=1.5)) == ["Subí el master 1.5 dB"]
    assert say(None, lambda d: d["master"].update(target_lufs=-8)) == [
        "El loudness objetivo del master ahora es -8 LUFS (antes -10)"
    ]
    assert say(None, lambda d: d["meta"].update(bpm=128)) == ["Subí el tempo de 120 a 128 BPM"]


def test_chop_sentences():
    assert say(None, lambda d: d["chops"][0].update(length_beats=4)) == [
        "Acorté el chop c1 de 8 a 4 beats"
    ]
    assert say(None, lambda d: d["chops"][0]["warp"].update(mode="stretch")) == [
        "Pasé el chop c1 a stretch (conserva el tono)"
    ]
    assert say(None, lambda d: d["chops"][0].update(start_offset_beats=0.5)) == [
        "Atrasé el inicio del chop c1 medio beat"
    ]


def test_pattern_sentences():
    assert say(None, lambda d: track(d, "drums")["pattern"].update(swing=0.3)) == [
        "Le puse swing 0.3 a la batería"
    ]

    def drop_hat(d):
        del track(d, "drums")["pattern"]["lanes"]["ohat"]

    assert say(None, drop_hat) == ["Saqué el hi-hat abierto de la batería"]

    def softer(d):
        track(d, "drums")["pattern"]["lanes"]["kick"]["steps"] = "x...x...x...3..."

    assert say(None, softer) == ["En el bombo golpes más suaves en el tiempo 4"]

    def ghost(d):
        track(d, "drums")["pattern"]["lanes"]["clap"]["steps"] = "....x..o....x..."

    assert say(None, ghost) == ["El clap pasa de los tiempos 2 y 4 a un patrón con síncopa"]


def test_no_change_has_no_sentences_and_summary_says_so():
    assert say() == []
    assert musical_summary([]) == "Sin cambios musicales."


def test_summary_is_one_paragraph_with_the_metrics_delta():
    s = musical_summary(
        ["Subí el bajo 2 dB", "Bajé el master 1 dB"], ["el mix quedó 1.2 LU más fuerte"]
    )
    assert s == "Subí el bajo 2 dB. Bajé el master 1 dB. El mix quedó 1.2 LU más fuerte."


# --------------------------------------------------------------------------
# coverage: every family gets a readable sentence, nothing leaks field names
# --------------------------------------------------------------------------

FX_CASES = [
    ("highpass", {"cutoff_hz": 150}, {"cutoff_hz": 300, "order": 8}),
    ("lowpass", {"cutoff_hz": 5000}, {"cutoff_hz": 2500, "order": 2}),
    (
        "sidechain",
        {"key_track": "drums", "depth": 0.5},
        {"depth": 0.2, "attack_ms": 5, "release_ms": 300, "key_lane": "kick"},
    ),
    (
        "compressor",
        {"threshold_db": -18, "ratio": 4},
        {
            "threshold_db": -24,
            "ratio": 2,
            "attack_ms": 30,
            "release_ms": 50,
            "knee_db": 12,
            "makeup_gain_db": 3,
        },
    ),
    ("limiter", {}, {"ceiling_db": -3, "release_ms": 100}),
    ("gain", {"gain_db": 0}, {"gain_db": 3}),
    (
        "reverb",
        {},
        {"room_size": 0.9, "damping": 0.2, "wet_level": 0.6, "dry_level": 0.2, "width": 0.4},
    ),
    ("delay", {"time_beats": 1}, {"time_beats": 0.75, "feedback": 0.6, "mix": 0.5}),
    (
        "chorus",
        {},
        {"rate_hz": 2, "depth": 0.9, "centre_delay_ms": 12, "feedback": 0.3, "mix": 0.8},
    ),
    (
        "phaser",
        {},
        {"rate_hz": 0.3, "depth": 0.1, "centre_frequency_hz": 900, "feedback": 0.5, "mix": 0.2},
    ),
    ("saturation", {"drive_db": 3}, {"drive_db": 12}),
    (
        "plugin",
        {"plugin": "p", "preset": "a.fxp", "params": {"x": 0.1}},
        {"preset": "b.fxp", "params": {"x": 0.7, "y": 0.2}},
    ),
]


@pytest.mark.parametrize(("ftype", "base", "changes"), FX_CASES, ids=[c[0] for c in FX_CASES])
def test_every_fx_param_gets_a_sentence(ftype, base, changes):
    def with_fx(d, extra=None):
        fx = {"id": "x1", "type": ftype, **base, **(extra or {})}
        track(d, "bass")["fx"] = [fx]

    sentences = say(with_fx, lambda d: with_fx(d, changes))
    assert sentences, f"{ftype}: change was not described"
    for s in sentences:
        assert not re.search(r"\b[a-z]+_[a-z_]+\b", s), f"field name leaked: {s}"
        assert "None" not in s and "{" not in s


def test_every_fx_type_when_added_and_removed():
    for ftype, base, _ in FX_CASES:

        def with_fx(d, ftype=ftype, base=base):
            track(d, "bass")["fx"] = [{"id": "x1", "type": ftype, **base}]

        added = say(None, with_fx)
        removed = say(with_fx, lambda d: track(d, "bass").update(fx=[]))
        assert len(added) == 1 and added[0].startswith("Agregué"), (ftype, added)
        assert len(removed) == 1 and removed[0].startswith("Saqué"), (ftype, removed)


def test_changing_an_fx_type_in_place_and_reordering():
    def lp(d):
        track(d, "bass")["fx"] = [{"id": "f", "type": "lowpass", "cutoff_hz": 1000}]

    def hp(d):
        track(d, "bass")["fx"] = [{"id": "f", "type": "highpass", "cutoff_hz": 1000}]

    (s,) = say(lp, hp)
    assert "por un filtro pasa-altos" in s


FAMILIES = [
    ("title", lambda d: d["meta"].update(title="otro")),
    ("key", lambda d: d["meta"].update(key="Am")),
    ("length", lambda d: d["meta"].update(length_bars=32)),
    ("seed", lambda d: d["meta"].update(seed=7)),
    ("source", lambda d: d["sources"][0].update(file="sources/otra.wav")),
    ("source+", lambda d: d["sources"].append({"id": "src2", "file": "sources/b.wav"})),
    ("chop stem", lambda d: d["chops"][0].update(stem="bass")),
    ("chop downbeat", lambda d: d["chops"][0].update(start_downbeat=2)),
    ("chop fade", lambda d: d["chops"][0].update(fade_ms=20)),
    (
        "chop source",
        lambda d: (
            d["sources"].append({"id": "s2", "file": "x.wav"}),
            d["chops"][0].update(source="s2"),
        ),
    ),
    ("chop+", lambda d: d["chops"].append({**d["chops"][0], "id": "c2"})),
    ("chop-", lambda d: d["chops"].clear() or track(d, "sample").update(clips=[])),
    ("bass pan", lambda d: track(d, "bass").update(pan=0.3)),
    ("until", lambda d: track(d, "drums").update(until_bar=9)),
    ("track order", lambda d: d["tracks"].reverse()),
    ("bass midi", lambda d: track(d, "bass").update(midi="midi/other.mid")),
    ("bass plugin", lambda d: track(d, "bass")["instrument"].update(plugin="sfizz")),
    ("inst params", lambda d: track(d, "bass")["instrument"].update(params={"cutoff": 0.3})),
    (
        "lane sample",
        lambda d: track(d, "drums")["pattern"]["lanes"]["kick"].update(
            sample="library://kits/disco-01/kick2.wav"
        ),
    ),
    ("lane pan", lambda d: track(d, "drums")["pattern"]["lanes"]["kick"].update(pan=0.2)),
    ("lane gain", lambda d: track(d, "drums")["pattern"]["lanes"]["kick"].update(gain_db=-4)),
    (
        "lane steps vel",
        lambda d: track(d, "drums")["pattern"]["lanes"]["kick"].update(steps="5...x...x...x..."),
    ),
    (
        "lane steps notation",
        lambda d: track(d, "drums")["pattern"]["lanes"]["kick"].update(steps="9...x...x...x..."),
    ),
    (
        "steps_per_bar",
        lambda d: (
            track(d, "drums")["pattern"].update(steps_per_bar=8)
            or [
                ln.update(steps=ln["steps"][:8])
                for ln in track(d, "drums")["pattern"]["lanes"].values()
            ]
        ),
    ),
    ("master fx", lambda d: d["master"]["fx"][0].update(threshold_db=-6)),
    ("master fx+", lambda d: d["master"]["fx"].append({"id": "g", "type": "gain", "gain_db": 1})),
    (
        "master auto",
        lambda d: d["automation"].append({"target": "master.gain_db", "points": [[1, -6], [3, 0]]}),
    ),
    (
        "track pan auto",
        lambda d: d["automation"].append(
            {"target": "tracks.bass.pan", "points": [[1, -1], [3, 1]]}
        ),
    ),
    ("curve", lambda d: d["automation"][0].update(curve="step")),
]


@pytest.mark.parametrize(("name", "mut"), FAMILIES, ids=[f[0] for f in FAMILIES])
def test_every_field_family_gets_a_readable_sentence(name, mut):
    a, b = make_song(), make_song(mut)
    assert song_diff(a, b), name
    sentences = describe_song(a, b)
    assert sentences, f"{name}: a real change produced no sentence"
    for s in sentences:
        assert s == s.strip() and s[0].isupper(), s
        assert "None" not in s and "{" not in s and "nada" not in s.split(), s


def test_unanticipated_fields_get_a_generic_readable_phrase():
    d = _Describer(make_song(), make_song())
    out = d._chop_change("c1", {"id": "c1", "zzz": 1}, {"id": "c1", "zzz": 2})
    assert out == ["Cambié zzz del chop c1 de 1 a 2"]
    out = d._fx_change(
        None,
        "el master",
        {"type": "limiter", "id": "l", "q": 1},
        {"type": "limiter", "id": "l", "q": 5},
        [],
    )
    assert out and "q" in out[0] and "de 1 a 5" in out[0]
    assert d._meta() == []


def test_sentences_are_deduplicated_and_do_not_end_with_a_period():
    got = say(None, lambda d: (track(d, "bass").update(gain_db=-2), d["master"].update(gain_db=-1)))
    assert got == ["Bajé el bajo 2 dB", "Bajé el master 1 dB"]
    assert not any(s.endswith(".") for s in got)
