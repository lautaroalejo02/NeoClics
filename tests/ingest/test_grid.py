"""WP12: beat-grid correction at the analysis level (source.regrid / preview_grid)."""

import numpy as np
import pytest
import soundfile as sf

from muestra.errors import MuestraError
from muestra.ingest import grid as g
from muestra.ingest.analysis import analyze
from muestra.ingest.regrid import preview_grid, regrid
from muestra.project import chop_times, edit_song_document, load_analysis, load_song
from muestra.registry import run_tool
from muestra.schemas.analysis import RawGrid


def _raw(n=129, period=0.24, downbeat_every=4, first=0):
    beats = 0.1 + period * np.arange(n)
    return RawGrid(
        bpm_global=60 / period,
        beats_s=beats.tolist(),
        downbeats_s=beats[first::downbeat_every].tolist(),
    )


def test_halving_keeps_every_second_beat_and_rebuilds_four_beat_bars():
    raw = _raw()
    beats, downbeats = g.derive_grid(raw, tempo_factor=0.5)
    assert np.allclose(beats, np.asarray(raw.beats_s)[::2])
    assert g.grid_bpm(beats) == pytest.approx(125, abs=0.01)
    idx = [int(np.argmin(abs(beats - d))) for d in downbeats]
    assert set(np.diff(idx)) == {4}
    # the first tracked downbeat stays a downbeat; the phase alternative is +2
    assert downbeats[0] == raw.downbeats_s[0]
    _, other = g.derive_grid(raw, tempo_factor=0.5, downbeat_shift_beats=2)
    assert other[0] == pytest.approx(raw.downbeats_s[1])


def test_doubling_interpolates_and_identity_is_exact():
    raw = _raw(period=1.0, n=33)
    beats, downbeats = g.derive_grid(raw, tempo_factor=2.0)
    assert len(beats) == 2 * 33 - 1
    assert beats[1] == pytest.approx(0.6)
    assert g.grid_bpm(beats) == pytest.approx(120)
    assert np.diff(downbeats).max() == pytest.approx(2.0)  # tracked bar = two bars now
    same_b, same_d = g.derive_grid(raw)
    assert same_b.tolist() == raw.beats_s and same_d.tolist() == raw.downbeats_s


def test_shift_moves_the_one_keeping_tracker_irregularities():
    beats = 0.1 + 0.5 * np.arange(40)
    # a 3-beat bar in the middle (tracker dropped a beat there)
    idx = [0, 4, 8, 11, 15, 19, 23, 27, 31, 35]
    raw = RawGrid(bpm_global=120, beats_s=beats.tolist(), downbeats_s=beats[idx].tolist())
    _, downbeats = g.derive_grid(raw, downbeat_shift_beats=1)
    got = [int(np.argmin(abs(beats - d))) for d in downbeats]
    assert got == [i + 1 for i in idx]


def test_piecewise_phase_follows_two_consistent_downbeats_only():
    beats = 0.1 + 0.25 * np.arange(80)
    # 2/4 reading (downbeat every 2 beats) -> regroup in 4; one stray downbeat ignored
    idx = list(range(0, 40, 2)) + [41] + list(range(44, 80, 2))
    raw = RawGrid(bpm_global=240, beats_s=beats.tolist(), downbeats_s=beats[idx].tolist())
    downbeats = beats[g.derive_downbeat_indices(beats, np.asarray(raw.downbeats_s))]
    got = [int(np.argmin(abs(beats - d))) for d in downbeats]
    assert all(i % 4 == 0 for i in got)


def test_choose_factor_and_confidence():
    assert g.choose_factor(250, (70, 160)) == 0.5
    assert g.choose_factor(62, (70, 160)) == 2.0
    assert g.choose_factor(125, (70, 160)) == 1.0
    assert g.choose_factor(400 - 1, (70, 160)) == 1.0  # nothing fits: leave it
    halved = g.assess(
        _raw(),
        origin="auto",
        tempo_factor=0.5,
        beat_phase=0,
        downbeat_shift_beats=0,
        beats_per_bar=4,
        tempo_range=(70, 160),
        meter_consistency=0.99,
    )
    assert halved.ask_human and halved.bar_phase_confidence == 0.5
    assert "corcheas" in halved.reason
    assert any(a.downbeat_shift_beats == 2 and a.tempo_factor == 0.5 for a in halved.alternatives)
    assert any(a.tempo_factor == 1.0 for a in halved.alternatives)
    ambiguous = g.assess(
        _raw(period=0.8),  # 75 BPM: 150 also fits
        origin="auto",
        tempo_factor=1.0,
        beat_phase=0,
        downbeat_shift_beats=0,
        beats_per_bar=4,
        tempo_range=(70, 160),
        meter_consistency=1.0,
    )
    assert ambiguous.tempo_confidence == 0.5 and ambiguous.ask_human


