"""Integration: analyze on real project layouts, the tools, compare,
summary, ears.toml and the song timeline."""

from __future__ import annotations

import json
import shutil
import struct
import time

import pytest

from muestra.ears import style
from muestra.ears.config import PROFILES, load_config
from muestra.ears.context import Timeline, read_midi_notes
from muestra.ears.core import analyze, compare, load_metrics, write_metrics
from muestra.ears.corpus import CorpusSpec, Defects, generate
from muestra.ears.report import MAX_ALARM_LINES
from muestra.errors import MuestraError
from muestra.project import open_project
from muestra.registry import REGISTRY, run_tool
from muestra.schemas.metrics import Metrics, StyleSimilarity
from muestra.schemas.render_meta import RenderMeta
from muestra.schemas.song import Song

from ..conftest import EXAMPLE_PROJECT


@pytest.fixture
def fixture_copy(tmp_path):
    dst = tmp_path / "proj"
    shutil.copytree(EXAMPLE_PROJECT, dst)
    return open_project(dst)


def test_analyze_fixture_project(fixture_copy):
    m = analyze(fixture_copy, "v001")
    Metrics.model_validate(m.model_dump(mode="json"))  # round-trips the contract
    assert m.version == "v001" and m.ears_version
    # the fixture render is a toy: a 1-bar render of a 220 Hz sine "sample"
    # and a silent (not muted) bass. Ears must say so instead of staying
    # quiet, and must SAY when it cannot check something (too few triggers).
    found = {a.code for a in m.alarms}
    assert "track_silent" in found
    assert any(s.startswith("sidechain de 'sample'") for s in m.skipped)
    assert m.loudness.lra_lu is None  # 2 s render: no 3 s short-term window
    for a in m.alarms:
        assert a.message and (a.severity == "info" or a.fix)


def test_summary_lists_skipped_checks_instead_of_claiming_full_coverage(fixture_copy):
    from muestra.ears.report import summarize

    m = analyze(fixture_copy, "v001")
    m.alarms = []
    m.skipped = ["sidechain: instrumento sin referencia seca"]
    text = summarize(m)
    assert "sin problemas detectados" not in text
    assert "Sin comprobar: sidechain: instrumento sin referencia seca" in text


def test_instrument_sidechain_skip_names_the_missing_dry_instrument_reference(tmp_path):
    from muestra.ears.context import load_context
    from muestra.ears.lowend import _measure_duck
    from muestra.schemas.song import SidechainFx

    generate(CorpusSpec(sr=24000, bars=4), tmp_path)
    ctx = load_context(open_project(tmp_path), "v001")
    fx = SidechainFx(type="sidechain", key_track="drums", key_lane="kick", depth=0.6)
    assert _measure_duck(ctx, "bass", fx) is None
    reason = next(s for s in ctx.skipped if "sidechain de 'bass'" in s)
    assert "instrumento" in reason and "referencia seca" in reason
    assert "stem fuente del chop" not in reason


def test_ears_analyze_tool_writes_metrics(fixture_copy):
    r = run_tool("ears.analyze", {"project": str(fixture_copy.root), "version": "v001"})
    assert r.ok, r.errors
    assert r.files == ["renders/v001/metrics.json"]
    on_disk = json.loads((fixture_copy.root / "renders/v001/metrics.json").read_text("utf-8"))
    assert on_disk["summary"] == r.data["summary"]
    assert r.data["metrics"] is None  # compact by default (WP12); detail=true for all
    full = run_tool(
        "ears.analyze", {"project": str(fixture_copy.root), "version": "v001", "detail": True}
    )
    assert full.data["metrics"]["summary"] == on_disk["summary"]
    assert load_metrics(fixture_copy, "v001").version == "v001"
    spec = REGISTRY["ears.analyze"]
    assert spec.long_running and spec.cli == ("ears", "analyze")


def test_ears_analyze_tool_errors_are_structured(fixture_copy):
    r = run_tool("ears.analyze", {"project": str(fixture_copy.root), "version": "v009"})
    assert not r.ok and r.errors[0].code == "file_not_found" and r.errors[0].hint
    (fixture_copy.root / "renders/v001/meta.json").write_text("{", encoding="utf-8")
    r = run_tool("ears.analyze", {"project": str(fixture_copy.root), "version": "v001"})
    assert not r.ok and r.errors[0].code == "parse_error"


