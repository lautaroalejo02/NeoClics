"""Beat-grid correction at the source-analysis level (WP12).

Beat trackers on old records often count eighth notes (double tempo),
quarter notes of a half-time feel (half tempo), or put the "one" on the
wrong beat. Those are properties of the ANALYSIS, not of each chop, so the
fix lives here: ``analysis.json`` keeps the tracker's raw output under
``grid.raw`` and the EFFECTIVE grid in ``beats_s``/``downbeats_s``/
``bpm_global``, which every consumer (chop timing, warp, previews, ears)
already reads. Corrections are absolute w.r.t. the raw grid, so they are
reversible and idempotent.

Pure functions only here (no project I/O) except :func:`regrid`,
:func:`apply_auto` and :func:`preview_grid`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from ..errors import MuestraError
from ..schemas.analysis import (
    TEMPO_FACTORS,
    Analysis,
    GridAlternative,
    GridCorrection,
    GridStep,
    RawGrid,
)

DEFAULT_TEMPO_RANGE = (70.0, 160.0)
#: future-funk sources usually sit here; used to break ties between octaves.
SWEET_SPOT = (90.0, 135.0)


# ---------------------------------------------------------------------------
# pure grid math
# ---------------------------------------------------------------------------


def grid_bpm(beats: np.ndarray) -> float:
    return float(60 / np.median(np.diff(beats)))


def tempo_variable(beats: np.ndarray) -> bool:
    intervals = np.diff(beats)
    spread = (np.percentile(intervals, 90) - np.percentile(intervals, 10)) / np.median(intervals)
    return bool(spread > 0.04)


def default_beat_phase(raw_beats: np.ndarray, raw_downbeats: np.ndarray) -> int:
    """Parity of tracked beats that holds most tracked downbeats (kept when halving)."""
    idx = np.array([int(np.argmin(abs(raw_beats - d))) for d in raw_downbeats])
    odd = int(np.count_nonzero(idx % 2))
    return 1 if odd > len(idx) - odd else 0


def derive_beats(raw_beats: np.ndarray, factor: float, beat_phase: int = 0) -> np.ndarray:
    raw_beats = np.asarray(raw_beats, dtype=float)
    if factor == 1.0:
        return raw_beats.copy()
    if factor == 0.5:
        return raw_beats[beat_phase::2].copy()
    if factor == 2.0:
        out = np.empty(2 * len(raw_beats) - 1)
        out[0::2] = raw_beats
        out[1::2] = (raw_beats[:-1] + raw_beats[1:]) / 2
        return out
    raise ValueError(f"unsupported tempo factor {factor}")


def _anchors(new_beats: np.ndarray, raw_downbeats: np.ndarray) -> np.ndarray:
    """Tracked downbeats mapped to the nearest corrected beat index (sorted, unique)."""
    idx = [int(np.argmin(abs(new_beats - d))) for d in raw_downbeats]
    return np.unique(np.asarray(idx, dtype=int))


def derive_downbeat_indices(
    new_beats: np.ndarray,
    raw_downbeats: np.ndarray,
    *,
    shift: int = 0,
    beats_per_bar: int = 4,
) -> np.ndarray:
    """Corrected downbeat indices into ``new_beats``.

    When the tracked bars already have ``beats_per_bar`` corrected beats
    (tempo factor 1 on a 4/4 reading) the tracker's own downbeats are kept,
    shifted by ``shift`` beats: local irregularities survive. Otherwise
    (half/double tempo, 2-beat bars) bars are rebuilt every
    ``beats_per_bar`` beats, phase-locked to the tracked downbeats; a new
    phase is adopted only when two consecutive tracked downbeats agree on
    it (a real dropped or added beat), never on one stray downbeat.
    """
    n = len(new_beats)
    anchors = _anchors(new_beats, raw_downbeats)
    if not len(anchors):
        return anchors
    spacing = np.diff(anchors)
    native = len(spacing) == 0 or float(np.mean(spacing == beats_per_bar)) >= 0.75
    if native:
        out = anchors + shift
    else:
        segments: list[tuple[int, int]] = [(int(anchors[0]), int(anchors[0]) % beats_per_bar)]
        for i in range(1, len(anchors)):
            residue = int(anchors[i]) % beats_per_bar
            if (
                residue != segments[-1][1]
                and i + 1 < len(anchors)
                and int(anchors[i + 1]) % beats_per_bar == residue
            ):
                segments.append((int(anchors[i]), residue))
        built: list[int] = []
        for k, (start, _) in enumerate(segments):
            end = segments[k + 1][0] if k + 1 < len(segments) else n
            built.extend(range(start, end, beats_per_bar))
        out = np.asarray(built, dtype=int) + shift
    out = out[(out >= 0) & (out < n)]
    return np.unique(out)


def derive_grid(
    raw: RawGrid,
    *,
    tempo_factor: float = 1.0,
    beat_phase: int = 0,
    downbeat_shift_beats: int = 0,
    beats_per_bar: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """Effective (beats_s, downbeats_s) for a correction of the raw grid."""
    raw_beats = np.asarray(raw.beats_s, dtype=float)
    raw_downbeats = np.asarray(raw.downbeats_s, dtype=float)
    beats = derive_beats(raw_beats, tempo_factor, beat_phase)
    if len(beats) < 2:
        raise MuestraError.one(
            "grid_mismatch",
            "The corrected grid has fewer than two beats.",
            hint="Use a longer source or keep tempo_factor 1.",
        )
    if tempo_factor == 1.0 and beats_per_bar == 4:
        # Preserve local meter breaks; rebuilding would hide unreliable regions.
        idx = _anchors(beats, raw_downbeats) + downbeat_shift_beats
        idx = idx[(idx >= 0) & (idx < len(beats))]
    else:
        idx = derive_downbeat_indices(
            beats, raw_downbeats, shift=downbeat_shift_beats, beats_per_bar=beats_per_bar
        )
    if not len(idx):
        raise MuestraError.one(
            "grid_mismatch",
            "The corrected grid has no downbeat left.",
            hint="Use a smaller downbeat_shift_beats.",
        )
    return beats, beats[idx]


# ---------------------------------------------------------------------------
# automatic octave choice + honest confidence
# ---------------------------------------------------------------------------


def _in(bpm: float, lo: float, hi: float) -> bool:
    return lo <= bpm <= hi


def choose_factor(raw_bpm: float, tempo_range: tuple[float, float]) -> float:
    """Tempo octave that falls in range (raw first), preferring the sweet spot."""
    lo, hi = tempo_range
    fitting = [f for f in TEMPO_FACTORS if _in(raw_bpm * f, lo, hi)]
    if not fitting:
        return 1.0
    if 1.0 in fitting:
        return 1.0
    sweet = [f for f in fitting if _in(raw_bpm * f, *SWEET_SPOT)]
    return (sweet or fitting)[0]


def _bpm_text(bpm: float) -> str:
    return f"{bpm:.1f}".rstrip("0").rstrip(".")


def assess(
    raw: RawGrid,
    *,
    origin: str,
    tempo_factor: float,
    beat_phase: int,
    downbeat_shift_beats: int,
    beats_per_bar: int,
    tempo_range: tuple[float, float],
    meter_consistency: float | None,
    history: list[GridStep] | None = None,
    note: str = "",
) -> GridCorrection:
    """Build the GridCorrection record: confidence, alternatives, Spanish reason."""
    lo, hi = tempo_range
    raw_bpm = raw.bpm_global
    bpm = raw_bpm * tempo_factor
    in_range = [f for f in TEMPO_FACTORS if _in(raw_bpm * f, lo, hi)]
    # Tempo octave confidence (heuristic, documented in docs/ingest.md).
    if tempo_factor not in in_range:
        tempo_conf = 0.25
    elif len(in_range) > 1:
        tempo_conf = 0.5  # two octaves fit the range: genuinely ambiguous
    elif tempo_factor == 1.0:
        tempo_conf = 0.85
    else:
        tempo_conf = 0.7  # tracker octave error corrected; the human should confirm
    if origin == "manual":
        tempo_conf = max(tempo_conf, 0.9)  # someone listened (or the agent asked)
    consistency = 1.0 if meter_consistency is None else float(meter_consistency)
    if tempo_factor == 0.5:
        # Tracked bars are half a real bar: nothing in the tracker output says
        # which of the two tracked downbeats is the real "one".
        bar_conf = 0.5
    else:
        bar_conf = round(min(1.0, max(0.0, consistency)), 3)
    if origin == "manual":
        bar_conf = max(bar_conf, 0.9)

    alternatives: list[GridAlternative] = []
    for f in TEMPO_FACTORS:
        if f == tempo_factor:
            continue
        b = raw_bpm * f
        what = {0.5: "mitad", 1.0: "como lo leyó el tracker", 2.0: "doble"}[f]
        out_of_range = "" if _in(b, lo, hi) else " (fuera del rango plausible)"
        alternatives.append(
            GridAlternative(
                tempo_factor=f,
                beat_phase=(
                    0
                    if f != 0.5
                    else beat_phase
                    if tempo_factor == 0.5
                    else default_beat_phase(np.asarray(raw.beats_s), np.asarray(raw.downbeats_s))
                ),
                bpm=round(b, 3),
                label=f"{_bpm_text(b)} BPM: {what}{out_of_range}",
            )
        )
    if tempo_factor == 0.5:
        alternatives.insert(
            0,
            GridAlternative(
                tempo_factor=0.5,
                beat_phase=beat_phase,
                downbeat_shift_beats=downbeat_shift_beats + 2,
                bpm=round(bpm, 3),
                label="mismo tempo, el uno dos tiempos más tarde (en el otro downbeat del tracker)",
            ),
        )
        alternatives.append(
            GridAlternative(
                tempo_factor=0.5,
                beat_phase=1 - beat_phase,
                downbeat_shift_beats=downbeat_shift_beats,
                bpm=round(bpm, 3),
                label="mismo tempo, pero los beats en los contratiempos (si los clicks "
                "caen entre los golpes)",
            ),
        )
    for delta in (1, -1):
        alternatives.append(
            GridAlternative(
                tempo_factor=tempo_factor,
                beat_phase=beat_phase,
                downbeat_shift_beats=downbeat_shift_beats + delta,
                bpm=round(bpm, 3),
                label=f"mismo tempo, el uno un tiempo {'más tarde' if delta > 0 else 'antes'}",
            )
        )

    raw_text = _bpm_text(raw_bpm)
    if origin == "tracker":
        reason = f"El tracker leyó {raw_text} BPM; se usa tal cual."
    elif tempo_factor == 1.0:
        reason = f"El tracker leyó {raw_text} BPM, dentro del rango {lo:g}-{hi:g}: sin cambio."
    elif tempo_factor == 0.5:
        reason = (
            f"El tracker leyó {raw_text} BPM (contó corcheas); "
            f"{'se tomó' if origin == 'auto' else 'tomé'} un beat de cada dos: "
            f"{_bpm_text(bpm)} BPM."
        )
    else:
        reason = (
            f"El tracker leyó {raw_text} BPM (medio tiempo); "
            f"{'se agregó' if origin == 'auto' else 'agregué'} un beat entre cada par: "
            f"{_bpm_text(bpm)} BPM."
        )
    if origin == "auto" and tempo_factor not in in_range:
        reason += f" Ninguna lectura (mitad, tal cual, doble) cae en {lo:g}-{hi:g} BPM."
    if downbeat_shift_beats:
        reason += (
            f" El uno se corrió {downbeat_shift_beats:+d} "
            f"{'tiempo' if abs(downbeat_shift_beats) == 1 else 'tiempos'} desde donde lo puso "
            "el tracker."
        )
    if beats_per_bar != 4:
        reason += f" Compases de {beats_per_bar} tiempos."
    if note:
        reason += " " + note
    ask = min(tempo_conf, bar_conf) < 0.6 or bool(note)
    if ask:
        reason += " Confirmalo de oído con source.preview_grid antes de armar el tema."
    return GridCorrection(
        origin=origin,
        tempo_factor=tempo_factor,
        beat_phase=beat_phase,
        downbeat_shift_beats=downbeat_shift_beats,
        beats_per_bar=beats_per_bar,
        tempo_range_bpm=(float(lo), float(hi)),
        raw=raw,
        tempo_confidence=round(tempo_conf, 3),
        bar_phase_confidence=round(bar_conf, 3),
        ask_human=ask,
        reason=reason,
        alternatives=alternatives,
        history=list(history or []),
    )


def meter_consistency(beats, downbeats, beats_per_bar: int = 4) -> float | None:
    """Fraction of bars that contain exactly ``beats_per_bar`` beats."""
    beats = np.asarray(beats)
    indices = [int(np.argmin(abs(beats - b))) for b in downbeats]
    if len(indices) < 2:
        return None
    return float(np.mean(np.diff(indices) == beats_per_bar))


def apply_correction(analysis: Analysis, correction: GridCorrection) -> Analysis:
    """Return ``analysis`` with its effective grid recomputed from ``correction.raw``."""
    beats, downbeats = derive_grid(
        correction.raw,
        tempo_factor=correction.tempo_factor,
        beat_phase=correction.beat_phase,
        downbeat_shift_beats=correction.downbeat_shift_beats,
        beats_per_bar=correction.beats_per_bar,
    )
    bpm = grid_bpm(beats)
    if not 0 < bpm < 400:
        raise MuestraError.one(
            "grid_mismatch",
            f"The corrected tempo {bpm:.1f} BPM is out of range.",
            hint="Use another tempo_factor.",
        )
    return analysis.model_copy(
        update={
            "beats_s": [float(b) for b in beats],
            "downbeats_s": [float(d) for d in downbeats],
            "bpm_global": bpm,
            "tempo_variable": tempo_variable(beats) if len(beats) > 2 else False,
            "grid": correction,
        }
    )


def raw_of(analysis: Analysis) -> RawGrid:
    if analysis.bpm_global is None or len(analysis.beats_s) < 2 or not analysis.downbeats_s:
        raise MuestraError.one(
            "grid_unavailable",
            "This source has no detected beat grid.",
            hint="Use source.preview_grid with window_s to listen; "
            "choose a rhythmic source with source.import and source.analyze.",
        )
    if analysis.grid is not None:
        return analysis.grid.raw
    return RawGrid(
        bpm_global=analysis.bpm_global,
        tempo_variable=analysis.tempo_variable,
        beats_s=list(analysis.beats_s),
        downbeats_s=list(analysis.downbeats_s),
    )


def auto_correct(
    analysis: Analysis,
    tempo_range: tuple[float, float] = DEFAULT_TEMPO_RANGE,
    *,
    apply: bool = True,
) -> Analysis:
    """Pick the tempo octave in ``tempo_range`` (``apply=False``: only report)."""
    raw = raw_of(analysis)
    raw_beats = np.asarray(raw.beats_s)
    factor = choose_factor(raw.bpm_global, tempo_range) if apply else 1.0
    phase = default_beat_phase(raw_beats, np.asarray(raw.downbeats_s)) if factor == 0.5 else 0
    consistency = meter_consistency(raw.beats_s, raw.downbeats_s)
    history = []
    if factor != 1.0:
        history.append(
            GridStep(at=datetime.now(UTC), origin="auto", tempo_factor=factor, beat_phase=phase)
        )
    note = ""
    suggested = choose_factor(raw.bpm_global, tempo_range)
    if not apply and suggested != 1.0:
        lo, hi = tempo_range
        note = (
            f"Está fuera de {lo:g}-{hi:g} BPM: probablemente "
            f"{_bpm_text(raw.bpm_global * suggested)}. Este análisis es anterior a la "
            "corrección automática y no se cambió para no mover los chops del tema: "
            f"corré source.regrid tempo_factor={suggested:g}."
        )
    correction = assess(
        raw,
        origin="auto" if apply else "tracker",
        tempo_factor=factor,
        beat_phase=phase,
        downbeat_shift_beats=0,
        beats_per_bar=4,
        tempo_range=tempo_range,
        meter_consistency=consistency,
        history=history,
        note=note,
    )
    return apply_correction(analysis, correction)


# ---------------------------------------------------------------------------
# chop remapping report
# ---------------------------------------------------------------------------


@dataclass
class ChopPlacement:
    start_downbeat: int
    start_offset_beats: float
    length_beats: float


def _clean(x: float, tol: float = 0.02) -> float:
    r = round(x)
    return float(r) if abs(x - r) < tol else round(float(x), 4)


def place_on_grid(analysis: Analysis, start_s: float, end_s: float) -> ChopPlacement | None:
    """Chop fields that cover [start_s, end_s) on ``analysis``'s grid (same audio)."""
    beats = np.asarray(analysis.beats_s)
    if start_s < beats[0] - 1e-6 or end_s > beats[-1] + 1e-6:
        return None
    positions = np.arange(len(beats), dtype=float)
    p = float(np.interp(start_s, beats, positions))
    q = float(np.interp(end_s, beats, positions))
    down_idx = np.array([int(np.argmin(abs(beats - d))) for d in analysis.downbeats_s])
    candidates = np.nonzero(down_idx <= p + 0.02)[0]
    k = int(candidates[-1]) if len(candidates) else 0
    offset = _clean(p - down_idx[k])
    length = _clean(q - p)
    if length <= 0 or length > 128:
        return None
    return ChopPlacement(start_downbeat=k + 1, start_offset_beats=offset, length_beats=length)


