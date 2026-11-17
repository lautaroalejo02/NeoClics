"""CPU backends, immutable per-model stem caches, and progress callbacks.

``local`` is the execution backend. Legacy config ``audio-separator`` is
accepted as an alias; ``model`` selects the algorithm. ``modal`` is a
reserved, deliberately unimplemented execution slot (no cloud requests).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from ..errors import MuestraError
from ..project import Project, load_analysis
from .audio import decode, stereo
from .storage import digest, project_lock, read_json, source_file, write_json

STEMS = ("drums", "bass", "vocals", "other")
DEFAULT_MODEL = "htdemucs"
Progress = Callable[[float, str], None]


def report(callback: Progress | None, fraction: float, message: str) -> None:
    if callback:
        callback(float(np.clip(fraction, 0, 1)), message)


class SeparationBackend(Protocol):
    def separate(
        self,
        source: Path,
        output: Path,
        model: str,
        progress: Progress | None = None,
    ) -> dict[str, Path]: ...


class ModalBackend:
    """Future GPU execution seam. Credentials, billing and remote storage are unset."""

    def separate(self, source, output, model, progress=None):
        raise MuestraError.one(
            "not_implemented",
            "The Modal separation backend is not implemented.",
            hint="Set separation.backend = 'local' for CPU execution.",
        )


class LocalBackend:
    def separate(self, source, output, model, progress=None):
        if model in {"htdemucs", "htdemucs_ft"}:
            return self._demucs(source, output, model, progress)
        if model == "mel-roformer+htdemucs":
            return self._mel(source, output, progress)
        raise MuestraError.one(
            "invalid_value",
            f"Unknown separation model: {model}.",
            path="model",
            hint="Choose htdemucs, htdemucs_ft or mel-roformer+htdemucs.",
        )

    def _demucs(self, source, output, model, progress):
        import torch
        from demucs_infer.api import Separator

        # Avoid saturating a thin shared laptop. CPU-only, deterministic (no random shifts).
        torch.set_num_threads(max(1, int(os.environ.get("MUESTRA_CPU_THREADS", "2"))))
        report(progress, 0.03, f"Loading {model} (official MIT weights)")

        def chunk(info):
            fraction = info["segment_offset"] / max(1, info["audio_length"])
            report(progress, 0.1 + 0.8 * fraction, f"Separating {model}")

        separator = Separator(
            model=model,
            device="cpu",
            shifts=0,
            overlap=0.25,
            jobs=0,
            callback=chunk,
        )
        audio, sr = decode(source)
        audio = stereo(audio)
        target_sr = separator.samplerate
        if sr != target_sr:
            from math import gcd

            divisor = gcd(sr, target_sr)
            audio = resample_poly(audio, target_sr // divisor, sr // divisor, axis=0)
        _, stems = separator.separate_tensor(
            torch.from_numpy(np.asarray(audio.T, dtype=np.float32)),
            sr=target_sr,
        )
        output.mkdir(parents=True, exist_ok=True)
        result = {}
        for name in STEMS:
            result[name] = output / f"{name}.wav"
            sf.write(result[name], stems[name].cpu().numpy().T, target_sr, subtype="FLOAT")
        return result

    def _mel(self, source, output, progress):
        """Official KimberleyJSN MIT vocal model + Demucs on its instrumental.

        RoFormer is TWO stems, so this explicitly named cascade produces the
        four-stem contract without pretending instrumental == other.
        """
        import logging
        import urllib.request

        import torch
        from audio_separator.separator import Separator

        torch.set_num_threads(max(1, int(os.environ.get("MUESTRA_CPU_THREADS", "2"))))
        cache = Path(os.environ.get("MUESTRA_MODEL_DIR", Path.home() / ".cache/muestra/models"))
        cache.mkdir(parents=True, exist_ok=True)
        weights = cache / "vocals_mel_band_roformer.ckpt"
        config = cache / "vocals_mel_band_roformer.yaml"
        downloads = {
            weights: "https://huggingface.co/KimberleyJSN/melbandroformer/resolve/main/"
            "MelBandRoformer.ckpt",
            config: "https://raw.githubusercontent.com/KimberleyJensen/"
            "Mel-Band-Roformer-Vocal-Model/main/configs/config_vocals_mel_band_roformer.yaml",
        }
        for path, url in downloads.items():
            if not path.exists():
                report(progress, 0.02, f"Downloading official MIT model: {path.name}")
                temporary = path.with_suffix(path.suffix + ".part")
                urllib.request.urlretrieve(url, temporary)
                temporary.replace(path)
        report(progress, 0.05, "Loading Mel-Band RoFormer")
        with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
            separator = Separator(
                model_file_dir=str(cache),
                output_dir=tmp,
                output_format="WAV",
                normalization_threshold=1.0,
                use_soundfile=True,
                log_level=logging.WARNING,
                mdxc_params={"batch_size": 1, "overlap": 4},
            )
            separator.torch_device = torch.device("cpu")
            separator.torch_device_mps = None
            separator.load_model(weights.name)
            report(progress, 0.1, "Separating vocals with Mel-Band RoFormer (CPU)")
            # Float WAV prevents an mp3/m4a input from making the wrapper choose PCM16.
            audio, sr = decode(source)
            floating_source = Path(tmp) / "input-float.wav"
            sf.write(floating_source, stereo(audio), sr, subtype="FLOAT")
            separator.separate(
                str(floating_source),
                custom_output_names={
                    "vocals": "vocals",
                    "other": "instrumental",
                    "instrumental": "instrumental",
                },
            )
            vocal, instrumental = Path(tmp) / "vocals.wav", Path(tmp) / "instrumental.wav"
            if not vocal.is_file() or not instrumental.is_file():
                raise MuestraError.one(
                    "separation_error",
                    "RoFormer did not return vocals and instrumental.",
                    hint="Use model='htdemucs' and check the installed audio-separator version.",
                )
            result = self._demucs(instrumental, output, "htdemucs", progress)
            secondary, ssr = decode(result["vocals"])
            other, _ = decode(result["other"])
            sf.write(result["other"], other + secondary, ssr, subtype="FLOAT")
            vocals, sr = decode(vocal)
            sf.write(result["vocals"], stereo(vocals), sr, subtype="FLOAT")
            return result


def backend_for(name: str) -> SeparationBackend:
    if name in {"local", "audio-separator", "demucs"}:
        return LocalBackend()
    if name == "modal":
        return ModalBackend()
    raise MuestraError.one("invalid_value", f"Unknown separation backend: {name}.")


def separate(
    project: Project,
    source_id: str,
    model: str | None = None,
    *,
    progress: Progress | None = None,
    backend: SeparationBackend | None = None,
) -> tuple[dict[str, str], str, bool]:
    model = model or project.config.separation.model or DEFAULT_MODEL
    _, source = source_file(project, source_id)
    content_hash = digest(source)
    try:
        engine_version = (
            version("demucs-infer")
            if model.startswith("htdemucs")
            else (version("audio-separator") + "+demucs-infer-" + version("demucs-infer"))
        )
    except PackageNotFoundError:
        engine_version = "unavailable"
    identity = f"{content_hash}:{model}:{engine_version}:cpu-shifts0-overlap025-v1"
    key = hashlib.sha256(identity.encode()).hexdigest()[:20]
    directory = project.path(f"cache/stems/{source_id}/{key}")
    manifest = directory / "manifest.json"
    backend = backend or backend_for(project.config.separation.backend)
    last_progress = 0.0
    callback = progress

    def monotonic_progress(value, message):
        nonlocal last_progress
        last_progress = max(last_progress, float(value))
        report(callback, last_progress, message)

    progress = monotonic_progress
    with project_lock(project):
        if manifest.is_file():
            data = read_json(manifest)
            if (
                data.get("identity") == identity
                and all(project.path(p).is_file() for p in data.get("stems", {}).values())
                and set(data.get("stems", {})) == set(STEMS)
            ):
                _activate(project, source_id, data)
                report(progress, 1, "Stems ready (cache)")
                return data["stems"], model, True
        directory.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(dir=directory.parent) as temp:
            try:
                outputs = backend.separate(source, Path(temp), model, progress)
            except ImportError as exc:
                raise MuestraError.one(
                    "missing_dependency",
                    f"Separation dependency unavailable: {exc}",
                    hint="Run uv sync --extra ingest. Current Demucs requires Windows or Apple "
                    "Silicon macOS with the shared NumPy 2 stack.",
                ) from exc
            except MuestraError:
                raise
            except Exception as exc:
                raise MuestraError.one(
                    "separation_error",
                    f"{model} separation failed: {exc}",
                    hint="Check ffmpeg, disk space and network access for official weights. "
                    "Retry with model='htdemucs'; no completed cache was published.",
                ) from exc
            if set(outputs) != set(STEMS):
                raise MuestraError.one("invalid_audio", "Backend must return exactly four stems.")
            formats = [sf.info(path) for path in outputs.values()]
            if (
                len({(i.frames, i.samplerate, i.channels) for i in formats}) != 1
                or formats[0].frames <= 0
                or formats[0].channels != 2
            ):
                raise MuestraError.one("invalid_audio", "Backend stems are not frame-aligned.")
            directory.mkdir(parents=True, exist_ok=True)
            for name, path in outputs.items():
                os.replace(path, directory / f"{name}.wav")
        stems = {
            name: (directory / f"{name}.wav").relative_to(project.root).as_posix() for name in STEMS
        }
        data = {
            "identity": identity,
            "source_sha256": content_hash,
            "model": model,
            "engine_version": engine_version,
            "stems": stems,
            "elapsed_s": time.perf_counter() - started,
        }
        write_json(manifest, data)  # completion marker only after all outputs succeeded
        _activate(project, source_id, data)
        report(progress, 1, "Four stems ready")
        return stems, model, False


def _activate(project: Project, source_id: str, data: dict) -> None:
    write_json(project.path(f"cache/stems/{source_id}/active.json"), data)
    path = project.path(f"cache/analysis/{source_id}.json")
    if path.exists():
        analysis = load_analysis(project, source_id)
        from ..schemas.analysis import Stems

        analysis.stems = Stems(**data["stems"])
        analysis.separation_model = data["model"]
        write_json(path, analysis.model_dump(mode="json"))
