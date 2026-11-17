"""Metrics delta between two versions, in Spanish (WP5).

Uses the ears package's structured comparison (``MetricsDelta``: loudness,
brightness, low end, per-track levels, alarms fixed/introduced) and its
Spanish notes; the local description is only a fallback for metrics files
ears cannot compare.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..schemas.metrics import Metrics, MetricsDelta

if TYPE_CHECKING:
    from pathlib import Path

    from ..project.core import Project

__all__ = ["compare_metrics", "describe_metrics_delta", "ears_delta", "load_metrics"]


def load_metrics(project: Project, version: str) -> Metrics | None:
    """``renders/<version>/metrics.json`` parsed; None when missing or invalid
    (metrics are derived data and never block a diff)."""
    return read_metrics_file(project.renders_dir / version / "metrics.json")


def read_metrics_file(path: Path) -> Metrics | None:
    try:
        return Metrics.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError):
        return None


def _n(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".") if abs(v) >= 0.05 else "0"


def describe_metrics_delta(a: Metrics, b: Metrics) -> list[str]:
    """Spanish phrases (lower-case, no final period) for what moved between
    two metrics files; empty when nothing moved noticeably."""
    out: list[str] = []
    d = b.loudness.integrated_lufs - a.loudness.integrated_lufs
    if abs(d) >= 0.5:
        out.append(f"el mix quedó {_n(abs(d))} LU más {'fuerte' if d > 0 else 'suave'}")
    tp = b.loudness.true_peak_dbtp - a.loudness.true_peak_dbtp
    if abs(tp) >= 1.0:
        out.append(f"el pico real {'subió' if tp > 0 else 'bajó'} {_n(abs(tp))} dB")
    if b.loudness.clipping and not a.loudness.clipping:
        out.append("ahora hay clipping")
    elif a.loudness.clipping and not b.loudness.clipping:
        out.append("ya no hay clipping")
    more: list[str] = []
    less: list[str] = []
    for key, word in (("low_db", "graves"), ("mid_db", "medios"), ("high_db", "agudos")):
        delta = getattr(b.spectral.mix, key) - getattr(a.spectral.mix, key)
        if delta >= 1.0:
            more.append(word)
        elif delta <= -1.0:
            less.append(word)
    if more:
        out.append("más energía en " + _join(more))
    if less:
        out.append("menos energía en " + _join(less))
    dom_a, dom_b = a.low_end_masking.dominant_track, b.low_end_masking.dominant_track
    if dom_b and dom_a != dom_b:
        out.append(f"el que manda en el rango de graves ahora es '{dom_b}'")
    if b.clicks.count > a.clicks.count:
        n = b.clicks.count - a.clicks.count
        out.append(f"aparecieron {n} {'click' if n == 1 else 'clicks'}")
    elif b.clicks.count < a.clicks.count:
        n = a.clicks.count - b.clicks.count
        out.append(f"{n} {'click' if n == 1 else 'clicks'} menos")
    off = b.alignment.max_abs_offset_ms - a.alignment.max_abs_offset_ms
    if abs(off) >= 5.0:
        out.append(
            f"el desfase máximo con la grilla {'creció' if off > 0 else 'bajó'} a "
            f"{_n(b.alignment.max_abs_offset_ms)} ms"
        )
    if a.tonal.in_key is False and b.tonal.in_key is True:
        out.append("el mix volvió a la tonalidad")
    elif a.tonal.in_key is True and b.tonal.in_key is False:
        out.append("el mix se salió de la tonalidad")
    new_alarms = {x.code for x in b.alarms} - {x.code for x in a.alarms}
    if new_alarms:
        out.append("alarmas nuevas: " + ", ".join(sorted(new_alarms)))
    return out


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" y {items[-1]}"


def ears_delta(a: Metrics | None, b: Metrics | None) -> MetricsDelta | None:
    """The ears package's structured delta (WP6 ``ears.report.compare_metrics``);
    None when either side has no metrics or ears cannot compare them."""
    if a is None or b is None:
        return None
    try:
        from ..ears.report import compare_metrics as ears_compare
    except ImportError:  # pragma: no cover - ears is part of the base install
        return None
    try:
        return ears_compare(a, b)
    except Exception:  # noqa: BLE001 - a broken metrics file never blocks a diff
        return None


def compare_metrics(a: Metrics | None, b: Metrics | None) -> list[str]:
    """Delta phrases for two metrics (either may be None -> nothing): the ears
    notes when ears can compare them, else the local description."""
    if a is None or b is None:
        return []
    delta = ears_delta(a, b)
    return delta.notes if delta is not None else describe_metrics_delta(a, b)