def _double_time_tracker(beats):
    eighths = np.sort(np.concatenate([beats, (beats[:-1] + beats[1:]) / 2]))
    return lambda audio, sr: (eighths, eighths[::4])


def test_analyze_auto_halves_out_of_range_tracker_and_keeps_raw(rhythmic_project, monkeypatch):
    project, source_id, fixture = rhythmic_project
    monkeypatch.setattr("muestra.ingest.analysis.integrated_loudness", lambda a, sr: -18.0)
    project.path(f"cache/analysis/{source_id}.json").unlink()
    beats = np.asarray(fixture.beats_s)
    result = analyze(project, source_id, tracker=_double_time_tracker(beats))
    assert result.grid.raw.bpm_global == pytest.approx(240)
    assert result.bpm_global == pytest.approx(120)
    assert result.grid.origin == "auto" and result.grid.tempo_factor == 0.5
    assert np.allclose(result.beats_s, beats)
    assert load_analysis(project, source_id) == result
    # a narrower/odd range re-derives an AUTO correction on cache hit
    again = analyze(project, source_id, tempo_range=(180, 300))
    assert again.grid.tempo_factor == 1.0 and again.bpm_global == pytest.approx(240)


def test_legacy_analysis_with_song_chops_is_reported_not_moved(rhythmic_project):
    from muestra.ingest.analysis import _grid_on_cache_hit

    project, source_id, beats = _eighth_project(rhythmic_project)
    legacy = load_analysis(project, source_id)
    assert legacy.grid is None
    upgraded = _grid_on_cache_hit(project, source_id, legacy, None)
    assert upgraded.grid.origin == "auto" and upgraded.bpm_global == pytest.approx(120)
    _song_with_chop(project, source_id)
    kept = _grid_on_cache_hit(project, source_id, legacy, None)
    assert kept.beats_s == legacy.beats_s and kept.grid.origin == "tracker"
    assert kept.grid.ask_human and "regrid tempo_factor=0.5" in kept.grid.reason


def _song_with_chop(project, source_id, start=3, length=8, offset=None):
    def mutate(doc):
        chop = {
            "id": "c1",
            "source": source_id,
            "stem": "mix",
            "start_downbeat": start,
            "length_beats": length,
        }
        if offset is not None:
            chop["start_offset_beats"] = offset
        doc["chops"] = [chop]
        doc["tracks"] = [{"id": "sample", "clips": [{"chop": "c1", "at_bar": 1}]}]

    edit_song_document(project, mutate)


def _eighth_project(rhythmic_project):
    project, source_id, fixture = rhythmic_project
    beats = np.asarray(fixture.beats_s)
    eighths = np.sort(np.concatenate([beats, (beats[:-1] + beats[1:]) / 2]))
    from muestra.project import save_analysis

    save_analysis(
        project,
        fixture.model_copy(
            update={
                "beats_s": eighths.tolist(),
                "downbeats_s": eighths[::4].tolist(),
                "bpm_global": 240,
            }
        ),
    )
    return project, source_id, beats


