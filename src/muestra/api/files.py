"""Restricted audio serving and bounded-memory waveform extraction."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import HTTPException
from pydantic import ValidationError

from ..schemas._base import WINDOWS_RESERVED_NAMES, LazyAdapter, RelPath

_RELATIVE = LazyAdapter(RelPath)
_RESERVED = WINDOWS_RESERVED_NAMES | {f"{prefix}{n}" for prefix in ("COM", "LPT") for n in "¹²³"}
MIME = {
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
}


def audio_path(root: Path, value: str) -> Path:
    try:
        _RELATIVE.validate_python(value)
        if any(ord(c) < 32 or c in '<>"|?*' for c in value):
            raise ValueError("Invalid filename")
        if any(p.split(".", 1)[0].upper() in _RESERVED for p in value.split("/")):
            raise ValueError("Reserved Windows filename")
        parts = value.split("/")
        if parts[0] != "renders" and parts[:2] != ["cache", "previews"]:
            raise ValueError("Outside audio folders")
        path = (root / value).resolve()
        actual = path.relative_to(root.resolve()).parts
        if actual[0] != "renders" and actual[:2] != ("cache", "previews"):
            raise ValueError("Symlink escapes audio folders")
        if path.suffix.lower() not in MIME:
            raise ValueError("Only audio files are served")
    except (ValidationError, ValueError, OSError, IndexError) as exc:
        raise HTTPException(403, "Use an audio path inside renders/ or cache/previews/.") from exc
    if not path.is_file():
        raise HTTPException(404, "Audio file not found.")
    return path


@lru_cache(maxsize=32)
def _peaks(path: str, mtime_ns: int, size: int, points: int) -> dict:
    # mtime/size form the cache key. Never load an entire multi-minute WAV into memory.
    with sf.SoundFile(path) as audio:
        frames, channels, rate = len(audio), audio.channels, audio.samplerate
        if channels > 32:
            raise ValueError("Waveform supports up to 32 channels.")
        count = min(points, frames)
        edges = np.linspace(0, frames, count + 1, dtype=np.int64)
        minima = np.full((count, channels), np.inf, dtype=np.float32)
        maxima = np.full((count, channels), -np.inf, dtype=np.float32)
        position, bucket = 0, 0
        for block in audio.blocks(blocksize=65536, dtype="float32", always_2d=True):
            np.nan_to_num(block, copy=False, nan=0, posinf=0, neginf=0)
            end = position + len(block)
            while bucket < count and edges[bucket] < end:
                lo, hi = max(position, edges[bucket]), min(end, edges[bucket + 1])
                if hi > lo:
                    chunk = block[lo - position : hi - position]
                    minima[bucket] = np.minimum(minima[bucket], chunk.min(axis=0))
                    maxima[bucket] = np.maximum(maxima[bucket], chunk.max(axis=0))
                if edges[bucket + 1] <= end:
                    bucket += 1
                else:
                    break
            position = end
    return {
        "sample_rate": rate,
        "frames": frames,
        "duration_s": frames / rate,
        "points": count,
        "channels": [
            {"min": minima[:, c].tolist(), "max": maxima[:, c].tolist()} for c in range(channels)
        ],
    }


def waveform(path: Path, points: int) -> dict:
    stat = path.stat()
    try:
        return _peaks(str(path), stat.st_mtime_ns, stat.st_size, points)
    except (sf.LibsndfileError, ValueError, OSError) as exc:
        raise HTTPException(422, "Audio could not be decoded for waveform peaks.") from exc
