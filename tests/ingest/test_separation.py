from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from muestra.errors import MuestraError
from muestra.ingest.audio import decode, stereo
from muestra.ingest.separation import STEMS, ModalBackend, separate
from muestra.ingest.storage import read_json


class FakeBackend:
    calls = 0

    def separate(self, source, output, model, progress=None):
        self.calls += 1
        audio, sr = decode(source)
        outputs = {}
        for name in STEMS:
            outputs[name] = output / f"{name}.wav"
            sf.write(outputs[name], stereo(audio) / 4, sr, subtype="FLOAT")
        return outputs


def test_cache_model_switch_and_progress(rhythmic_project):
    project, source_id, _ = rhythmic_project
    backend = FakeBackend()
    progress = []
    first, _, cached = separate(
        project, source_id, backend=backend, progress=lambda value, text: progress.append(value)
    )
    assert not cached and progress[-1] == 1
    second, _, cached = separate(project, source_id, backend=backend)
    assert cached and first == second and backend.calls == 1
    alternative, _, cached = separate(project, source_id, "htdemucs_ft", backend=backend)
    assert not cached and alternative != first
    assert all(project.path(path).is_file() for path in first.values())
    assert separate(project, source_id, backend=backend)[2]
    project.path(first["bass"]).unlink()
    assert not separate(project, source_id, backend=backend)[2]
    assert backend.calls == 3


def test_failure_never_marks_cache_complete(rhythmic_project):
    project, source_id, _ = rhythmic_project

    class Broken:
        def separate(self, *args):
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        separate(project, source_id, backend=Broken())
    assert not list(project.path(f"cache/stems/{source_id}").glob("*/manifest.json"))
    assert not project.path(".muestra-ingest.lock").exists()


def test_source_mutation_invalidates_cache(rhythmic_project):
    project, source_id, analysis = rhythmic_project
    backend = FakeBackend()
    first = separate(project, source_id, backend=backend)[0]
    path = project.path(analysis.file)
    data, sr = decode(path)
    sf.write(path, data * 0.5, sr)
    second = separate(project, source_id, backend=backend)[0]
    assert first != second
    active = read_json(project.path(f"cache/stems/{source_id}/active.json"))
    assert active["stems"] == second


def test_modal_is_explicitly_unimplemented(tmp_path):
    with pytest.raises(MuestraError, match="not implemented"):
        ModalBackend().separate(Path("in"), tmp_path, "htdemucs")


def test_backend_rejects_unaligned_stems(rhythmic_project):
    project, source_id, _ = rhythmic_project

    class Unaligned(FakeBackend):
        def separate(self, *args):
            paths = super().separate(*args)
            sf.write(paths["bass"], np.zeros((10, 2)), 8000)
            return paths

    with pytest.raises(MuestraError, match="frame-aligned"):
        separate(project, source_id, backend=Unaligned())
