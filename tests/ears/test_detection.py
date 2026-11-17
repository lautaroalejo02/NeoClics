"""Each metric detects its defect on the synthetic corpus and stays quiet
on the clean mix. These are the calibration claims of docs/ears.md."""

from __future__ import annotations

import pytest

from .conftest import codes


def test_clean_mix_raises_no_warnings(render):
    _p, m = render()
    assert codes(m) == set(), [(a.code, a.message) for a in m.alarms]
    assert m.skipped == []


def test_clean_mix_with_sustained_layer_is_click_free(render):
    _p, m = render(sustained=True)
    assert codes(m) == set(), [(a.code, a.message) for a in m.alarms]
    assert m.clicks.count == 0


@pytest.mark.parametrize(
    ("defect", "expected"),
    [
        ({"late_chop_ms": 20.0}, "grid_offset"),
        ({"late_chop_ms": -20.0}, "grid_offset"),
        ({"clip_late": (0, 30.0)}, "clip_offset"),
        ({"drift_ms": 30.0}, "grid_drift"),
        ({"click": True}, "click"),
        ({"collision": True}, "low_end_collision"),
        ({"sample_low": True}, "low_end_collision"),
        ({"detune_cents": 30.0}, "sample_detuned"),
        ({"source_detune_cents": -30.0}, "sample_detuned"),
        ({"transpose_semitones": 1}, "track_transposed"),
        ({"transpose_semitones": 1}, "bass_sample_clash"),
        ({"clip_master_db": 6.0}, "clipping"),
        ({"missing_sidechain": True}, "sidechain_missing"),
        ({"weak_sidechain": True}, "sidechain_weak"),
        ({"dark": True}, "spectral_high_mid_deficit"),
        ({"silent_track": True}, "track_silent"),
        ({"dc_offset": 0.01}, "dc_offset"),
        ({"low_phase": True}, "low_end_phase"),
        ({"kick_late_ms": 20.0}, "lane_offset"),
        ({"inaudible_pad": True}, "track_inaudible"),
        ({"over_compress": True}, "over_compressed"),
    ],
)
def test_defect_is_detected(render, defect, expected):
    _p, m = render(**defect)
    found = codes(m)
    assert expected in found, [(a.code, a.message) for a in m.alarms]
    alarm = next(a for a in m.alarms if a.code == expected)
    assert alarm.message and alarm.fix, "every warn/error alarm explains and suggests a fix"


@pytest.mark.parametrize(
    "defect",
    [
        {"sample_swing": 0.5},  # swung sample: a groove, not a timing error
        {"sample_triplets": True},  # genuinely off-16th material
        {"late_chop_ms": 8.0},  # below the timing threshold: must stay quiet
        {"detune_cents": 10.0},  # below the detune threshold
    ],
)
def test_no_false_alarm_on_legit_material(render, defect):
    _p, m = render(**defect)
    timing = {"grid_offset", "grid_drift", "clip_offset", "sample_detuned"}
    assert not (codes(m) & timing), [(a.code, a.message) for a in m.alarms]


def test_swing_is_reported_as_info_with_a_pattern_fix(render):
    _p, m = render(sample_swing=0.5)
    alarm = next(a for a in m.alarms if a.code == "swing_mismatch")
    assert alarm.severity == "info"
    assert "pattern.swing" in alarm.fix
    assert alarm.value == pytest.approx(0.5 * 125 / 3, abs=4)  # swing*step/3 at 120 BPM


def test_no_sidechain_is_a_genre_hint(render):
    _p, m = render(no_sidechain=True)
    assert codes(m) == set()
    alarm = next(a for a in m.alarms if a.code == "no_sidechain")
    assert alarm.severity == "info" and "sidechain" in alarm.fix


def test_late_chop_offset_and_fix_are_quantitative(render):
    _p, m = render(late_chop_ms=20.0)
    al = m.alignment.tracks["sample"]
    assert al.median_offset_ms == pytest.approx(20.0, abs=3.0)
    alarm = next(a for a in m.alarms if a.code == "grid_offset")
    # 20 ms at 120 BPM = 0.04 beats later in the source
    assert "chops.c1.start_offset_beats" in alarm.fix
    assert alarm.chop == "c1" and alarm.where and alarm.where[0].bar == 1


def test_clip_offset_names_the_clip(render):
    _p, m = render(clip_late=(0, 30.0))
    alarm = next(a for a in m.alarms if a.code == "clip_offset")
    assert alarm.clip == "tracks.sample.clips[0]"
    assert alarm.value == pytest.approx(30.0, abs=4.0)
    assert "offset_beats" in alarm.fix
    assert "grid_offset" not in codes(m)  # the other instance is fine


def test_collision_reports_bars_and_beats(render):
    _p, m = render(collision=True)
    alarm = next(a for a in m.alarms if a.code == "low_end_collision")
    assert alarm.track == "bass" and alarm.count >= 8
    assert all(w.beat in (1.0, 2.0, 3.0, 4.0) for w in alarm.where)
    assert "sidechain" in alarm.fix
    pair = next(p for p in m.low_end_masking.pairs if p.track == "bass")
    assert pair.collisions / pair.kick_hits > 0.5


def test_sidechain_measurement(render):
    _p, clean = render()
    _p, missing = render(missing_sidechain=True)
    _p, weak = render(weak_sidechain=True)
    sc = clean.low_end_masking.sidechain[0]
    assert sc.ducking and sc.measured_dip_db > 4.0  # depth 0.6 -> 8 dB nominal
    assert sc.recovery_ms is not None and 20 < sc.recovery_ms < 150
    assert missing.low_end_masking.sidechain[0].measured_dip_db < 1.5
    assert 1.5 < weak.low_end_masking.sidechain[0].measured_dip_db < 4.0


def test_detune_expected_vs_measured(render):
    _p, m = render(detune_cents=30.0)
    d = m.tonal.detune[0]
    assert d.expected_detune_cents == pytest.approx(30.0, abs=0.5)  # from snapshot+analysis
    assert d.relative_cents == pytest.approx(30.0, abs=5.0)  # measured on the render
    alarm = next(a for a in m.alarms if a.code == "sample_detuned")
    assert "pitch_semitones" in alarm.fix and "meta.bpm" in alarm.fix


def test_source_detune_is_measured_not_predicted(render):
    _p, m = render(source_detune_cents=-30.0)
    d = m.tonal.detune[0]
    assert d.expected_detune_cents == pytest.approx(0.0, abs=0.5)
    assert d.relative_cents == pytest.approx(-30.0, abs=5.0)
    alarm = next(a for a in m.alarms if a.code == "sample_detuned")
    assert "meta.bpm" not in alarm.fix  # a BPM change would not fix a detuned record


def test_click_maps_to_the_chop_and_suggests_fade(render):
    _p, m = render(click=True)
    edge = [e for e in m.clicks.events if e.edge in ("clip_start", "clip_end")]
    assert edge and edge[0].chop == "c1"
    alarm = next(a for a in m.alarms if a.code == "click")
    assert "chops.c1.fade_ms" in alarm.fix


def test_click_on_the_loop_seam_at_48k(render):
    _p, m = render(spec={"sr": 48000}, click=True)
    assert "loop_seam_click" in codes(m)


def test_tonal_estimate_on_clean_mix(render):
    _p, m = render()
    assert m.tonal.compatible is True
    assert m.tonal.estimated is not None
    assert m.tonal.tracks["bass"].best_shift_semitones == 0


def test_clean_at_48k_is_also_quiet(render):
    _p, m = render(spec={"sr": 48000})
    assert codes(m) == set(), [(a.code, a.message) for a in m.alarms]
