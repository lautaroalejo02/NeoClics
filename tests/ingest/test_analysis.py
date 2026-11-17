import numpy as np
import pytest

from muestra.errors import MuestraError
from muestra.ingest.analysis import analyze, estimate_key, validate_grid
from muestra.project import load_analysis


def test_analysis_variable_grid_cache_and_no_separation(rhythmic_project, monkeypatch):
    project, source_id, fixture = rhythmic_project
    beats = np.cumsum(np.linspace(0.48, 0.56, 31))
    beats -= beats[0] - 0.25
    calls = []

    def tracker(audio, sr):
        calls.append(sr)
        return beats, beats[::4]

    monkeypatch.setattr("muestra.ingest.analysis.integrated_loudness", lambda a, sr: -18.0)
    result = analyze(project, source_id, tracker=tracker)
    assert result.tempo_variable
    assert np.allclose(result.beats_s, beats)
    assert result.downbeats_s == beats[::4].tolist()
    assert result.stems is None and result.separation_model is None
    assert result.sections[0].start_s == 0
    assert result.sections[-1].end_s == fixture.duration_s
    assert result.loudness_lufs == -18
    assert analyze(project, source_id, tracker=tracker) == load_analysis(project, source_id)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "beats,downbeats",
    [
        ([], []),
        ([1, 1, 2], [1]),
        ([1, 2, 3], [1.5]),
        ([1, 2, np.nan], [1]),
        ([1, 2, 3], [1, 1]),
        ([1, 2, 30], [1]),
    ],
)
def test_invalid_grid_never_invents_beats(beats, downbeats):
    with pytest.raises(MuestraError, match="grid|Downbeats"):
        validate_grid(beats, downbeats, 10)


def test_key_profile_and_silence():
    profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    estimate = estimate_key(np.roll(profile, 7)[:, None])
    assert estimate.tonic == "G" and estimate.mode == "major"
    assert 0 < estimate.confidence <= 1
    assert estimate_key(np.zeros((12, 10))) is None


def test_inconsistent_meter_is_cached_with_reliability(rhythmic_project):
    project, source_id, _ = rhythmic_project
    beats = np.arange(0.25, 17, 0.5)
    result = analyze(project, source_id, tracker=lambda audio, sr: (beats, beats[::3]))
    assert result.reliability.confidence == 0
    assert not any(r.reliable for r in result.reliability.regions)
    assert result.downbeats_s == beats[::3].tolist()
    assert project.path(f"cache/analysis/{source_id}.meta.json").exists()


def test_partial_grid_windows_use_full_analysis(rhythmic_project):
    from muestra.ingest.chops import choose, find
    from muestra.ingest.regrid import preview_grid
    from muestra.ingest.storage import write_json
    from muestra.project.tools import SongValidateParams, song_validate
    from muestra.schemas.chops import ChopQuery

    project, source_id, _ = rhythmic_project
    beats = np.arange(0.25, 17, 0.5)
    # Three irregular bars, then a stable run. No 75% acceptance gate.
    downs = beats[[0, 3, 6, 9, 13, 17, 21, 25, 29, 33]]
    result = analyze(project, source_id, tracker=lambda a, sr: (beats, downs))
    assert 0 < result.reliability.confidence < 0.75
    found, _ = find(project, source_id, ChopQuery(stem="mix", length_beats=4, n=2))
    assert all(c.start_s >= downs[3] for c in found.candidates)
    previews = preview_grid(project, source_id, window_s=(1.2, 4.2))
    assert previews[0].start_s == 1.2
    assert previews[0].end_s == 4.2
    assert load_analysis(project, source_id).beats_s == result.beats_s
    doubtful, _ = find(
        project,
        source_id,
        ChopQuery(stem="mix", at_s=0.25, length_beats=4, n=1, allow_unreliable=True),
    )
    _, _, _, outcome = choose(project, doubtful.candidates[0].id, "c1", "manual", None)
    assert outcome["warnings"]
    validated = song_validate(SongValidateParams(project=str(project.root)))
    assert validated.valid and validated.warnings
    # Attaching separated stems must not invoke the tracker or alter the grid.
    from muestra.ingest.separation import _activate

    write_json(project.path("cache/test.json"), {})
    _activate(project, source_id, {"stems": {"other": result.file}, "model": "test"})
    attached = load_analysis(project, source_id)
    assert attached.stems.other == result.file
    assert attached.beats_s == result.beats_s
    assert attached.reliability == result.reliability


def test_valid_audio_without_tracked_pulse_is_not_an_analysis_error(rhythmic_project):
    from muestra.ingest.regrid import preview_grid
    from muestra.ingest.tools import SourceAnalyzeParams, source_analyze

    project, source_id, _ = rhythmic_project
    result = analyze(project, source_id, tracker=lambda a, sr: ([], []))
    assert result.bpm_global is None and not result.beats_s
    assert result.reliability.confidence == 0
    assert (
        source_analyze(SourceAnalyzeParams(project=str(project.root), source_id=source_id)).grid
        is None
    )
    audition = preview_grid(project, source_id, window_s=(1, 3))
    assert audition[0].bpm is None and project.path(audition[0].path).exists()
