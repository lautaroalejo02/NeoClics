from time import perf_counter

import numpy as np
import pytest
import soundfile as sf

from muestra.ingest.analysis import neural_beats, validate_grid
from muestra.ingest.separation import STEMS, separate
from muestra.ingest.storage import import_source
from muestra.ingest.validation import matching_score
from muestra.project import create_project

pytestmark = pytest.mark.slow


def test_official_demucs_cpu(tmp_path):
    sr = 44100
    time = np.arange(sr * 4) / sr
    audio = np.column_stack([0.1 * np.sin(2 * np.pi * 220 * time)] * 2)
    source = tmp_path / "synth.wav"
    sf.write(source, audio, sr)
    project = create_project(tmp_path / "project")
    source_id, _ = import_source(project, source, None)
    started = perf_counter()
    paths, _, cached = separate(project, source_id, "htdemucs")
    assert not cached
    first_elapsed = perf_counter() - started
    started = perf_counter()
    assert separate(project, source_id, "htdemucs")[2]
    print("Real Demucs 4s separation/cache seconds:", first_elapsed, perf_counter() - started)
    assert set(paths) == set(STEMS)
    for path in paths.values():
        data, rate = sf.read(project.path(path))
        assert rate == sr and data.shape == audio.shape and np.isfinite(data).all()


def test_neural_drifting_drum_grid():
    """Independent synthesized onset/downbeat truth, not predictions compared to themselves."""
    sr = 22050
    beats = np.r_[0.5, 0.5 + np.cumsum(np.linspace(0.48, 0.56, 63))]
    audio = np.zeros(round((beats[-1] + 1) * sr), dtype=np.float32)
    rng = np.random.default_rng(51)
    for index, beat in enumerate(beats):
        start = round(beat * sr)
        t = np.arange(round(sr * 0.2)) / sr
        kick = 0.7 * np.sin(2 * np.pi * (65 * t - 55 * t**2)) * np.exp(-t * 30)
        snare = rng.normal(0, 0.25, len(t)) * np.exp(-t * 40)
        pulse = kick + (snare if index % 2 == 1 else 0)
        if index % 4 == 0:
            pulse += 0.2 * np.sin(2 * np.pi * 110 * t) * np.exp(-t * 8)
        audio[start : start + len(t)] += pulse
        off = round((beat + 0.25) * sr)
        hat = rng.normal(0, 0.06, 600) * np.exp(-np.arange(600) / 100)
        audio[off : off + len(hat)] += hat
    detected, downbeats = validate_grid(*neural_beats(audio, sr), len(audio) / sr)
    score = matching_score(
        beats[4:-4], detected[(detected >= beats[4] - 0.1) & (detected <= beats[-5] + 0.1)]
    )
    print("Independent drifting fixture beat evidence:", score)
    print("Downbeat evidence:", matching_score(beats[::4], downbeats))
    assert score["f1"] >= 0.85
