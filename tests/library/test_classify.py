"""Name parsing, verification against audio and descriptive tags."""

from __future__ import annotations

import pytest

from muestra.library.audio import probe
from muestra.library.classify import (
    classify,
    descriptor_tags,
    kit_dir,
    kit_name,
    parse_name,
    probe_needs,
)
from muestra.schemas.library import AudioFeatures

from .conftest import hat, kick, write


@pytest.mark.parametrize(
    ("rel", "type_", "subtype", "kind"),
    [
        ("kits/tr8-hardware-machines/roland-tr-8/kick-tr-8-kick-02.wav", "kick", None, "one-shot"),
        (
            "kits/tr8-hardware-machines/roland-tr-8/chat-tr-8-closed-hat-01.wav",
            "chat",
            None,
            "one-shot",
        ),
        (
            "kits/tr8-hardware-machines/roland-tr-8/ohat-tr-8-open-hat-01.wav",
            "ohat",
            None,
            "one-shot",
        ),
        (
            "kits/tr8-hardware-machines/roland-tr-8/crash-tr-8-crash-02.wav",
            "cym",
            "crash",
            "one-shot",
        ),
        ("kits/tr8-hardware-machines/roland-tr-8/clap-tr-8-clap-01.wav", "clap", None, "one-shot"),
        (
            "kits/tr8-hardware-machines/roland-tr-8/cowbell-tr-8-cowbell.wav",
            "perc",
            "cowbell",
            "one-shot",
        ),
        # 'misc' prefix: the keywords decide
        (
            "kits/acoustic-drums-1000/kit-2-acoustic-room/misc-cycdh-k2room-clhat-06.wav",
            "chat",
            None,
            "one-shot",
        ),
        (
            "kits/acoustic-drums-1000/kit-2-acoustic-room/misc-cycdh-k2room-ophat-01.wav",
            "ohat",
            None,
            "one-shot",
        ),
        (
            "kits/acoustic-drums-1000/kit-14-acoustic/misc-cycdh-k5-sdst03.wav",
            "snare",
            "sidestick",
            "one-shot",
        ),
        (
            "kits/goldbaby-tape-machines/dmx606-samples/misc-dmx606-bd1-l.wav",
            "kick",
            None,
            "one-shot",
        ),
        (
            "kits/goldbaby-tape-machines/tape606-samplepack/misc-606hho-04-tapesat.wav",
            "ohat",
            None,
            "one-shot",
        ),
        (
            "kits/legowelt-studio-drums/basedrums/misc-legowelt-basedrum-070.wav",
            "kick",
            None,
            "one-shot",
        ),
        (
            "kits/acoustic-drums-1000/kit-3-acoustic/misc-cycdh-k3-trash02.wav",
            "cym",
            "crash",
            "one-shot",
        ),
        # the 'hat' prefix is refined by its keywords
        ("kits/acoustic-drums-1000/acoustic/hat-khats-open-12.wav", "ohat", None, "one-shot"),
        ("kits/acoustic-drums-1000/acoustic/hat-khats-clsd-12.wav", "chat", None, "one-shot"),
        ("kits/acoustic-drums-1000/acoustic/hat-khats-pdl-02.wav", "hat", "pedal", "one-shot"),
        # loops, fills, phrases
        (
            "kits/french-house-loops/beats/kick-fhc-beata-120-01-kicksnare.wav",
            "drums",
            "kick+snare",
            "loop",
        ),
        ("kits/french-house-loops/beats/misc-fhc-beatb-128-01-hats.wav", "drums", "hats", "loop"),
        ("kits/french-house-loops/instruments/misc-fhc-pad-a-120-a.wav", "pad", None, "loop"),
        ("kits/french-house-loops/instruments/misc-fhc-munchbass-140-a.wav", "bass", None, "loop"),
        ("kits/percussion/conga/conga-loop01-140bpm.wav", "perc", "conga", "loop"),
        ("kits/drum-fills/120bpm/fill-if-acoufill-120-01.wav", "fill", None, "phrase"),
        ("kits/drum-fills/95bpm/snare-if-snareroll-95-03.wav", "fill", "roll", "phrase"),
        ("kits/hats-cymbals-gongs/accoustic/cym-cymbal-roll-01.wav", "cym", None, "one-shot"),
        (
            "kits/acoustic-drums-1000/ludwig-a/snare-cycdh-ludsnroffa-01.wav",
            "snare",
            "snares-off",
            "one-shot",
        ),
        (
            "kits/percussion/world-perc-loops/perc-wp-handdrum-a-110-02.wav",
            "perc",
            "hand-drum",
            "loop",
        ),
    ],
)
def test_parse_name_on_the_real_naming_scheme(rel, type_, subtype, kind):
    info = parse_name(rel)
    assert (info.type, info.subtype, info.kind) == (type_, subtype, kind)


def test_bpm_is_read_from_loop_names_only():
    assert parse_name("kits/x/beats/misc-fhc-beata-128-01.wav").bpms == [128.0]
    assert parse_name("kits/x/conga/conga-loop02-140bpm.wav").bpms == [140.0]
    # a number in a one-shot name is a take number, not a tempo
    assert parse_name("kits/legowelt/basedrums/misc-legowelt-basedrum-105.wav").bpms == []
    assert parse_name("kits/x/kick-tr-808-kick-120.wav").bpms == []


