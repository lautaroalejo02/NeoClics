from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from muestra.errors import MuestraError
from muestra.ingest.audio import decode, ffmpeg_path
from muestra.ingest.storage import import_source, source_file
from muestra.project import create_project, load_song


@pytest.fixture
def project(tmp_path):
    return create_project(tmp_path / "project")


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "input.wav"
    sf.write(path, np.sin(np.arange(16000) * 0.1) * 0.1, 16000)
    return path


def test_import_preserves_bytes_comments_and_deduplicates(project, audio):
    project.song_path.write_text(
        project.song_path.read_text(encoding="utf-8") + "\n# preserve me\n", encoding="utf-8"
    )
    source_id, rel = import_source(project, audio, None)
    assert source_id == "src1"
    assert project.path(rel).read_bytes() == audio.read_bytes()
    assert import_source(project, audio, "different") == (source_id, rel)
    assert "# preserve me" in project.song_path.read_text(encoding="utf-8")
    assert len(load_song(project).sources) == 1


def test_import_without_song_and_conflicting_id(project, audio, tmp_path):
    project.song_path.unlink()
    source_id, rel = import_source(project, audio, "record")
    assert source_file(project, source_id) == (rel, project.path(rel))
    other = tmp_path / "other.flac"
    sf.write(other, np.ones(1000) * 0.2, 16000)
    with pytest.raises(MuestraError, match="already in use"):
        import_source(project, other, "record")


def test_missing_ffmpeg_hint(monkeypatch):
    monkeypatch.delenv("MUESTRA_FFMPEG", raising=False)
    monkeypatch.setattr("muestra.ingest.audio.shutil.which", lambda _: None)
    with pytest.raises(MuestraError, match="ffmpeg was not found") as exc:
        ffmpeg_path()
    assert "MUESTRA_FFMPEG" in exc.value.hint


def test_decode_rejects_empty(tmp_path):
    path = tmp_path / "empty.wav"
    sf.write(path, np.zeros(0), 16000)
    with pytest.raises(MuestraError, match="nonempty"):
        decode(path)


def test_ffmpeg_override(monkeypatch):
    monkeypatch.setenv("MUESTRA_FFMPEG", str(Path("ffmpeg-custom")))
    assert ffmpeg_path() == "ffmpeg-custom"
