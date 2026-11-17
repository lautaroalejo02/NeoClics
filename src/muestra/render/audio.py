"""Audio I/O and content-addressed, disposable renderer caches."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import uuid
from pathlib import Path

import numpy as np
import pedalboard
import scipy
import soundfile as sf
from scipy.signal import resample, resample_poly

from ..errors import MuestraError
from ..project.core import Project

# Bump whenever decoding/warping or its quality settings change.
CACHE_ABI = (
    f"render-3-pb{pedalboard.__version__}-numpy{np.__version__}"
    f"-scipy{scipy.__version__}-sndfile{sf.__libsndfile_version__}"
)


def digest(*values: object) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def cache_read(project: Project, kind: str, key: str) -> np.ndarray | None:
    try:
        a = np.load(project.cache_dir / "render" / kind / f"{key}.npy", allow_pickle=False)
        if a.ndim == 2 and a.shape[1] == 2 and a.dtype == np.float32 and np.isfinite(a).all():
            return a
    except (OSError, ValueError, EOFError):
        pass  # Interrupted/corrupt caches are disposable.
    return None


def cache_write(project: Project, kind: str, key: str, audio: np.ndarray) -> None:
    directory = project.cache_dir / "render" / kind
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"{key}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("wb") as handle:
            np.save(handle, np.asarray(audio, dtype=np.float32), allow_pickle=False)
        temporary.replace(directory / f"{key}.npy")
    finally:
        temporary.unlink(missing_ok=True)


def decode(project: Project, path: Path, sample_rate: int) -> tuple[np.ndarray, str]:
    content = file_hash(path)  # Always hash bytes, even when size/mtime haven't changed.
    key = digest(CACHE_ABI, content, sample_rate)
    cached = cache_read(project, "decoded", key)
    if cached is not None:
        return cached, content
    try:
        audio, native_rate = sf.read(path, dtype="float32", always_2d=True)
    except (RuntimeError, sf.LibsndfileError):
        try:
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-f",
                    "f32le",
                    "-ac",
                    "2",
                    "-ar",
                    str(sample_rate),
                    "pipe:1",
                ],
                capture_output=True,
                check=True,
            )
            audio = np.frombuffer(proc.stdout, dtype="<f4").reshape(-1, 2).copy()
            native_rate = sample_rate
        except (OSError, subprocess.CalledProcessError) as exc:
            raise MuestraError.one(
                "audio_decode",
                f"Cannot decode {path.name}: {exc}",
                path=str(path),
                value=str(path),
                hint="Use a valid WAV/FLAC or install ffmpeg for other formats.",
            ) from exc
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    elif audio.shape[1] != 2:
        raise MuestraError.one(
            "audio_channels",
            "Only mono and stereo sources are supported.",
            path=str(path),
            value=audio.shape[1],
            hint="Downmix the source to stereo first.",
        )
    if native_rate != sample_rate:
        common = math.gcd(native_rate, sample_rate)
        audio = resample_poly(
            audio,
            sample_rate // common,
            native_rate // common,
            axis=0,
            window=("kaiser", 10.0),
        )
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if not len(audio) or not np.isfinite(audio).all():
        raise MuestraError.one(
            "invalid_audio",
            "Audio is empty or contains NaN/Inf.",
            path=str(path),
            value=str(path),
            hint="Re-export a finite, nonempty audio file.",
        )
    cache_write(project, "decoded", key, audio)
    return audio, content


def fit(audio: np.ndarray, frames: int) -> np.ndarray:
    result = np.zeros((frames, 2), dtype=np.float32)
    n = min(frames, len(audio))
    result[:n] = audio[:n]
    return result


def fade(audio: np.ndarray, sample_rate: int, ms: float = 5, *, start: bool = True) -> np.ndarray:
    """Raised-cosine edge ramps, zero value and slope at the cut."""
    audio = audio.copy()
    n = min(round(sample_rate * ms / 1000), len(audio) // 2)
    if n > 1:
        ramp = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))).astype(np.float32)
        if start:
            audio[:n] *= ramp[:, None]
        audio[-n:] *= ramp[::-1, None]
    return audio


def vinyl(audio: np.ndarray, frames: int) -> np.ndarray:
    """Band-limited Fourier resampling at one constant rate."""
    if len(audio) == frames:
        return audio.copy()
    return resample(audio, frames, axis=0).astype(np.float32)


def stretch(audio: np.ndarray, sample_rate: int, frames: int, pitch: float = 0) -> np.ndarray:
    if not len(audio) or frames < 1:
        return np.zeros((frames, 2), np.float32)
    if frames == len(audio) and pitch == 0:
        return audio.copy()
    result = pedalboard.time_stretch(
        np.ascontiguousarray(audio.T),
        sample_rate,
        stretch_factor=len(audio) / frames,
        pitch_shift_in_semitones=pitch,
        high_quality=True,
        transient_mode="crisp",
        transient_detector="compound",
        retain_phase_continuity=True,
        use_long_fft_window=None,
        use_time_domain_smoothing=False,
        preserve_formants=True,
    )
    return fit(result.T, frames)
