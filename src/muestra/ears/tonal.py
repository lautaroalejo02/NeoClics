"""Tonal coherence: key, tuning/detune and bass-vs-sample (spec 10, bullet 5).

* Key: Krumhansl-Kessler correlation on a peak-based chroma of the
  summed tonal stems (drums excluded: they smear the chroma). Relative
  major/minor are treated as compatible (same scale).
* Tuning: mode of the spectral-peak deviation from A440 12-TET. For a
  resampled chop the expected pitch shift is known from the song
  snapshot + analysis.json (12*log2(project_bpm/local_bpm) +
  pitch_semitones); the measurement tells what is actually heard,
  including a source that was itself recorded off A440.
* Bass vs sample: the bass notes are known exactly from the song, so
  their pitch-class histogram is compared with the sample's measured
  chroma, including the transposition that would fit best.
"""

from __future__ import annotations

import math

import numpy as np

from ..music import PITCH_CLASS_NAMES, KeyParseError, parse_key
from ..schemas.analysis import KeyEstimate
from ..schemas.metrics import Alarm, BassVsSample, DetuneCheck, TonalReport, TrackTonal
from . import dsp
from .alarms import bass_tracks, make_alarm
from .context import RenderContext

__all__ = ["measure_tonal"]


def _wrap_cents(c: float) -> float:
    return (c + 50.0) % 100.0 - 50.0


def _key_name(pc: int, mode: str) -> str:
    return PITCH_CLASS_NAMES[pc] + ("m" if mode == "minor" else "")


def _major_set(pc: int, mode: str) -> frozenset[int]:
    """The diatonic collection (relative minor folded onto its major)."""
    root = pc if mode == "major" else (pc + 3) % 12
    return frozenset((root + s) % 12 for s in (0, 2, 4, 5, 7, 9, 11))


def _profile(pc: int, mode: str) -> np.ndarray:
    return np.roll(dsp.AUDIO_MAJOR if mode == "major" else dsp.AUDIO_MINOR, pc)


def _best_shift(chroma: np.ndarray, pc: int, mode: str) -> tuple[int, float, float]:
    """(k, r_k, r_0): transposition of the track (in semitones) relative
    to the target key that best explains its chroma."""
    prof = _profile(pc, mode)
    rs = {k: dsp.correlation(np.roll(chroma, -k), prof) for k in range(-6, 7)}
    k = max(rs, key=lambda kk: (rs[kk], -abs(kk)))
    return k, rs[k], rs[0]


