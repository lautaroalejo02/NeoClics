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
        assert np.max(abs(data[0])) == 0
        assert candidate.score.reasons
    candidate = result.candidates[0]
    chosen, chop, _ = choose(
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
        return np.ones((200, 2), dtype=np.float32) * 0.1

    monkeypatch.setattr("muestra.render.core.render_chop", render)
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
    with pytest.raises(MuestraError, match="changed since"):
        choose(project, result.pick.candidate_id, "loop", "manual", None)
