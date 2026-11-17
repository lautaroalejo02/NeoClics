"""Neural, variable-tempo beat grid plus conservative portable descriptors."""

from __future__ import annotations

import os
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version

import numpy as np
from scipy.signal import resample_poly, stft

from ..errors import MuestraError
from ..project import Project, load_analysis
from ..schemas.analysis import Analysis, KeyEstimate, Section, Stems
from .audio import decode
from .separation import Progress, report
from .storage import digest, project_lock, read_json, source_file, write_json

ANALYSIS_METHOD = "beat-this-final0-minimal+chroma-ks+novelty-v2"
BeatTracker = Callable[[np.ndarray, int], tuple[np.ndarray, np.ndarray]]


def neural_beats(audio: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    try:
        import torch
        from beat_this.inference import Audio2Beats
    except ImportError as exc:
        raise MuestraError.one(
            "missing_dependency",
            f"Beat tracking requires beat-this: {exc}",
            hint="Run uv sync; the official final0 MIT model downloads on first use.",
        ) from exc
    torch.set_num_threads(max(1, int(os.environ.get("MUESTRA_CPU_THREADS", "2"))))
    tracker = Audio2Beats(checkpoint_path="final0", device="cpu", float16=False, dbn=False)
    return tracker(audio, sr)


def validate_grid(beats, downbeats, duration: float) -> tuple[np.ndarray, np.ndarray]:
    beats = np.asarray(beats, dtype=float)
    downbeats = np.asarray(downbeats, dtype=float)
    if (
        beats.ndim != 1
        or downbeats.ndim != 1
        or len(beats) < 2
        or not len(downbeats)
        or not np.isfinite(beats).all()
        or not np.isfinite(downbeats).all()
        or np.any(np.diff(beats) <= 0)
        or np.any(np.diff(downbeats) <= 0)
        or beats[0] < 0
        or beats[-1] > duration
        or downbeats[0] < 0
        or downbeats[-1] > duration
    ):
        raise MuestraError.one(
            "grid_mismatch",
            "The tracker did not produce a usable beat/downbeat grid.",
            hint="Try a longer rhythmic excerpt; free-time intros and noisy records need "
            "human grid verification. No uniform-tempo fallback is applied.",
        )
    indices = np.array([np.argmin(abs(beats - b)) for b in downbeats])
    if np.any(abs(beats[indices] - downbeats) > 0.05) or len(set(indices)) != len(indices):
        raise MuestraError.one("grid_mismatch", "Downbeats do not match distinct tracked beats.")
    # Store one common grid so chop timing and the renderer agree sample-for-sample.
    return beats, beats[indices]


def spectral_features(audio: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Downsampled STFT and chroma; no platform-specific native MIR dependencies."""
    from math import gcd

    mono = audio.mean(axis=1)
    target = 11025
    divisor = gcd(sr, target)
    mono = resample_poly(mono, target // divisor, sr // divisor)
    # A short valid input still gets a usable FFT.
    if len(mono) < 4096:
        mono = np.pad(mono, (0, 4096 - len(mono)))
    frequencies, times, spectrum = stft(mono, fs=target, nperseg=4096, noverlap=3072)
    magnitude = np.abs(spectrum).astype(np.float64)
    valid = (frequencies >= 55) & (frequencies <= 4000)
    notes = 69 + 12 * np.log2(frequencies[valid] / 440)
    # Estimate global tuning from strong bins; accommodates tape-speed pitch offsets.
    weights = magnitude[valid].sum(axis=1)
    tuning = np.angle(np.sum(weights * np.exp(2j * np.pi * notes))) / (2 * np.pi)
    pitch_classes = np.round(notes - tuning).astype(int) % 12
    chroma = np.zeros((12, magnitude.shape[1]))
    for pc in range(12):
        chroma[pc] = magnitude[valid][pitch_classes == pc].sum(axis=0)
    return times, magnitude, chroma


def meter_consistency(beats, downbeats) -> float | None:
    """Fraction of inferred bars containing exactly four beat intervals (v0 meter)."""
    indices = [int(np.argmin(abs(np.asarray(beats) - beat))) for beat in downbeats]
    return float(np.mean(np.diff(indices) == 4)) if len(indices) > 1 else None


def estimate_key(chroma: np.ndarray) -> KeyEstimate | None:
    profile = chroma.sum(axis=1)
    if np.linalg.norm(profile) < 1e-8 or np.std(profile) < 1e-8:
        return None
    templates = {
        "major": [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
        "minor": [6.33, 2.68, 3.52, 5.38, 2.6, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
    }
    estimates = sorted(
        [
            (float(np.corrcoef(profile, np.roll(template, tonic))[0, 1]), tonic, mode)
            for mode, template in templates.items()
            for tonic in range(12)
        ],
        reverse=True,
    )
    best, tonic, mode = estimates[0]
    # Confidence expresses profile correlation AND margin, not a calibrated probability.
    confidence = np.clip(max(0, best) * min(1, (best - estimates[1][0]) / 0.2), 0, 1)
    return KeyEstimate(
        tonic=["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"][tonic],
        mode=mode,
        confidence=float(confidence),
    )


def detect_sections(
    times: np.ndarray,
    spectrum: np.ndarray,
    chroma: np.ndarray,
    downbeats: np.ndarray,
    duration: float,
) -> list[Section]:
    """Novelty boundaries on downbeats, >=8 bars apart; labels make no verse/chorus claim."""
    energy = np.log1p(spectrum.sum(axis=0) * 100)
    features = np.vstack([chroma / np.maximum(chroma.sum(axis=0), 1e-12), energy[None]])
    features = (features - features.mean(axis=1, keepdims=True)) / np.maximum(
        features.std(axis=1, keepdims=True), 1e-6
    )
    novelty = []
    for i, boundary in enumerate(downbeats):
        width = 4 * np.median(np.diff(downbeats)) if len(downbeats) > 1 else 8
        left = (times >= boundary - width) & (times < boundary)
        right = (times >= boundary) & (times < boundary + width)
        value = (
            float(np.linalg.norm(features[:, left].mean(axis=1) - features[:, right].mean(axis=1)))
            if left.any() and right.any()
            else 0
        )
        novelty.append((value, i, float(boundary)))
    values = np.array([x[0] for x in novelty])
    threshold = float(np.median(values) + 1.5 * np.std(values))
    accepted = []
    for value, index, boundary in sorted(novelty, reverse=True):
        if (
            value <= max(threshold, 0.5)
            or index < 8
            or len(downbeats) - index < 8
            or any(abs(index - old_index) < 8 for old_index, _ in accepted)
        ):
            continue
        accepted.append((index, boundary))
    bounds = [0.0, *(b for _, b in sorted(accepted)), duration]
    return [
        Section(label="intro" if i == 0 else f"section_{i + 1:02d}", start_s=a, end_s=b)
        for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False))
    ]


def integrated_loudness(audio: np.ndarray, sr: int) -> float | None:
    try:
        import pyloudnorm as pyln
    except ImportError as exc:
        raise MuestraError.one(
            "missing_dependency", "LUFS needs pyloudnorm.", hint="Run uv sync."
        ) from exc
    if len(audio) < sr * 0.4:
        return None
    value = float(pyln.Meter(sr).integrated_loudness(audio))
    return value if np.isfinite(value) else None


def active_stems(project: Project, source_id: str, content_hash: str) -> dict:
    path = project.path(f"cache/stems/{source_id}/active.json")
    if path.is_file():
        data = read_json(path)
        if data.get("source_sha256") == content_hash and all(
            project.path(p).is_file() for p in data["stems"].values()
        ):
            return data
    return {}


def analyze(
    project: Project,
    source_id: str,
    *,
    tracker: BeatTracker | None = None,
    progress: Progress | None = None,
) -> Analysis:
    rel, path = source_file(project, source_id)
    content_hash = digest(path)
    try:
        tracker_version = version("beat-this")
    except PackageNotFoundError:
        tracker_version = "unavailable"
    identity = f"{content_hash}:{ANALYSIS_METHOD}:{tracker_version}"
    cache = project.path(f"cache/analysis/{source_id}.json")
    meta = cache.with_suffix(".meta.json")
    with project_lock(project):
        separated = active_stems(project, source_id, content_hash)
        if cache.exists() and meta.exists() and read_json(meta).get("identity") == identity:
            analysis = load_analysis(project, source_id)
            analysis.stems = Stems(**separated["stems"]) if separated else None
            analysis.separation_model = separated.get("model")
            write_json(cache, analysis.model_dump(mode="json"))
            report(progress, 1, "Analysis ready (cache)")
            return analysis
        report(progress, 0.05, "Decoding source")
        audio, sr = decode(path)
        duration = len(audio) / sr
        if np.max(abs(audio)) < 1e-7:
            raise MuestraError.one("grid_mismatch", "Silent audio has no reliable beat grid.")
        report(progress, 0.15, "Tracking beats and downbeats with Beat This! final0")
        beats, downbeats = validate_grid(*(tracker or neural_beats)(audio, sr), duration)
        consistency = meter_consistency(beats, downbeats)
        if consistency is not None and consistency < 0.75:
            raise MuestraError.one(
                "grid_mismatch",
                f"Only {consistency:.0%} of inferred bars contain four beats; "
                "the recording's meter or downbeat grid is uncertain.",
                hint="Muestra v0 requires 4/4. Audition the click overlay with "
                "muestra.ingest.validation; use a verified 4/4 excerpt. "
                "No speculative grid was cached.",
            )
        intervals = np.diff(beats)
        bpm = float(60 / np.median(intervals))
        if not 0 < bpm < 400:
            raise MuestraError.one("grid_mismatch", f"Tracked BPM {bpm:.1f} is out of range.")
        # Robust interval spread avoids flagging a single missed beat as tempo drift.
        spread = (np.percentile(intervals, 90) - np.percentile(intervals, 10)) / np.median(
            intervals
        )
        report(progress, 0.75, "Estimating key, sections and integrated loudness")
        times, spectrum, chroma = spectral_features(audio, sr)
        analysis = Analysis(
            source_id=source_id,
            file=rel,
            duration_s=duration,
            sample_rate=sr,
            bpm_global=bpm,
            tempo_variable=bool(spread > 0.04),
            beats_s=beats.tolist(),
            downbeats_s=downbeats.tolist(),
            key=estimate_key(chroma),
            sections=detect_sections(times, spectrum, chroma, downbeats, duration),
            loudness_lufs=integrated_loudness(audio, sr),
            stems=Stems(**separated["stems"]) if separated else None,
            separation_model=separated.get("model"),
        )
        write_json(cache, analysis.model_dump(mode="json"))
        write_json(
            meta,
            {
                "identity": identity,
                "method": ANALYSIS_METHOD,
                "tempo_interval_spread": float(spread),
                "four_beat_bar_consistency": consistency,
            },
        )
        report(progress, 1, "Analysis ready; audition downbeats on old or noisy records")
        return analysis
