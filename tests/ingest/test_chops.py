import numpy as np
import pytest
import soundfile as sf

from muestra.errors import MuestraError
from muestra.feedback import read_events
from muestra.ingest.chops import choose, find, read_set
from muestra.ingest.tools import ChopsListParams, chops_list
from muestra.project import chop_times, load_song, save_analysis
from muestra.schemas.chops import ChopAdjust, ChopQuery


def test_candidates_preview_fallback_diversity_and_manual(rhythmic_project):
    project, source_id, analysis = rhythmic_project
    result, path = find(project, source_id, ChopQuery(stem="mix", length_beats=4, n=3))
    assert len(result.candidates) == 3
    assert result.pick.candidate_id == result.candidates[0].id == "cs001/cand1"
    assert "Elijo" in result.pick.justification
    assert read_set(path) == result
    for candidate in result.candidates:
        data, sr = sf.read(project.path(candidate.preview))
        assert len(data) == round((candidate.end_s - candidate.start_s) * sr) * 2
        # WP13: a seamless two-repeat loop (crossfaded seam and wrap), no gap.
        mid = len(data) // 2
        assert np.max(abs(data[mid - 2 : mid + 2])) > 0
        assert candidate.score.reasons
    candidate = result.candidates[0]
    chosen, chop, _, _ = choose(
        project, candidate.id, "loop", "manual", ChopAdjust(start_beats=0.5, length_beats=-1)
    )
    assert chosen.chosen.mode == "manual"
    assert chop.start_offset_beats == 0.5 and chop.length_beats == 3
    timing = chop_times(analysis, chop.start_downbeat, chop.length_beats, 0.5)
    assert timing is not None and timing[0] > candidate.start_s
    event = read_events(project).events[-1]
    assert event.type == "chop_choice" and event.candidate_id == candidate.id
    assert load_song(project).chops[0] == chop
    assert len(chops_list(ChopsListParams(project=str(project.root))).sets) == 1


def test_real_renderer_seam_and_auto_no_feedback(rhythmic_project, monkeypatch):
    project, source_id, _ = rhythmic_project
    calls = []

    def render(project, chop, sample_rate, bpm):
        calls.append((chop, sample_rate, bpm))
        return np.ones((400, 2), dtype=np.float32) * 0.1

    monkeypatch.setattr("muestra.render.core.render_chop_loop", render)
    result, _ = find(project, source_id, ChopQuery(stem="mix", n=1, length_beats=4))
    assert calls[0][1] == project.config.sample_rate
    info = sf.info(project.path(result.candidates[0].preview))
    assert info.frames == 400
    before = project.feedback_path.read_bytes()
    choose(project, result.pick.candidate_id, "loop", "auto", None)
    assert project.feedback_path.read_bytes() == before


def test_unknown_section_missing_stem_and_invalid_adjust(rhythmic_project):
    project, source_id, _ = rhythmic_project
    with pytest.raises(MuestraError, match="No other stem"):
        find(project, source_id, ChopQuery())
    with pytest.raises(MuestraError, match="Unknown section"):
        find(project, source_id, ChopQuery(stem="mix", section="chorus"))
    result, _ = find(project, source_id, ChopQuery(stem="mix", n=1, length_beats=4))
    before = project.song_path.read_bytes()
    with pytest.raises(MuestraError):
        choose(project, result.pick.candidate_id, "loop", "manual", ChopAdjust(length_beats=-10))
    assert project.song_path.read_bytes() == before


def test_stale_grid_cannot_be_chosen(rhythmic_project):
    project, source_id, analysis = rhythmic_project
    result, _ = find(project, source_id, ChopQuery(stem="mix", n=1))
    analysis.beats_s = [b + 0.01 for b in analysis.beats_s]
    save_analysis(project, analysis)
    with pytest.raises(MuestraError, match="cambiaron"):
        choose(project, result.pick.candidate_id, "loop", "manual", None)


def test_interrupted_preview_reserves_id(rhythmic_project, monkeypatch):
    project, source_id, _ = rhythmic_project

    def broken(*args):
        raise RuntimeError("renderer failed")

    monkeypatch.setattr("muestra.render.core.render_chop_loop", broken)
    with pytest.raises(RuntimeError, match="renderer failed"):
        find(project, source_id, ChopQuery(stem="mix", n=1))
    monkeypatch.undo()
    result, _ = find(project, source_id, ChopQuery(stem="mix", n=1))
    assert result.set_id == "cs002"


def test_global_set_ids_across_sources(rhythmic_project, tmp_path):
    from muestra.ingest.storage import import_source

    project, source_id, analysis = rhythmic_project
    first, _ = find(project, source_id, ChopQuery(stem="mix", n=1))
    data, sr = sf.read(project.path(analysis.file))
    another = tmp_path / "another.wav"
    sf.write(another, data * 0.5, sr)
    new_id, rel = import_source(project, another, None)
    new_analysis = analysis.model_copy(update={"source_id": new_id, "file": rel})
    save_analysis(project, new_analysis)
    second, _ = find(project, new_id, ChopQuery(stem="mix", n=1))
    assert first.set_id == "cs001" and second.set_id == "cs002"


