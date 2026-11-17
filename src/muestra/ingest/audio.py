"""Portable decoding. Imported originals are never transcoded in place."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import soundfile as sf

from ..errors import MuestraError
from ..media import decode_failure, require_ffmpeg, run_ffmpeg


def ffmpeg_path(override: str | None = None) -> str:
    """The shared resolver (muestra.media): override, MUESTRA_FFMPEG, PATH,
    usual install locations."""
    return require_ffmpeg(override)


def decode(path: Path, *, ffmpeg: str | None = None) -> tuple[np.ndarray, int]:
    """Return finite float32 (frames, channels), preserving native sample rate."""
    try:
        audio, sr = sf.read(path, dtype="float32", always_2d=True)
    except (sf.SoundFileError, OSError) as native:
        if not Path(path).is_file():
            raise MuestraError.one(
                "file_not_found", f"{path} does not exist.", path=str(path)
            ) from native
        # Damaged native files say so; ffmpeg is only blamed when it's needed.
        data = run_ffmpeg(
            Path(path), ["-f", "wav", "-acodec", "pcm_f32le", "pipe:1"], ffmpeg, native
        )
        try:
            audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        except (sf.SoundFileError, RuntimeError) as exc:
            raise decode_failure(Path(path), exc) from exc
    if not len(audio) or audio.shape[1] not in (1, 2) or not np.isfinite(audio).all():
        raise MuestraError.one(
            "invalid_audio",
            "Audio must be nonempty, finite and mono or stereo.",
            hint="Export a mono or stereo WAV/FLAC and import it again.",
        )
    return audio, sr


def stereo(audio: np.ndarray) -> np.ndarray:
    return np.repeat(audio, 2, axis=1) if audio.shape[1] == 1 else audio


def fade(audio: np.ndarray, sr: int, ms: float = 5) -> np.ndarray:
    audio = audio.copy()
    n = min(round(sr * ms / 1000), len(audio) // 2)
    if n:
        ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
        audio[:n] *= ramp
        audio[-n:] *= ramp[::-1]
    return audio
