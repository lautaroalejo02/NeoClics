"""Cheap audio evidence independent of neural meter labels; never a beat tracker."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from math import gcd

import numpy as np
from scipy.signal import find_peaks, resample_poly, stft

QUALITY_VERSION = "onset-parity-pulse-v2"


@dataclass
class PulseEvidence:
    step_s: float
    values: np.ndarray

    def at(self, beats, tolerance=0.07) -> np.ndarray:
        values = []
        for beat in beats:
            first = max(0, int(np.floor((beat - tolerance) / self.step_s)))
            last = min(len(self.values), int(np.ceil((beat + tolerance) / self.step_s)) + 1)
            values.append(float(np.max(self.values[first:last], initial=0)))
        return np.asarray(values)

    @cached_property
    def onset_times(self) -> np.ndarray:
        threshold = max(0.12, float(np.percentile(self.values, 90)) * 0.2)
        peaks, _ = find_peaks(
            self.values,
            height=threshold,
            prominence=threshold * 0.5,
            distance=max(1, round(0.08 / self.step_s)),
        )
        return peaks * self.step_s

    def supports(self, beats, start, end) -> bool:
        beats = np.asarray(beats)
        inside = beats[(beats >= start - 1e-6) & (beats < end - 1e-6)]
        if len(inside) < 2:
            return False
        times = self.onset_times
        times = times[(times >= start - 0.07) & (times < end)]
        matched = sum(np.any(abs(times - beat) <= 0.07) for beat in inside)
        return matched >= max(2, int(np.ceil(len(inside) * 0.4)))

    def to_dict(self) -> dict:
        return {"step_s": self.step_s, "values": self.values.round(6).tolist()}

    @classmethod
    def from_dict(cls, data):
        return cls(data["step_s"], np.asarray(data["values"]))


def pulse_evidence(audio: np.ndarray, sr: int) -> PulseEvidence:
    target = 11025
    divisor = gcd(sr, target)
    mono = resample_poly(audio.mean(axis=1), target // divisor, sr // divisor)
    if len(mono) < 1024:
        mono = np.pad(mono, (0, 1024 - len(mono)))
    frequencies, _, spectrum = stft(mono, fs=target, nperseg=1024, noverlap=914)
    magnitude = abs(spectrum)
    flux = np.maximum(np.diff(magnitude, axis=1, prepend=magnitude[:, :1]), 0)
    envelopes = []
    global_floor = max(1e-6, float(np.mean(magnitude.sum(axis=0))) * 0.01)
    for lower, upper in ((35, 180), (180, 4000)):
        selected = (frequencies >= lower) & (frequencies < upper)
        envelope = flux[selected].sum(axis=0)
        floor = global_floor
        scale = max(floor, float(np.percentile(envelope, 95)))
        envelopes.append(envelope / scale)
    return PulseEvidence(110 / target, np.mean(envelopes, axis=0))


def supported_half_phase(strength, fallback: int) -> tuple[int, bool]:
    strength = np.asarray(strength)
    if len(strength) < 16:
        return fallback, False
    scores = [float(np.mean(strength[phase::2])) for phase in (0, 1)]
    best = int(np.argmax(scores))
    supported = scores[best] >= 0.2 and scores[best] > 1.5 * max(scores[1 - best], 1e-9)
    return (best, True) if supported else (fallback, False)
