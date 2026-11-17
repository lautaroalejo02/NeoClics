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


def test_inconsistent_meter_is_rejected_before_cache(rhythmic_project):
    project, source_id, _ = rhythmic_project
    beats = np.arange(0.25, 17, 0.5)
    with pytest.raises(MuestraError, match="four beats"):
        analyze(project, source_id, tracker=lambda audio, sr: (beats, beats[::3]))
    assert not project.path(f"cache/analysis/{source_id}.meta.json").exists()
