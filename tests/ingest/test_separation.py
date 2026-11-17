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
        output.mkdir(parents=True, exist_ok=True)
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

    with pytest.raises(MuestraError, match="interrupted"):
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


@pytest.mark.parametrize("kind", ["instrumental", "no-drums"])
def test_derived_mix_uses_three_aligned_stems_and_keeps_grid(rhythmic_project, kind):
    from muestra.ingest.analysis import analyze
    from muestra.ingest.derived_mix import derive_mix, import_derived
    from muestra.project import load_analysis

    project, source_id, original = rhythmic_project
    separate(project, source_id, backend=FakeBackend())
    path = derive_mix(project, source_id, kind)
    combined, sr = decode(path)
    source, _ = decode(project.resolve(original.file))
    assert np.allclose(combined, source * 0.75, atol=1e-7)
    assert derive_mix(project, source_id, kind) == path
    new_id, _ = import_derived(project, source_id, kind, "src-derived")
    derived = load_analysis(project, new_id)
    assert derived.beats_s == original.beats_s
    assert derived.downbeats_s == original.downbeats_s
    assert derived.stems is None and sr == original.sample_rate
    cached = analyze(project, new_id, tracker=lambda *args: pytest.fail("Do not retrack excerpts"))
    assert cached.beats_s == original.beats_s


def test_backend_rejects_unaligned_stems(rhythmic_project):
    project, source_id, _ = rhythmic_project

    class Unaligned(FakeBackend):
        def separate(self, *args):
            paths = super().separate(*args)
            sf.write(paths["bass"], np.zeros((10, 2)), 8000)
            return paths

    with pytest.raises(MuestraError, match="frame-aligned"):
        separate(project, source_id, backend=Unaligned())


def test_mel_cascade_output_names_and_float_intermediate(rhythmic_project, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from muestra.ingest.separation import LocalBackend

    project, _, analysis = rhythmic_project
    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "vocals_mel_band_roformer.ckpt").touch()
    (cache / "vocals_mel_band_roformer.yaml").touch()
    monkeypatch.setenv("MUESTRA_MODEL_DIR", str(cache))
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None, device=lambda x: x)
    )

    class FakeSeparator:
        def __init__(self, **kwargs):
            self.output = Path(kwargs["output_dir"])

        def load_model(self, filename):
            assert filename == "vocals_mel_band_roformer.ckpt"

        def separate(self, source, custom_output_names):
            assert sf.info(source).subtype == "FLOAT"
            assert custom_output_names["other"] == "instrumental"
            audio, sr = decode(Path(source))
            sf.write(self.output / "vocals.wav", audio * 0.2, sr, subtype="FLOAT")
            sf.write(self.output / "instrumental.wav", audio * 0.8, sr, subtype="FLOAT")
            return ["vocals.wav", "instrumental.wav"]

    monkeypatch.setitem(
        sys.modules, "audio_separator.separator", SimpleNamespace(Separator=FakeSeparator)
    )
    monkeypatch.setattr(LocalBackend, "_demucs", lambda self, *args: FakeBackend().separate(*args))
    paths = LocalBackend().separate(
        project.path(analysis.file), tmp_path / "outputs", "mel-roformer+htdemucs"
    )
    separated = [decode(path)[0] for path in paths.values()]
    original, _ = decode(project.path(analysis.file))
    assert np.allclose(sum(separated), original, atol=1e-6)