def test_song_snapshot_falls_back_to_git(fixture_copy, tmp_path):
    import subprocess

    root = fixture_copy.root
    (root / "renders/v001/song.yaml").unlink()
    with pytest.raises(MuestraError) as e:
        analyze(fixture_copy, "v001")
    assert e.value.issues[0].code == "file_not_found"
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "song.yaml"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "v001"], check=True)
    subprocess.run([*git, "tag", "v001"], check=True)
    assert analyze(fixture_copy, "v001").version == "v001"


def test_summary_is_compact(tmp_path):
    root = tmp_path / "p"
    defects = Defects(click=True, collision=True, missing_sidechain=True, detune_cents=30)
    generate(CorpusSpec(sr=24000, bars=8, defects=defects), root)
    m = analyze(open_project(root), "v001")
    lines = m.summary.splitlines()
    assert len(lines) <= MAX_ALARM_LINES + 2
    assert len(m.summary) < 2000
    assert lines[0].startswith("v001:") and "LUFS" in lines[0]
    assert any(line.startswith("- PROBLEMA") for line in lines)


def test_compare_versions(tmp_path):
    root = tmp_path / "p"
    generate(CorpusSpec(sr=24000, bars=8, defects=Defects(missing_sidechain=True)), root)
    spec_b = CorpusSpec(sr=24000, bars=8, target_lufs=-8.0)
    generate(spec_b, root, version="v002")
    project = open_project(root)
    d = compare(project, "v001", "v002")
    assert d.loudness == "louder" and d.lufs_delta > 0.5  # limiter eats part of +2 LU
    assert any(a.code == "sidechain_missing" for a in d.alarms_fixed)
    # the mini-limiter of the corpus cannot reach -8 LUFS: compare must report it
    assert [a.code for a in d.alarms_introduced] == ["loudness_off_target"]
    assert d.notes
    r = run_tool("ears.compare", {"project": str(root), "version_a": "v001", "version_b": "v002"})
    assert r.ok and r.data["delta"]["loudness"] == "louder"
    assert r.files == []  # both metrics.json already existed


def test_ears_toml_overrides(tmp_path):
    root = tmp_path / "p"
    generate(CorpusSpec(sr=24000, bars=8), root)
    (root / "ears.toml").write_text(
        '[spectral]\nprofile = "pink"\ntolerance_db = {air = 3}\n\n'
        "[thresholds]\ndc_warn_db = -100\nclip_run_error = 3\n",
        encoding="utf-8",
    )
    cfg = load_config(root)
    assert cfg.reference.name == "pink" and cfg.reference.tolerance_db["air"] == 3
    assert cfg.thresholds.dc_warn_db == -100.0 and cfg.thresholds.clip_run_error == 3
    m = analyze(open_project(root), "v001")
    assert m.spectral.reference.name == "pink"
    # an absurdly strict DC threshold must now fire on the clean mix
    assert any(a.code == "dc_offset" for a in m.alarms)


@pytest.mark.parametrize(
    "text",
    [
        "[thresholds]\nnope = 1\n",
        '[spectral]\nprofile = "nope"\n',
        "[spectral]\nlevels_db = {sub = 1}\n",
        "[bogus]\nx = 1\n",
        "[spectral\n",
    ],
)
def test_ears_toml_errors(tmp_path, text):
    (tmp_path / "ears.toml").write_text(text, encoding="utf-8")
    with pytest.raises(MuestraError):
        load_config(tmp_path)