def nearest_downbeat(analysis: Analysis, t: float) -> int:
    return int(np.argmin(abs(np.asarray(analysis.downbeats_s) - t))) + 1


def speed_ratio(project_bpm: float, local_bpm: float) -> float:
    return project_bpm / local_bpm


def semitones(ratio: float) -> float:
    return 12 * math.log2(ratio)


def preview_dir(source_id: str) -> str:
    return f"cache/previews/{source_id}/grid"


def click(sr: int, freq: float, ms: float = 30.0) -> np.ndarray:
    t = np.arange(round(sr * ms / 1000)) / sr
    return (np.sin(2 * np.pi * freq * t) * np.exp(-t * 1000 / (ms / 4))).astype(np.float32)


def overlay_clicks(
    audio: np.ndarray,
    sr: int,
    beats: np.ndarray,
    downbeats: np.ndarray,
    *,
    start_s: float,
    click_db: float = -6.0,
    music_db: float = -3.0,
) -> np.ndarray:
    """Mix a 1 kHz click on every beat and an accented 2 kHz click on each downbeat."""
    out = audio.astype(np.float32) * np.float32(10 ** (music_db / 20))
    beat_click = click(sr, 1000.0) * np.float32(10 ** ((click_db - 6) / 20))
    down_click = click(sr, 2000.0, 45.0) * np.float32(10 ** (click_db / 20))
    downset = {round(float(d), 6) for d in downbeats}
    for b in beats:
        accent = round(float(b), 6) in downset
        c = down_click if accent else beat_click
        i = round((float(b) - start_s) * sr)
        if i < 0 or i >= len(out):
            continue
        j = min(len(out), i + len(c))
        out[i:j] += c[: j - i, None]
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    if peak > 0.99:
        out *= np.float32(0.99 / peak)
    return out


__all__ = [
    "DEFAULT_TEMPO_RANGE",
    "ChopPlacement",
    "apply_correction",
    "assess",
    "auto_correct",
    "choose_factor",
    "default_beat_phase",
    "derive_beats",
    "derive_downbeat_indices",
    "derive_grid",
    "grid_bpm",
    "meter_consistency",
    "nearest_downbeat",
    "overlay_clicks",
    "place_on_grid",
    "raw_of",
]
