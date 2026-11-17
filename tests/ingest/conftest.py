import os

import numpy as np
import pytest
import soundfile as sf

from muestra.ingest.storage import import_source
from muestra.project import create_project, save_analysis
from muestra.schemas.analysis import Analysis, Section


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: downloads official models and runs CPU inference")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("MUESTRA_RUN_SLOW") != "1":
        for item in items:
            if "slow" in item.keywords and "ingest" in str(item.path):
                item.add_marker(
                    pytest.mark.skip(reason="Set MUESTRA_RUN_SLOW=1 to run model tests")
                )


@pytest.fixture
def rhythmic_project(tmp_path):
    project = create_project(tmp_path / "project")
    sr = 8000
    duration = 18
    time = np.arange(sr * duration) / sr
    audio = 0.12 * np.sin(2 * np.pi * 220 * time)
    beats = np.arange(0.25, duration - 0.1, 0.5)
    rng = np.random.default_rng(11)
    for beat in beats:
        start = round(beat * sr)
        pulse = rng.normal(0, 0.2, 400) * np.exp(-np.arange(400) / 70)
        audio[start : start + len(pulse)] += pulse
    path = tmp_path / "record.wav"
    sf.write(path, np.column_stack([audio, audio]), sr)
    source_id, rel = import_source(project, path, None)
    analysis = Analysis(
        source_id=source_id,
        file=rel,
        duration_s=duration,
        sample_rate=sr,
        bpm_global=120,
        beats_s=beats.tolist(),
        downbeats_s=beats[::4].tolist(),
        sections=[Section(label="intro", start_s=0, end_s=duration)],
    )
    save_analysis(project, analysis)
    return project, source_id, analysis
