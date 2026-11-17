"""Sample-position automation; duplicate points are right-continuous jumps."""

from __future__ import annotations

import numpy as np

from ..schemas.song import BEATS_PER_BAR, Song


class Automation:
    def __init__(self, song: Song, sample_rate: int):
        self.curves = {a.target: a for a in song.automation}
        self.frames_per_bar = BEATS_PER_BAR * 60 / song.meta.bpm * sample_rate

    def has(self, prefix: str) -> bool:
        return any(k == prefix or k.startswith(prefix + ".") for k in self.curves)

    def value(self, target: str, frames: np.ndarray | float, default: float):
        a = self.curves.get(target)
        if a is None:
            return default
        points = np.asarray(a.points, dtype=np.float64)
        positions = (points[:, 0] - 1) * self.frames_per_bar
        values = points[:, 1]
        indices = np.searchsorted(positions, frames, side="right") - 1
        left = np.clip(indices, 0, len(values) - 1)
        if a.curve == "step":
            return values[left]
        right = np.minimum(left + 1, len(values) - 1)
        span = positions[right] - positions[left]
        amount = np.clip((frames - positions[left]) / np.where(span > 0, span, 1), 0, 1)
        if a.curve == "exp":
            return values[left] * np.exp(np.log(values[right] / values[left]) * amount)
        return values[left] + (values[right] - values[left]) * amount


def pan_gain(audio: np.ndarray, gain_db, pan) -> np.ndarray:
    """Stereo balance with a -3 dB equal-power centre (no channel crossfeed)."""
    gain = np.power(10.0, np.asarray(gain_db) / 20)
    angle = (np.asarray(pan) + 1) * np.pi / 4
    scales = np.stack((np.cos(angle) * gain, np.sin(angle) * gain), axis=-1)
    return (audio * scales).astype(np.float32)