def test_regrid_halves_keeps_same_audio_and_reports(rhythmic_project):
    project, source_id, beats = _eighth_project(rhythmic_project)
    _song_with_chop(project, source_id, start=3, length=8)
    before = chop_times(load_analysis(project, source_id), 3, 8)
    (project.path(f"cache/chops/{source_id}")).mkdir(parents=True, exist_ok=True)
    out = regrid(project, source_id, tempo_factor=0.5)
    after = load_analysis(project, source_id)
    assert after.bpm_global == pytest.approx(120)
    assert after.grid.raw.bpm_global == pytest.approx(240)
    chop = load_song(project).chops[0]
    assert (chop.start_downbeat, chop.length_beats) == (2, 4)
    assert chop_times(after, chop.start_downbeat, chop.length_beats) == pytest.approx(before)
    change = out.chops[0]
    assert (
        change.updated
        and change.same_audio
        and change.rate_after == pytest.approx(2 * change.rate_before)
    )
    assert "mismo audio" in change.note and "x2.00" in change.note
    assert out.suggested_bpm == pytest.approx(load_song(project).meta.bpm / 2)
    # idempotent: the same absolute call changes nothing
    again = regrid(project, source_id, tempo_factor=0.5)
    assert not again.chops[0].updated and "song.yaml" not in again.files
    # reversible
    regrid(project, source_id, reset=True)
    assert load_analysis(project, source_id).bpm_global == pytest.approx(240)
    chop = load_song(project).chops[0]
    assert (chop.start_downbeat, chop.length_beats) == (3, 8)


def test_regrid_shift_reports_offset_and_update_song_false(rhythmic_project):
    project, source_id, _ = rhythmic_project
    _song_with_chop(project, source_id, start=2, length=4)
    out = regrid(project, source_id, downbeat_shift_beats=1, update_song=False)
    change = out.chops[0]
    assert not change.updated and not change.same_audio
    assert change.moved_ms == pytest.approx(500, abs=1)
    assert "cambió lo que suena" in change.note
    assert load_song(project).chops[0].start_downbeat == 2
    out = regrid(project, source_id, downbeat_shift_beats=-1)
    chop = load_song(project).chops[0]
    assert out.chops[0].same_audio and chop.start_offset_beats != 0


def test_regrid_retires_stale_candidate_sets(rhythmic_project):
    from muestra.ingest.chops import find, set_paths
    from muestra.schemas.chops import ChopQuery

    project, source_id, _ = rhythmic_project
    found, _ = find(project, source_id, ChopQuery(stem="mix", length_beats=4, n=1))
    assert regrid(project, source_id, tempo_factor=1.0).stale_sets == []  # same grid
    assert set_paths(project, source_id)
    out = regrid(project, source_id, tempo_factor=2.0)
    assert out.stale_sets == [found.set_id]
    assert set_paths(project, source_id) == []
    assert project.path(f"cache/chops/{source_id}/stale/{found.set_id}.json").exists()
    again, _ = find(project, source_id, ChopQuery(stem="mix", length_beats=4, n=1))
    assert again.set_id != found.set_id  # ids are never reused


def test_preview_grid_clicks_on_beats_with_accent(rhythmic_project):
    project, source_id, analysis = rhythmic_project
    previews = preview_grid(project, source_id, bars=2, alternatives=True)
    assert previews[0].applied and len(previews) >= 3
    data, sr = sf.read(project.path(previews[0].path))
    assert previews[0].path.startswith(f"cache/previews/{source_id}/grid/")
    # the accented downbeat click (2 kHz) is louder than the beat clicks
    start = previews[0].start_s - min(previews[0].start_s, 0.25)
    first = round((analysis.downbeats_s[0] - start) * sr)
    second = round((analysis.beats_s[1] - start) * sr)
    seg = lambda i: np.max(abs(data[i : i + round(0.02 * sr), 0]))  # noqa: E731
    assert seg(first) > seg(second) > 0
    hypothetical = preview_grid(project, source_id, bars=2, tempo_factor=0.5)
    assert not hypothetical[0].applied and hypothetical[0].bpm == pytest.approx(60)
    assert load_analysis(project, source_id) == analysis  # auditioning never applies


def test_tools_envelope_and_errors(rhythmic_project):
    project, source_id, _ = rhythmic_project
    res = run_tool(
        "source.regrid", {"project": str(project.root), "source_id": source_id, "tempo_factor": 0.5}
    )
    assert res.ok and res.data["grid"]["bpm"] == pytest.approx(60)
    assert res.data["grid"]["origin"] == "manual"
    bad = run_tool(
        "source.regrid", {"project": str(project.root), "source_id": source_id, "tempo_factor": 3}
    )
    assert not bad.ok
    prev = run_tool(
        "source.preview_grid",
        {"project": str(project.root), "source_id": source_id, "start_downbeat": 999},
    )
    assert not prev.ok and prev.errors[0].code == "out_of_range"
    with pytest.raises(MuestraError):
        g.derive_grid(_raw(n=3), tempo_factor=0.5, downbeat_shift_beats=16)