def measure_tonal(ctx: RenderContext) -> tuple[TonalReport, list[Alarm]]:
    th = ctx.config.thresholds
    tl = ctx.timeline
    alarms: list[Alarm] = []
    try:
        target_pc, target_mode = parse_key(ctx.song.meta.key)
    except KeyParseError:
        target_pc, target_mode = None, None
    report = TonalReport(target_key=ctx.song.meta.key)

    tonal_ids = []
    for t in ctx.song.tracks:
        if t.pattern is not None or t.mute or t.id not in ctx.stems:
            continue
        if float(np.max(np.abs(ctx.mono(t.id)))) < 1e-4:
            continue
        tonal_ids.append(t.id)

    tunings: dict[str, tuple[float | None, float]] = {}
    chromas: dict[str, np.ndarray] = {}
    for tid in tonal_ids:
        y = ctx.mono(tid)
        cents, strength = dsp.tuning_cents(y, ctx.sr)
        tunings[tid] = (cents, strength)
        c = dsp.chroma(y, ctx.sr, tuning=cents if strength >= th.tuning_min_strength else 0.0)
        chromas[tid] = c
        pc, mode, _r, conf = dsp.estimate_key(c)
        tt = TrackTonal(
            key=KeyEstimate(tonic=PITCH_CLASS_NAMES[pc], mode=mode, confidence=conf)
            if np.any(c)
            else None,
            tuning_cents=None if cents is None else round(cents, 1),
        )
        if target_pc is not None and np.any(c):
            scale = dsp.key_scale(target_pc, target_mode)
            tt.out_of_key_share = round(float(sum(c[p] for p in range(12) if p not in scale)), 3)
            k, rk, r0 = _best_shift(c, target_pc, target_mode)
            tt.best_shift_semitones = k
            track = ctx.track(tid)
            if k != 0 and rk - r0 >= 0.25 and rk >= 0.6 and conf >= th.key_min_confidence:
                chop_ids = sorted({cl.chop for cl in (track.clips or []) if cl.chop})
                if chop_ids:
                    chop = next(ch for ch in ctx.song.chops if ch.id == chop_ids[0])
                    fix = (
                        f"cambiá `chops.{chop.id}.warp.pitch_semitones` de "
                        f"{chop.warp.pitch_semitones:g} a {chop.warp.pitch_semitones - k:g}."
                    )
                elif track.instrument is not None:
                    fix = f"transponé las notas de `tracks.{tid}` {-k:+d} semitonos."
                else:
                    fix = f"transponé `{tid}` {-k:+d} semitonos."
                alarms.append(
                    make_alarm(
                        tl,
                        "track_transposed",
                        "warn",
                        f"`{tid}` suena {abs(k)} semitono{'s' if abs(k) > 1 else ''} "
                        f"{'arriba' if k > 0 else 'abajo'} de {ctx.song.meta.key}: choca con la "
                        "tonalidad del tema.",
                        track=tid,
                        value=k,
                        threshold=0,
                        unit="semitones",
                        fix=fix,
                    )
                )
        report.tracks[tid] = tt

    # --- key of the tonal sum (mix without drums) ---------------------------
    if tonal_ids:
        y = sum(ctx.mono(t) for t in tonal_ids)
        source = "tonal_stems"
    else:
        y = ctx.mono("mix")
        source = "mix"
    if np.any(y):
        ref_cents = _reference_tuning(ctx, tunings, exclude=set())
        c = dsp.chroma(y, ctx.sr, tuning=ref_cents)
        pc, mode, _r, conf = dsp.estimate_key(c)
        report.estimated = KeyEstimate(tonic=PITCH_CLASS_NAMES[pc], mode=mode, confidence=conf)
        report.source = source
        if target_pc is not None:
            report.tonic_match = pc == target_pc
            report.mode_match = mode == target_mode
            report.in_key = report.tonic_match and report.mode_match
            est_set, tgt_set = _major_set(pc, mode), _major_set(target_pc, target_mode)
            report.compatible = est_set == tgt_set
            distance = len(est_set - tgt_set)  # accidentals apart (1 = neighbour key)
            transposed = any(a.code == "track_transposed" for a in alarms)
            far = distance >= 2 and conf >= th.key_min_confidence
            near = distance == 1 and conf >= 0.5
            if (far or near) and not transposed:
                alarms.append(
                    make_alarm(
                        tl,
                        "key_mismatch",
                        "warn" if far else "info",
                        f"El tema suena en {_key_name(pc, mode)} (confianza {conf:.2f}) y "
                        f"`meta.key` dice {ctx.song.meta.key}.",
                        value=conf,
                        threshold=th.key_min_confidence,
                        fix=f"si el tema está en {_key_name(pc, mode)}, poné `meta.key: "
                        f"{_key_name(pc, mode)}`; si no, revisá `pitch_semitones` de los chops.",
                    )
                )

    report.detune = _detune(ctx, tunings, alarms)
    report.bass_vs_sample = _bass_vs_sample(ctx, chromas, alarms)
    return report, alarms


def _reference_tuning(
    ctx: RenderContext, tunings: dict[str, tuple[float | None, float]], exclude: set[str]
) -> float:
    """Tuning of the non-sample tonal tracks (instruments are A440 by default)."""
    th = ctx.config.thresholds
    vals = [
        c
        for tid, (c, s) in tunings.items()
        if tid not in exclude
        and c is not None
        and s >= th.tuning_min_strength
        and (ctx.track(tid) is not None and ctx.track(tid).instrument is not None)
    ]
    return float(np.median(vals)) if vals else 0.0


def _detune(ctx: RenderContext, tunings, alarms: list[Alarm]) -> list[DetuneCheck]:
    th = ctx.config.thresholds
    tl = ctx.timeline
    out: list[DetuneCheck] = []
    for track in ctx.song.tracks:
        if track.clips is None or track.mute or track.id not in tunings:
            continue
        chop_ids = []
        for cl in track.clips:
            if cl.chop and cl.chop not in chop_ids:
                chop_ids.append(cl.chop)
        measured, strength = tunings[track.id]
        ok = measured is not None and strength >= th.tuning_min_strength
        ref = _reference_tuning(ctx, tunings, exclude={track.id})
        for cid in chop_ids:
            chop = next((c for c in ctx.song.chops if c.id == cid), None)
            if chop is None:
                continue
            _analysis, span = ctx.analysis_for_chop(cid)
            local_bpm = None
            if span is not None and span[1] > span[0]:
                local_bpm = 60.0 * chop.length_beats / (span[1] - span[0])
            ps = chop.warp.pitch_semitones
            if chop.warp.mode == "resample":
                shift = None if local_bpm is None else 12.0 * math.log2(tl.bpm / local_bpm) + ps
            else:
                shift = ps
            exp_cents = None if shift is None else _wrap_cents((shift - round(shift)) * 100.0)
            rel = _wrap_cents(measured - ref) if ok else None
            out.append(
                DetuneCheck(
                    track=track.id,
                    chop=cid,
                    mode=chop.warp.mode,
                    local_bpm=None if local_bpm is None else round(local_bpm, 3),
                    expected_shift_semitones=None if shift is None else round(shift, 3),
                    expected_detune_cents=None if exp_cents is None else round(exp_cents, 1),
                    measured_cents=None if not ok else round(measured, 1),
                    reference_cents=round(ref, 1),
                    relative_cents=None if rel is None else round(rel, 1),
                )
            )
            heard = rel if rel is not None else exp_cents
            if heard is None or abs(heard) <= th.detune_warn_cents:
                continue
            sev = "error" if abs(heard) > th.detune_error_cents and rel is not None else "warn"
            basis = "medido" if rel is not None else "previsto por el resample (sin medir)"
            new_ps = round(ps - heard / 100.0, 2)
            fix = f"poné `chops.{cid}.warp.pitch_semitones: {new_ps:g}` (hoy {ps:g})"
            if (
                chop.warp.mode == "resample"
                and local_bpm is not None
                and exp_cents is not None
                and abs(exp_cents - heard) < 10.0
            ):
                good_bpm = local_bpm * 2.0 ** ((round(shift) - ps) / 12.0)
                fix += (
                    f" o llevá `meta.bpm` a {good_bpm:.2f} para que el resample caiga en un "
                    "semitono entero"
                )
            expl = ""
            if exp_cents is not None and shift is not None:
                expl = (
                    f" (el resample de {local_bpm:.1f} a {tl.bpm:g} BPM lo corre "
                    f"{shift:+.2f} semitonos)"
                )
            alarms.append(
                make_alarm(
                    tl,
                    "sample_detuned",
                    sev,
                    f"`{track.id}` está desafinado {heard:+.0f} cents respecto del resto ({basis})"
                    f"{expl}: el bajo y el sample van a batir.",
                    track=track.id,
                    chop=cid,
                    value=heard,
                    threshold=th.detune_warn_cents,
                    unit="cents",
                    fix=fix + ".",
                )
            )
            break  # one detune alarm per track: the stem's tuning is one number
    return out


