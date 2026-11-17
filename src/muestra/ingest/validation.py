"""Offline beat evidence and audition clicks; optional independent beat annotations.

uv run --extra ingest python -m muestra.ingest.validation recording.mp3 --output OUT
Annotations JSON: {"beats_s": [...], "downbeats_s": [...]} via --annotations FILE.
Unannotated onset agreement is a diagnostic, NEVER reported as beat accuracy.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import find_peaks

from .analysis import meter_consistency, neural_beats, validate_grid
from .audio import decode, stereo
from .storage import read_json, write_json


def matching_score(reference, estimated, tolerance=0.07) -> dict:
    """One-to-one chronological F1 at ±70 ms, no double-counted matches."""
    reference = np.sort(reference)
    estimated = np.sort(estimated)
    i = j = matches = 0
    offsets = []
    while i < len(reference) and j < len(estimated):
        delta = estimated[j] - reference[i]
        if abs(delta) <= tolerance:
            matches += 1
            offsets.append(abs(float(delta)))
            i += 1
            j += 1
        elif delta < 0:
            j += 1
        else:
            i += 1
    precision = matches / max(len(estimated), 1)
    recall = matches / max(len(reference), 1)
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / max(precision + recall, 1e-12),
        "median_abs_offset_ms": float(np.median(offsets) * 1000) if offsets else None,
        "reference_count": len(reference),
        "estimated_count": len(estimated),
    }


def onset_agreement(audio, sr, beats) -> dict:
    hop = max(1, round(sr * 0.01))
    mono = audio.mean(axis=1)
    length = len(mono) // hop
    rms = np.sqrt(np.mean(mono[: length * hop].reshape(length, hop) ** 2, axis=1))
    novelty = np.maximum(np.diff(rms), 0)
    peaks, _ = find_peaks(novelty, distance=10, prominence=max(float(np.std(novelty) * 0.5), 1e-8))
    times = (peaks + 1) * hop / sr
    if not len(times):
        return {"fraction_near_onset_70ms": 0, "median_nearest_onset_ms": None}
    offsets = np.array([np.min(abs(times - beat)) for beat in beats])
    return {
        "fraction_near_onset_70ms": float(np.mean(offsets <= 0.07)),
        "median_nearest_onset_ms": float(np.median(offsets) * 1000),
    }


def click_overlay(audio, sr, beats, downbeats):
    result = stereo(audio).copy() * 0.65
    for beat in beats:
        down = bool(np.any(abs(downbeats - beat) < 0.01))
        frequency = 1500 if down else 1000
        count = min(round(sr * 0.03), len(result) - round(beat * sr))
        if count <= 0:
            continue
        t = np.arange(count) / sr
        click = 0.25 * np.sin(2 * np.pi * frequency * t) * np.exp(-t * 100)
        start = round(beat * sr)
        result[start : start + count] += click[:, None]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annotations", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for path in args.audio:
        audio, sr = decode(path)
        started = time.perf_counter()
        beats, downbeats = validate_grid(*neural_beats(audio, sr), len(audio) / sr)
        intervals = np.diff(beats)
        result = {
            "file": str(path),
            "duration_s": len(audio) / sr,
            "analysis_s": time.perf_counter() - started,
            "bpm_median": float(60 / np.median(intervals)),
            "four_beat_bar_consistency": meter_consistency(beats, downbeats),
            "interval_p10_p90_s": np.percentile(intervals, [10, 90]).tolist(),
            "beats_s": beats.tolist(),
            "downbeats_s": downbeats.tolist(),
            "onset_diagnostic_not_accuracy": onset_agreement(audio, sr, beats),
        }
        if args.annotations:
            reference = read_json(args.annotations)
            result["beat_accuracy"] = matching_score(reference["beats_s"], beats)
            result["downbeat_accuracy"] = matching_score(reference["downbeats_s"], downbeats)
        sf.write(
            args.output / f"{path.stem}-grid.wav",
            click_overlay(audio, sr, beats, downbeats),
            sr,
            subtype="PCM_24",
        )
        write_json(args.output / f"{path.stem}.json", result)
        results.append(result)
    write_json(args.output / "summary.json", {"results": results})


if __name__ == "__main__":
    main()
