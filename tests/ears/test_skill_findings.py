"""Regressions for the ears findings of the WP9 skill run (docs/skill-findings.md 1-3)."""

from __future__ import annotations

from muestra.ears.core import analyze
from muestra.ears.corpus import CorpusSpec, Defects, generate
from muestra.project import open_project

FAST = {"sr": 24000, "bars": 8}


def _corpus(root, *, edit_song=None, ears_toml: str | None = None, **defects):
    generate(CorpusSpec(defects=Defects(**defects), **FAST), root)
    snapshot = root / "renders/v001/song.yaml"
    if edit_song:
        snapshot.write_text(edit_song(snapshot.read_text("utf-8")), "utf-8")
    if ears_toml:
        (root / "ears.toml").write_text(ears_toml, "utf-8")
    return analyze(open_project(root), "v001")


def test_loudness_off_target_never_suggests_master_gain_db(tmp_path):
    """Finding 1: with target_lufs the renderer normalises after master.gain_db."""

    def louder_target(text: str) -> str:
        assert "target_lufs: -10" in text
        return text.replace("target_lufs: -10", "target_lufs: -5")

    m = _corpus(tmp_path, edit_song=louder_target)
    alarm = next(a for a in m.alarms if a.code == "loudness_off_target")
    assert "`master.gain_db` no sirve" in alarm.fix or "master.gain_db" not in alarm.fix
    assert "revisá `master.gain_db`" not in alarm.fix
    assert "target_lufs" in alarm.fix


def test_measured_tuning_beats_the_a440_prediction(tmp_path):
    """Finding 2: a record off A440, compensated by the resample, is in tune.

    tuning_min_strength = 0.99 forces the weak-measurement path that real old
    records hit (strength 0.05-0.08 in the skill run)."""
    m = _corpus(
        tmp_path,
        ears_toml="[thresholds]\ntuning_min_strength = 0.99\n",
        detune_cents=30.0,
        source_detune_cents=-30.0,
    )
    d = m.tonal.detune[0]
    assert abs(d.expected_detune_cents - 30.0) < 1.0  # the prediction says +30
    assert d.relative_cents is not None and abs(d.relative_cents) < 10.0  # rendered in tune
    assert "sample_detuned" not in {a.code for a in m.alarms}


def test_weak_measurement_sets_the_fix_when_really_detuned(tmp_path):
    m = _corpus(tmp_path, ears_toml="[thresholds]\ntuning_min_strength = 0.99\n", detune_cents=30.0)
    alarm = next(a for a in m.alarms if a.code == "sample_detuned")
    assert alarm.severity == "warn" and "señal débil" in alarm.message
    assert abs(alarm.value - 30.0) < 6.0
    assert "pitch_semitones: -0.3" in alarm.fix  # ps 0 - measured/100, not a re-detune


def test_a_fifth_from_the_key_is_not_a_transposition_warning(tmp_path):
    """Finding 3: everything in F#m while meta.key says Bm (F# is B's fifth)."""

    def dominant_key(text: str) -> str:
        assert "key: F#m" in text
        return text.replace("key: F#m", "key: Bm")

    m = _corpus(tmp_path, edit_song=dominant_key)
    warned = {a.code for a in m.alarms if a.severity != "info"}
    assert "track_transposed" not in warned
    softened = [a for a in m.alarms if a.code == "track_transposed"]
    assert all("a propósito" in a.message for a in softened)