def _bass_vs_sample(ctx: RenderContext, chromas, alarms: list[Alarm]) -> list[BassVsSample]:
    th = ctx.config.thresholds
    tl = ctx.timeline
    out: list[BassVsSample] = []
    roles = set(bass_tracks(ctx))
    basses = [
        t for t in ctx.song.tracks if t.id in roles and t.instrument is not None and t.id in chromas
    ]
    samples = [t for t in ctx.song.tracks if t.clips is not None and t.id in chromas]
    for b in basses:
        notes = ctx.timeline.notes(b.id)
        if len(notes) < 2:
            continue
        hist = np.zeros(12)
        for n in notes:
            if n.pitch is not None:
                hist[n.pitch % 12] += n.dur_s or 0.1
        if hist.sum() <= 0:
            continue
        hist /= hist.sum()
        for s in samples:
            c = chromas[s.id]
            if not np.any(c):
                continue
            fit = dsp.correlation(hist, c)
            rs = {k: dsp.correlation(np.roll(hist, k), c) for k in range(-6, 7)}
            k = max(rs, key=lambda kk: (rs[kk], -abs(kk)))
            order = np.argsort(-c)
            strong = set(order[:5].tolist())
            present = set(order[:7].tolist())
            clash = sum(
                hist[p]
                for p in range(12)
                if p not in present and ({(p + 1) % 12, (p - 1) % 12} & strong)
            )
            out.append(
                BassVsSample(
                    bass_track=b.id,
                    sample_track=s.id,
                    fit=round(fit, 3),
                    best_shift_semitones=k,
                    best_fit=round(rs[k], 3),
                    clash_share=round(float(clash), 3),
                )
            )
            shifted = k != 0 and rs[k] - fit >= 0.3 and rs[k] >= 0.5
            if clash >= th.bass_clash_warn_share or shifted:
                if shifted:
                    chop_ids = sorted({cl.chop for cl in (s.clips or []) if cl.chop})
                    alt = ""
                    if chop_ids:
                        ch = next(x for x in ctx.song.chops if x.id == chop_ids[0])
                        alt = (
                            f" o el sample {-k:+d} (`chops.{ch.id}.warp.pitch_semitones: "
                            f"{ch.warp.pitch_semitones - k:g}`)"
                        )
                    fix = f"transponé las notas de `tracks.{b.id}` {k:+d} semitonos{alt}."
                else:
                    fix = (
                        f"revisá las notas de `tracks.{b.id}` que caen a un semitono de las "
                        "notas fuertes del sample ("
                        + ", ".join(PITCH_CLASS_NAMES[p] for p in sorted(strong))
                        + ")."
                    )
                alarms.append(
                    make_alarm(
                        tl,
                        "bass_sample_clash",
                        "warn",
                        f"El bajo (`{b.id}`) choca con las notas del sample (`{s.id}`)"
                        + (
                            f": encajaría {k:+d} semitonos"
                            if shifted
                            else f" ({clash:.0%} del tiempo a un semitono)"
                        )
                        + ".",
                        track=b.id,
                        value=clash if not shifted else k,
                        threshold=th.bass_clash_warn_share if not shifted else 0,
                        unit="share" if not shifted else "semitones",
                        fix=fix,
                    )
                )
    return out