def _loop_track(project, repeat=8):
    from muestra.project import edit_song_document

    def mutate(doc):
        doc["meta"]["length_bars"] = 8
        doc["tracks"] = [{"id": "sample", "clips": [{"chop": "c1", "at_bar": 1, "repeat": repeat}]}]

    edit_song_document(project, mutate)


def test_manual_longer_choice_rescales_repeat_and_is_recorded(rhythmic_project):
    """WP13 finding 2: picking a longer loop used to raise clip_overrun with no
    chop_choice event; now the span is kept (8 x 4 beats -> 4 x 8 beats)."""
    from muestra.ingest.tools import ChopsChooseParams, chops_choose

    project, source_id, _ = rhythmic_project
    result, _ = find(project, source_id, ChopQuery(stem="mix", length_beats=4, n=2))
    choose(project, result.candidates[0].id, "c1", "auto", None)
    _loop_track(project)
    out = chops_choose(
        ChopsChooseParams(
            project=str(project.root),
            candidate_id=result.candidates[1].id,
            chop_id="c1",
            adjust=ChopAdjust(length_beats=4),
        )
    )
    song = load_song(project)
    assert song.chops[0].length_beats == 8 and song.tracks[0].clips[0].repeat == 4
    assert out.chop_id == "c1" and out.agent_todo is None
    assert [(c.track, c.old_repeat, c.new_repeat) for c in out.repeat_changes] == [("sample", 8, 4)]
    assert out.message.startswith("Listo") and "repeticiones" in out.message
    event = read_events(project).events[-1]
    assert event.type == "chop_choice" and event.candidate_id == result.candidates[1].id
    assert event.adjust.length_beats == 4


def test_manual_choice_that_cannot_keep_the_span_becomes_a_new_chop(rhythmic_project):
    project, source_id, _ = rhythmic_project
    result, _ = find(project, source_id, ChopQuery(stem="mix", length_beats=4, n=1))
    choose(project, result.candidates[0].id, "c1", "auto", None)
    _loop_track(project)
    before = load_song(project)
    chosen, chop, _, outcome = choose(
        project, result.candidates[0].id, "c1", "manual", ChopAdjust(length_beats=2)
    )
    song = load_song(project)
    assert chop.id == outcome["chop_id"] == "c1-2" and chop.length_beats == 6
    assert song.chops[0] == before.chops[0]  # c1 and its clips untouched
    assert song.tracks == before.tracks
    assert "c1-2" in outcome["agent_todo"] and "chop nuevo" in outcome["message"]
    assert chosen.chosen.chop_id == "c1-2"
    assert read_events(project).events[-1].type == "chop_choice"


def test_manual_choice_is_recorded_even_when_it_fails(rhythmic_project):
    project, source_id, _ = rhythmic_project
    result, _ = find(project, source_id, ChopQuery(stem="mix", length_beats=4, n=1))
    with pytest.raises(MuestraError):
        choose(project, result.pick.candidate_id, "c1", "manual", ChopAdjust(length_beats=-10))
    event = read_events(project).events[-1]
    assert event.type == "chop_choice" and event.adjust.length_beats == -10


def test_find_chops_by_time_window_and_exact_spot(rhythmic_project):
    """WP13 finding 4: 'el riff del 0:08' is one call; novelty sections can be
    huge, so a time window filters candidates directly."""
    from muestra.registry import run_tool

    project, source_id, analysis = rhythmic_project
    base = {"project": str(project.root), "source_id": source_id, "stem": "mix"}
    windowed = run_tool("source.find_chops", {**base, "window_s": [4, 10], "length_beats": 4})
    assert windowed.ok, windowed.errors
    for c in windowed.data["set"]["candidates"]:
        assert c["start_s"] >= 4 - 0.25 and c["end_s"] <= 10 + 0.25
        assert c["bar"] == c["start_downbeat"] and c["start_mmss"].startswith("0:0")
        assert c["length_s"] == pytest.approx(c["end_s"] - c["start_s"], abs=1e-3)
    assert windowed.data["downbeats_s"] == [round(t, 3) for t in analysis.downbeats_s]

    exact = run_tool("source.find_chops", {**base, "at_s": "0:08.3", "length_beats": 4, "n": 3})
    assert exact.ok, exact.errors
    first = exact.data["set"]["candidates"][0]
    assert first["start_s"] == pytest.approx(8.25) and first["bar"] == 5
    assert (
        first["start_mmss"] == "0:08.2" and exact.data["set"]["pick"]["candidate_id"] == first["id"]
    )
    assert len(exact.data["set"]["candidates"]) == 3
    assert exact.data["set"]["query"]["at_s"] == pytest.approx(8.3)

    empty = run_tool("source.find_chops", {**base, "window_s": [4, 5], "length_beats": 16})
    assert not empty.ok and empty.errors[0].code == "no_candidates"
    assert "window_s" in empty.hint
    bad = run_tool("source.find_chops", {**base, "at_s": "quince"})
    assert not bad.ok
