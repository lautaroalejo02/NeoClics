"""Conservative local grid evidence, always derived from the full-file grid.

Reliability means meter/interval consistency, not verified musical beat accuracy.
Reconstructed bars must also have support in the raw tracker downbeats.
"""

from __future__ import annotations

import numpy as np

from ..schemas.analysis import Analysis, GridBar, GridRegion, GridReliability
from .pulse import PulseEvidence


def assess_reliability(analysis: Analysis, pulse: PulseEvidence | None = None) -> GridReliability:
    if len(analysis.beats_s) < 2 or len(analysis.downbeats_s) < 2:
        return GridReliability(
            confidence=0,
            regions=[
                GridRegion(
                    start_s=0,
                    end_s=analysis.duration_s,
                    reliable=False,
                    reason="No se detectó ningún compás completo; sin grilla fiable.",
                )
            ],
            hint="Audio analizado, sin grilla fiable. source.preview_grid permite escuchar "
            "una ventana; source.find_chops no inventa pulsos ni propone cortes sin grilla.",
        )
    beats = np.asarray(analysis.beats_s)
    downs = analysis.downbeats_s
    indices = np.asarray([int(np.argmin(abs(beats - d))) for d in downs])
    correction = analysis.grid
    bpb = correction.beats_per_bar if correction else 4
    raw = correction.raw if correction else analysis
    raw_beats = np.asarray(raw.beats_s)
    raw_indices = np.asarray([int(np.argmin(abs(raw_beats - d))) for d in raw.downbeats_s])
    factor = correction.tempo_factor if correction else 1
    allowed = {round(bpb / factor)}
    if factor == 0.5:
        allowed.add(bpb)  # two tracked half-bars can support one corrected bar
    bars = []
    for k, (i, j) in enumerate(zip(indices, indices[1:], strict=False)):
        start, end = downs[k : k + 2]
        intervals = np.diff(beats[i : j + 1])
        stable = bool(len(intervals) and max(intervals) / min(intervals) <= 1.4)
        supported = all(
            int(q - p) in allowed
            for a, b, p, q in zip(
                raw.downbeats_s, raw.downbeats_s[1:], raw_indices, raw_indices[1:], strict=False
            )
            if a < end - 0.05 and b > start + 0.05
        )
        audible = pulse is None or pulse.supports(beats, start, end)
        reliable = int(j - i) == bpb and stable and supported and audible
        reason = (
            "Compás consistente; confirmar el uno de oído."
            if reliable
            else "Compás irregular o sin respaldo del tracker."
            if int(j - i) != bpb or not supported
            else "Saltos locales de tempo; revisar los clicks."
        )
        if not audible:
            reason = "Sin ataques regulares suficientes sobre los beats; pulso no verificado."
        bars.append(
            GridBar(
                start_s=start,
                end_s=end,
                reliable=reliable,
                reason=reason,
                start_downbeat=k + 1,
                beat_count=int(j - i),
            )
        )
    regions = []
    spans = []
    if downs[0] > 0:
        spans.append(
            GridRegion(
                start_s=0,
                end_s=downs[0],
                reliable=False,
                reason="Antes del primer downbeat: sin compás completo.",
            )
        )
    spans.extend(
        GridRegion(**bar.model_dump(exclude={"start_downbeat", "beat_count"})) for bar in bars
    )
    if downs[-1] < analysis.duration_s:
        spans.append(
            GridRegion(
                start_s=downs[-1],
                end_s=analysis.duration_s,
                reliable=False,
                reason="Final sin compás completo.",
            )
        )
    for span in spans:
        if regions and regions[-1].reliable == span.reliable and regions[-1].end_s == span.start_s:
            regions[-1].end_s = span.end_s
            if regions[-1].reason != span.reason:
                regions[-1].reason = "Grilla dudosa; ver el detalle por compás."
        else:
            regions.append(span)
    confidence = sum(bar.reliable for bar in bars) / len(bars) if bars else 0
    reliable = sum(r.reliable for r in regions)
    hint = (
        f"{confidence:.0%} de compases consistentes; {reliable} regiones fiables. "
        "source.find_chops usa esas regiones por defecto (allow_unreliable=true para "
        "explorar otras). Confirmá con source.preview_grid(window_s=[inicio, fin]); "
        "no hace falta separar para analizar ni reanalizar al separar."
    )
    return GridReliability(confidence=confidence, bars=bars, regions=regions, hint=hint)


def reliable_window(analysis: Analysis, start: float, end: float) -> bool:
    report = analysis.reliability or assess_reliability(analysis)
    return any(
        r.reliable and r.start_s <= start + 1e-6 and r.end_s >= end - 1e-6 for r in report.regions
    )


def chop_warning(analysis: Analysis, start: float, end: float) -> str | None:
    if not reliable_window(analysis, start, end):
        return (
            f"Grilla dudosa entre {start:.2f} y {end:.2f} s: confirmá con "
            f"source.preview_grid(window_s=[{start:.2f}, {end:.2f}])."
        )
    return None
