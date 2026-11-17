import io
import subprocess

import numpy as np
import pytest
import soundfile as sf

from muestra.ingest.audio import decode
from muestra.ingest.tools import (
    ChopsChooseParams,
    ChopsListParams,
    SourceAnalyzeParams,
    SourceFindChopsParams,
    SourceImportParams,
    SourceSeparateParams,
    chops_choose,
    chops_list,
    source_analyze,
    source_find_chops,
    source_import,
    source_separate,
)

from .test_separation import FakeBackend


def test_all_six_tools(rhythmic_project, monkeypatch):
    project, source_id, analysis = rhythmic_project
    common = {"project": str(project.root), "source_id": source_id}
    imported = source_import(
        SourceImportParams(project=str(project.root), path=str(project.path(analysis.file)))
    )
    assert imported.source_id == source_id
    monkeypatch.setattr("muestra.ingest.separation.backend_for", lambda _: FakeBackend())
    monkeypatch.setattr(
        "muestra.ingest.analysis.neural_beats",
        lambda audio, sr: (analysis.beats_s, analysis.downbeats_s),
    )
    monkeypatch.setattr("muestra.ingest.analysis.integrated_loudness", lambda a, sr: -20)
    separated = source_separate(SourceSeparateParams(**common))
    assert not separated.cached and len(separated.stems) == 4
    analyzed = source_analyze(SourceAnalyzeParams(**common))
    assert analyzed.analysis.stems.other == separated.stems["other"]
    found = source_find_chops(SourceFindChopsParams(**common, length_beats=4, n=1))
    chosen = chops_choose(
        ChopsChooseParams(
            project=str(project.root),
            candidate_id=found.set.pick.candidate_id,
            chop_id="sample",
            mode="auto",
        )
    )
    assert chosen.chop.source == source_id
    assert chops_list(ChopsListParams(project=str(project.root))).sets[0].chosen.mode == "auto"


def test_decode_ffmpeg_fallback_uses_argument_list(tmp_path, monkeypatch):
    original_read = sf.read
    encoded = io.BytesIO()
    sf.write(encoded, np.ones((100, 2)) * 0.1, 8000, format="WAV", subtype="FLOAT")
    path = tmp_path / "odd name; not a command.m4a"
    path.write_bytes(b"not soundfile")
    calls = []

    def read(value, **kwargs):
        if value == path:
            raise sf.SoundFileError("unsupported")
        return original_read(value, **kwargs)

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=encoded.getvalue())

    monkeypatch.setattr("muestra.ingest.audio.sf.read", read)
    monkeypatch.setattr("muestra.media.subprocess.run", run)  # shared resolver (WP13)
    data, sr = decode(path, ffmpeg="custom-ffmpeg")
    assert data.shape == (100, 2) and sr == 8000
    assert calls[0][0] == "custom-ffmpeg" and str(path) in calls[0]


def test_tiny_chop_returns_structured_error(rhythmic_project):
    from muestra.errors import MuestraError
    from muestra.ingest.chops import find
    from muestra.schemas.chops import ChopQuery

    project, source_id, _ = rhythmic_project
    with pytest.raises(MuestraError, match="No audible loops"):
        find(project, source_id, ChopQuery(stem="mix", length_beats=0.000001))