def test_custom_reference_profile(tmp_path):
    levels = dict(PROFILES["dance"].levels_db)
    (tmp_path / "ears.toml").write_text(
        '[spectral]\nname = "mine"\nlevels_db = {'
        + ", ".join(f"{k} = {v}" for k, v in levels.items())
        + "}\ndefault_tolerance_db = 4\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.reference.name == "mine" and set(cfg.reference.tolerance_db.values()) == {4.0}


def test_style_hook_is_optional_and_never_breaks(fixture_copy):
    assert analyze(fixture_copy, "v001").style_similarity is None

    class Good:
        model = "fake"

        def score(self, mix, sr, references):
            return StyleSimilarity(model="fake", references=[], mean_score=0.5)

    class Broken:
        model = "broken"

        def score(self, mix, sr, references):
            raise RuntimeError("no model")

    try:
        style.register(Good())
        assert analyze(fixture_copy, "v001").style_similarity.mean_score == 0.5
        style.register(Broken())
        m = analyze(fixture_copy, "v001")
        assert m.style_similarity is None and any("broken" in s for s in m.skipped)
    finally:
        style.unregister()


def _meta(range_bars=(1, 17), bpm=120.0):
    return RenderMeta.model_validate(
        {
            "version": "v001",
            "created_at": "2026-11-17T00:00:00Z",
            "agent": "t",
            "sample_rate": 48000,
            "bpm": bpm,
            "key": "F#m",
            "length_bars": 16,
            "range_bars": list(range_bars),
            "mix": "renders/v001/mix.wav",
        }
    )


def _song(**track):
    return Song.model_validate(
        {
            "meta": {"title": "t", "bpm": 120, "key": "F#m", "length_bars": 16},
            "tracks": [track],
        }
    )


def test_timeline_pattern_swing_window_and_range():
    song = _song(
        id="drums",
        at_bar=3,
        until_bar=5,
        pattern={
            "lanes": {"hat": {"sample": "a.wav", "steps": "xx.............."}},
            "swing": 0.6,
        },
    )
    tl = Timeline(song, _meta(range_bars=(2, 6)), EXAMPLE_PROJECT, 8 * 2.0)
    hits = tl.lane_hits("drums")["hat"]
    # bars 3 and 4 only; render starts at bar 2 (t=0 is bar 2 beat 1)
    assert [round(h.t, 4) for h in hits] == [2.0, 2.0 + 0.125 + 0.6 * 0.125 / 3, 4.0, 4.15]
    assert tl.where(2.0) == (3, 1.0)
    assert tl.where(0.75) == (2, 2.5)


def test_timeline_clip_instances_and_notes():
    song = Song.model_validate(
        {
            "meta": {"title": "t", "bpm": 120, "key": "F#m", "length_bars": 16},
            "sources": [{"id": "s", "file": "a.wav"}],
            "chops": [{"id": "c1", "source": "s", "start_downbeat": 1, "length_beats": 8}],
            "tracks": [
                {"id": "sample", "clips": [{"chop": "c1", "at_bar": 3, "repeat": 3}]},
                {
                    "id": "bass",
                    "instrument": {"plugin": "x"},
                    "notes": [{"beat": 5, "pitch": "F#1", "length_beats": 2}],
                },
            ],
        }
    )
    tl = Timeline(song, _meta(), EXAMPLE_PROJECT, 32.0)
    inst = tl.clip_instances("sample")
    assert [(c.start_s, c.end_s, c.repeat_index) for c in inst] == [
        (4.0, 8.0, 0),
        (8.0, 12.0, 1),
        (12.0, 16.0, 2),
    ]
    assert inst[0].ref == "tracks.sample.clips[0]"
    (note,) = tl.notes("bass")
    assert (note.t, note.pitch, note.dur_s) == (2.0, 30, 1.0)


def test_read_midi_notes(tmp_path):
    def vlq(n):
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        return bytes(out)

    track = vlq(0) + b"\xff\x51\x03" + (400000).to_bytes(3, "big")  # tempo: ignored (R4)
    track += vlq(0) + bytes([0x90, 42, 100]) + vlq(240) + bytes([0x80, 42, 0])
    track += vlq(240) + bytes([0x90, 45, 90]) + vlq(480) + bytes([45, 0])  # running status
    track += vlq(0) + b"\xff\x2f\x00"
    data = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
    data += b"MTrk" + struct.pack(">I", len(track)) + track
    p = tmp_path / "b.mid"
    p.write_bytes(data)
    notes = read_midi_notes(p)
    assert [(b, pitch, ln) for b, pitch, ln, _v in notes] == [(0.0, 42, 0.5), (1.0, 45, 1.0)]
    assert read_midi_notes(tmp_path / "missing.mid") == []


def test_performance_16_bars_four_tracks(tmp_path):
    """Spec: ~2 s for a 16-bar render with ~4 tracks on the dev laptop.

    Asserted loosely (CI and busy machines vary); the measured time is
    recorded in metrics.analysis_time_s and printed with -s.
    """
    root = tmp_path / "p"
    generate(CorpusSpec(sr=48000, bars=16), root)
    project = open_project(root)
    analyze(project, "v001")  # warm-up (imports, FFT plans)
    t0 = time.perf_counter()
    m, _path = write_metrics(project, "v001")
    elapsed = time.perf_counter() - t0
    print(f"\nears: 16 bars, 4 tracks + 3 lanes @ 48 kHz analysed in {elapsed:.2f} s")
    assert len(m.technical.tracks) == 4
    assert elapsed < 12.0


def test_a_failing_metric_is_reported_not_fatal(fixture_copy, monkeypatch):
    from muestra.ears import core

    def boom(ctx):
        raise KeyError("bug")

    monkeypatch.setattr(core, "measure_clicks", boom)
    m = analyze(fixture_copy, "v001")
    assert m.clicks.count == 0
    assert any(s.startswith("clicks: error interno (KeyError") for s in m.skipped)
