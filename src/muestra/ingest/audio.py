"""Portable decoding. Imported originals are never transcoded in place."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from ..errors import MuestraError


def ffmpeg_path(override: str | None = None) -> str:
    executable = override or os.environ.get("MUESTRA_FFMPEG") or shutil.which("ffmpeg")
    if executable:
        return str(executable)
    raise MuestraError.one(
        "file_not_found",
        "ffmpeg was not found.",
        path="ffmpeg",
        hint="Install ffmpeg (winget install Gyan.FFmpeg / brew install ffmpeg), restart your "
        "terminal, or set MUESTRA_FFMPEG to its executable path.",
    )


def decode(path: Path, *, ffmpeg: str | None = None) -> tuple[np.ndarray, int]:
    """Return finite float32 (frames, channels), preserving native sample rate."""
    try:
        audio, sr = sf.read(path, dtype="float32", always_2d=True)
    except (sf.SoundFileError, OSError):
        try:
            result = subprocess.run(
                [
                    ffmpeg_path(ffmpeg),
                    "-v",
                    "error",
                    "-nostdin",
                    "-i",
                    str(path),
                    "-f",
                    "wav",
                    "-acodec",
                    "pcm_f32le",
                    "pipe:1",
                ],
                capture_output=True,
                check=True,
            )
            audio, sr = sf.read(io.BytesIO(result.stdout), dtype="float32", always_2d=True)
        except (subprocess.CalledProcessError, OSError, sf.SoundFileError) as exc:
            raise MuestraError.one(
                "decode_error",
                f"Cannot decode {path.name}: {exc}",
                path=str(path),
                hint="Check that this is a valid wav/flac/mp3/m4a file and ffmpeg runs.",
            ) from exc
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