def test_kit_names_follow_the_kits_layout():
    assert kit_name("kits/tr808-musicradar/bass-drum-bd/kick-e808-bd-01.wav") == "tr808-musicradar"
    assert (
        kit_dir("kits/tr808-musicradar/bass-drum-bd/kick-e808-bd-01.wav") == "kits/tr808-musicradar"
    )
    assert kit_name("my-pack/kick.wav") == "my-pack"
    assert kit_name("kick.wav") is None


def test_probe_needs():
    assert probe_needs(parse_name("kits/x/kick-a-01.wav")) == (False, False)
    assert probe_needs(parse_name("kits/x/beats/misc-beat-120-01.wav")) == (True, False)
    assert probe_needs(parse_name("kits/x/instruments/misc-pad-a-120-a.wav")) == (True, True)


def test_type_mismatch_is_flagged_not_hidden(tmp_path):
    rel = "kits/pack/kick-liar-01.wav"
    p = probe(write(tmp_path / "liar.wav", hat(0.015, 0.2)))
    c = classify(rel, p)
    assert c.type == "kick"  # the name still decides ...
    assert any(f.startswith("type-mismatch:name=kick") for f in c.flags)  # ... but it is flagged


def test_honest_kick_has_no_mismatch_flag(tmp_path):
    p = probe(write(tmp_path / "k.wav", kick()))
    c = classify("kits/pack/kick-ok-01.wav", p)
    assert not [f for f in c.flags if f.startswith("type-")]


def test_unnamed_files_get_a_conservative_inferred_type(tmp_path):
    p = probe(write(tmp_path / "k.wav", kick()))
    c = classify("kits/pack/misc-zzz-001.wav", p)
    assert c.type == "kick" and "type-inferred:kick" in c.flags
    p2 = probe(write(tmp_path / "h.wav", hat(0.015, 0.15)))
    assert classify("kits/pack/misc-zzz-002.wav", p2).type == "chat"


def test_loop_bpm_agreement_and_disagreement(tmp_path):
    from .conftest import click_loop

    p = probe(write(tmp_path / "l.wav", click_loop(120.0, 4)), want_bpm=True)
    ok = classify("kits/p/loops/misc-beat-120-01.wav", p)
    assert ok.bpm == 120.0 and ok.bpm_source == "name+detected" and ok.bpm_confidence > 0.7
    # half-time naming still agrees
    half = classify("kits/p/loops/misc-beat-60-01.wav", p)
    assert half.bpm == 60.0 and half.bpm_source == "name+detected"
    bad = classify("kits/p/loops/misc-beat-100-01.wav", p)
    assert bad.bpm == 100.0 and bad.bpm_source == "name"
    assert any(f.startswith("bpm-disagreement:name=100,detected=120") for f in bad.flags)


def test_descriptor_thresholds():
    def tags(type_, **kw):
        base = {"sub": 0.85, "low": 0.1, "mid": 0.03, "high": 0.01, "air": 0.0, "tonality": 0.7}
        base.update(kw)
        return descriptor_tags(
            type_,
            AudioFeatures(**base),
            kind="one-shot",
            peak_dbfs=-1.0,
            clipping=False,
            channels=1,
            side_ratio=0.0,
            sample_rate=44100,
        )

    punchy = tags(
        "kick", attack_ms=1.5, decay_ms=120, spectral_centroid_hz=90, fundamental_hz=55, note="A1"
    )
    assert {"punchy", "short", "subby", "dark", "tonal", "tuned", "clean"} <= set(punchy)
    boomy = tags("kick", attack_ms=2, decay_ms=900, spectral_centroid_hz=80)
    assert {"boomy", "long", "subby"} <= set(boomy) and "punchy" not in boomy
    bright_hat = tags(
        "chat",
        sub=0.0,
        low=0.0,
        mid=0.02,
        high=0.1,
        air=0.88,
        tonality=0.05,
        attack_ms=1,
        decay_ms=60,
        spectral_centroid_hz=11000,
    )
    assert {"bright", "short", "noisy"} <= set(bright_hat) and "subby" not in bright_hat
    # brightness is judged per type: 1.2 kHz is bright for a kick, dark for a snare
    assert "bright" in tags("kick", spectral_centroid_hz=1200)
    assert "dark" in tags("snare", sub=0.0, low=0.3, mid=0.5, spectral_centroid_hz=1200)


def test_hot_quiet_clipped_tags():
    f = AudioFeatures(sub=0.5, low=0.4, mid=0.1, high=0.0, air=0.0)
    kw = {"kind": "one-shot", "channels": 1, "side_ratio": 0.0, "sample_rate": 44100}
    assert "hot" in descriptor_tags("kick", f, peak_dbfs=-0.1, clipping=False, **kw)
    assert "quiet" in descriptor_tags("kick", f, peak_dbfs=-20.0, clipping=False, **kw)
    t = descriptor_tags("kick", f, peak_dbfs=0.0, clipping=True, **kw)
    assert "clipped" in t and "clean" not in t
