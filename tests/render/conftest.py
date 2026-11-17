from __future__ import annotations

import shutil

import numpy as np
import pytest
import soundfile as sf

from muestra.plugins import core as plugins
from muestra.project import load_song, open_project
from tests.conftest import EXAMPLE_PROJECT, FIXTURES


@pytest.fixture
def project(tmp_path):
    return prepare_fixture(tmp_path)


def prepare_fixture(tmp_path):
    """Copy and reconcile the scaffold's deliberately short placeholder stems."""
    root = tmp_path / "song"
    shutil.copytree(EXAMPLE_PROJECT, root)
    shutil.copytree(FIXTURES / "library", tmp_path / "library")
    # The shared scaffold has 1.2 s placeholder stems but a 5.7 s analysis.
    # Extend ONLY this disposable test copy with fixture audio; the renderer
    # correctly rejects the original inconsistent stem rather than padding it.
    for path in (root / "cache/stems/src1").glob("*.wav"):
        audio, sr = sf.read(path, dtype="float32", always_2d=True)
        audio = np.tile(audio, (5, 1))[: round(5.7 * sr)]
        sf.write(path, audio, sr, subtype="PCM_16")
    return open_project(root)


@pytest.fixture
def song(project):
    return load_song(project)


def fixture_instrument(project, instrument, notes, sample_rate, n_frames, *, bpm):
    """Test-only host fake using fixture bass AUDIO, never a product fallback."""
    audio, sr = sf.read(project.path("cache/stems/src1/bass.wav"), dtype="float32", always_2d=True)
    from scipy.signal import resample_poly

    from muestra.render.core import beat_frame

    if sr != sample_rate:
        from math import gcd

        common = gcd(sr, sample_rate)
        audio = resample_poly(audio, sample_rate // common, sr // common, axis=0)
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    result = np.zeros((n_frames, 2), np.float32)
    for note in notes:
        start = beat_frame(note.beat - 1, bpm, sample_rate)
        end = min(n_frames, beat_frame(note.beat - 1 + note.length_beats, bpm, sample_rate))
        if end > start:
            piece = np.resize(audio, (end - start, 2)) * (note.velocity / 127)
            result[start:end] += piece
    return result


@pytest.fixture
def fake_instrument(monkeypatch):
    monkeypatch.setattr(plugins, "render_instrument", fixture_instrument)
