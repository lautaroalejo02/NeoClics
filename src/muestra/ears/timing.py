"""Grid alignment of transients (spec 10, bullet 4).

Two bases, chosen per track kind:

* ``events`` (pattern lanes, instrument tracks): the song says exactly
  when each hit/note starts; every detected onset is matched to the
  nearest expected event. Swing is already inside the expected times, so
  a swung pattern never reads as "late".
* ``beats`` (clip tracks, i.e. the sample): the source's own rhythm is
  unknown, so only onsets within 1/8 beat of a quarter note are used to
  measure the systematic offset. Syncopation, triplets and 16th
  pick-ups fall outside that window and cannot bias it; the full
  distribution against the nearest 16th is reported alongside. An alarm
  needs a tight cluster (small MAD) AND a clear pulse (enough quarter
  notes carry an onset); otherwise the material is "not grid-like" and
  ears stays quiet.
"""

from __future__ import annotations

import numpy as np

from ..schemas.metrics import (
    Alarm,
    AlarmLocation,  # noqa: F401  (re-exported for typing users)
    AlignmentEvent,
    AlignmentReport,
    ClipOffset,
    TrackAlignment,
)
from . import dsp
from .alarms import make_alarm
from .context import ClipInstance, RenderContext

__all__ = ["measure_alignment"]

_HIST_EDGES = [-1e9, -30.0, -15.0, -5.0, 5.0, 15.0, 30.0, 1e9]
_HIST_LABELS = ["<-30", "-30..-15", "-15..-5", "-5..5", "5..15", "15..30", ">30"]
MAX_FLAGGED = 8
#: flux-peak onsets precede the attack by ~2.3 ms on dense sampled material
#: (measured on the corpus: -2.1..-2.6 ms with MAD < 1 ms); added back.
FLUX_BIAS_MS = 2.3


def _hist(offsets_ms: np.ndarray) -> dict[str, int]:
    counts, _ = np.histogram(offsets_ms, bins=_HIST_EDGES)
    return {lab: int(c) for lab, c in zip(_HIST_LABELS, counts, strict=True)}


def _stats(off: np.ndarray) -> tuple[float, float, float, float, float]:
    med = float(np.median(off))
    mad = float(np.median(np.abs(off - med)))
    return (
        float(np.mean(off)),
        med,
        mad,
        float(np.max(np.abs(off))),
        float(np.percentile(np.abs(off), 90)),
    )


def measure_alignment(ctx: RenderContext) -> tuple[AlignmentReport, list[Alarm]]:
    alarms: list[Alarm] = []
    tracks: dict[str, TrackAlignment] = {}
    lanes: dict[str, TrackAlignment] = {}
    for track in ctx.song.tracks:
        if track.mute or track.id not in ctx.stems:
            continue
        if track.pattern is not None:
            hits = ctx.timeline.lane_hits(track.id)
            merged: list[float] = []
            for lane, events in hits.items():
                if not events or lane not in ctx.lanes.get(track.id, {}):
                    continue
                key = f"{track.id}.{lane}"
                res = _events_basis(ctx, key, [e.t for e in events], track.id, lane, alarms)
                if res is not None:
                    lanes[key] = res[0]
                    merged += res[1]
            if merged:
                off = np.array(merged)
                mean, med, mad, mx, p90 = _stats(off)
                tracks[track.id] = TrackAlignment(
                    basis="events",
                    mean_offset_ms=round(mean, 2),
                    median_offset_ms=round(med, 2),
                    mad_ms=round(mad, 2),
                    max_abs_offset_ms=round(mx, 2),
                    p90_abs_offset_ms=round(p90, 2),
                    n_transients=int(off.size),
                    histogram_ms=_hist(off),
                )
        elif track.instrument is not None:
            notes = [e.t for e in ctx.timeline.notes(track.id)]
            if notes:
                res = _events_basis(ctx, track.id, notes, track.id, None, alarms)
                if res is not None:
                    tracks[track.id] = res[0]
        elif track.clips is not None:
            res2 = _beats_basis(ctx, track.id, alarms)
            if res2 is not None:
                tracks[track.id] = res2
    overall = max((a.max_abs_offset_ms for a in tracks.values()), default=0.0)
    return AlignmentReport(tracks=tracks, lanes=lanes, max_abs_offset_ms=overall), alarms


# ---------------------------------------------------------------------------
# events basis
# ---------------------------------------------------------------------------


def _events_basis(
    ctx: RenderContext,
    key: str,
    expected: list[float],
    tid: str,
    lane: str | None,
    alarms: list[Alarm],
) -> tuple[TrackAlignment, list[float]] | None:
    th = ctx.config.thresholds
    tl = ctx.timeline
    on = dsp.onsets(ctx.mono(key), ctx.sr).strongest(0.1)
    if len(on) == 0:
        return None
    exp = np.array(sorted(expected))
    gaps = np.diff(exp)
    tol = 0.04 if gaps.size == 0 else min(0.04, 0.45 * float(np.min(gaps[gaps > 1e-6], initial=1)))
    offs, times = [], []
    for e in exp:
        j = int(np.argmin(np.abs(on.times - e)))
        d = on.times[j] - e
        if abs(d) <= tol:
            offs.append(d * 1000.0)
            times.append(float(e))
    if len(offs) < 3:
        return None
    off = np.array(offs)
    mean, med, mad, mx, p90 = _stats(off)
    flagged = [
        AlignmentEvent(
            at_s=round(t, 3), bar=tl.where(t)[0], beat=tl.where(t)[1], offset_ms=round(o, 1)
        )
        for o, t in sorted(zip(offs, times, strict=True), key=lambda p: -abs(p[0]))
        if abs(o) > th.timing_warn_ms
    ][:MAX_FLAGGED]
    res = TrackAlignment(
        basis="events",
        mean_offset_ms=round(mean, 2),
        median_offset_ms=round(med, 2),
        mad_ms=round(mad, 2),
        max_abs_offset_ms=round(mx, 2),
        p90_abs_offset_ms=round(p90, 2),
        n_transients=int(off.size),
        histogram_ms=_hist(off),
        flagged=flagged,
    )
    track = ctx.track(tid)
    is_instrument = track is not None and track.instrument is not None
    max_mad = th.timing_max_mad_ms * (0.6 if is_instrument else 1.0)
    if off.size >= 4 and mad <= max_mad and abs(med) > th.timing_warn_ms:
        late = med > 0
        sev = "error" if abs(med) > th.timing_error_ms and not is_instrument else "warn"
        if lane:
            when = "tarde" if late else "antes"
            what = f"El carril `{lane}` de `{tid}` suena {abs(med):.0f} ms {when} de la grilla"
            if late:
                what += " (¿el sample tiene silencio al principio?)"
                fix = (
                    f"cambiá `tracks.{tid}.pattern.lanes.{lane}.sample` por uno sin silencio "
                    "inicial."
                )
            else:
                fix = f"revisá `tracks.{tid}.pattern.swing` y el sample del carril `{lane}`."
        else:
            what = f"Las notas de `{tid}` arrancan {abs(med):.0f} ms {'tarde' if late else 'antes'}"
            if late:
                what += " (ataque lento del preset)"
            fix = (
                f"usá un preset con ataque más corto en `tracks.{tid}.instrument` o adelantá "
                "las notas."
            )
        alarms.append(
            make_alarm(
                tl,
                "lane_offset" if lane else "notes_offset",
                sev,
                what + ".",
                at_s=times[0],
                track=tid,
                lane=lane,
                value=med,
                threshold=th.timing_warn_ms,
                unit="ms",
                fix=fix,
            )
        )
    return res, offs


# ---------------------------------------------------------------------------
# beats basis (clip tracks)
# ---------------------------------------------------------------------------


def _beats_basis(ctx: RenderContext, tid: str, alarms: list[Alarm]) -> TrackAlignment | None:
    th = ctx.config.thresholds
    tl = ctx.timeline
    inst = ctx.timeline.clip_instances(tid)
    if not inst:
        return None
    # dense sampled material: the flux-peak time is the consistent one (see dsp.onsets)
    on = dsp.onsets(ctx.mono(tid), ctx.sr, refine=False).strongest(0.15)
    if len(on) == 0:
        ctx.skipped.append(f"alignment de '{tid}': sin transitorios detectables")
        return None
    spb = tl.spb
    pos = on.times / spb  # render-relative beats (render starts on a bar line)
    q = np.round(pos)
    off_q = (pos - q) * spb * 1000.0 + FLUX_BIAS_MS
    near_beat = np.abs(pos - q) <= 0.125
    s16 = np.round(pos * 4.0) / 4.0
    off16 = (pos - s16) * spb * 1000.0 + FLUX_BIAS_MS
    odd16 = (np.round(pos * 4.0).astype(int) % 2 == 1) & (np.abs(pos - s16) <= 0.0625)

    def instance_of(t: float) -> ClipInstance | None:
        for c in inst:
            if c.start_s - 0.06 <= t < c.end_s - 0.02:
                return c
        return None

    owners = [instance_of(t) for t in on.times]
    active = np.array([o is not None for o in owners])
    sel = near_beat & active
    off = off_q[sel]
    t_sel = on.times[sel]
    own_sel = [o for o, k in zip(owners, sel, strict=True) if k]
    if off.size == 0:
        ctx.skipped.append(f"alignment de '{tid}': ningún transitorio cerca de los tiempos")
        return None
    # pulse: share of quarter notes (inside clip instances) that carry an onset
    quarters = set()
    for c in inst:
        qa = int(np.ceil(c.start_s / spb - 1e-6))
        qb = int(np.floor((c.end_s - 0.02) / spb))
        quarters.update(range(qa, qb + 1))
    hit_q = set(int(v) for v in q[sel])
    pulse = len(hit_q & quarters) / max(1, len(quarters))
    mean, med, mad, mx, p90 = _stats(off)
    # drift: offset vs position inside the clip instance (pooled)
    rel = np.array([t - o.start_s for t, o in zip(t_sel, own_sel, strict=True)])
    drift = 0.0
    inst_len = float(np.median([c.end_s - c.start_s for c in inst]))
    if off.size >= th.timing_min_onsets and np.ptp(rel) > 0.5 * inst_len:
        drift = float(np.polyfit(rel, off, 1)[0] * inst_len)
    # off-beat 16ths (swing)
    offbeat = off16[odd16 & active]
    offbeat_med = float(np.median(offbeat)) if offbeat.size >= 4 else None
    # per clip instance
    clip_offs: list[ClipOffset] = []
    for c in inst:
        vals = np.array([o for o, ow in zip(off, own_sel, strict=True) if ow is c])
        if vals.size >= 3:
            bar, _ = tl.where(c.start_s)
            clip_offs.append(
                ClipOffset(
                    clip=c.ref,
                    repeat_index=c.repeat_index,
                    chop=c.chop,
                    at_s=round(c.start_s, 3),
                    bar=bar,
                    offset_ms=round(float(np.median(vals)), 2),
                    n_onsets=int(vals.size),
                )
            )
    flagged = []
    for o, t, ow in sorted(zip(off, t_sel, own_sel, strict=True), key=lambda p: -abs(p[0])):
        if abs(o) <= th.timing_warn_ms or len(flagged) >= MAX_FLAGGED:
            break
        bar, beat = tl.where(float(t))
        flagged.append(
            AlignmentEvent(
                at_s=round(float(t), 3),
                bar=bar,
                beat=beat,
                offset_ms=round(float(o), 1),
                clip=ow.ref if ow else None,
            )
        )
    res = TrackAlignment(
        basis="beats",
        mean_offset_ms=round(mean, 2),
        median_offset_ms=round(med, 2),
        mad_ms=round(mad, 2),
        max_abs_offset_ms=round(mx, 2),
        p90_abs_offset_ms=round(p90, 2),
        n_transients=int(off.size),
        drift_ms=round(drift, 2),
        pulse_share=round(pulse, 3),
        offbeat_offset_ms=None if offbeat_med is None else round(offbeat_med, 2),
        histogram_ms=_hist(off16[active]),
        flagged=flagged,
        clips=clip_offs,
    )
    trusted = off.size >= th.timing_min_onsets and pulse >= th.timing_min_pulse_share
    if not trusted:
        ctx.skipped.append(
            f"alignment de '{tid}': pulso poco claro ({off.size} transitorios en tiempo, "
            f"{pulse:.0%} de los tiempos): no se evalúa"
        )
        return res
    _clip_track_alarms(ctx, tid, inst, off, t_sel, own_sel, rel, clip_offs, alarms)
    # swing: the sample's off-beat 16ths vs the drums' swing
    if offbeat_med is not None and mad <= th.timing_max_mad_ms:
        sample_swing_ms = offbeat_med - med
        drum = next((t for t in ctx.song.tracks if t.pattern is not None and not t.mute), None)
        if drum is not None:
            step_s = spb * 4.0 / drum.pattern.steps_per_bar
            drum_swing_ms = drum.pattern.swing * step_s / 3.0 * 1000.0
            if abs(sample_swing_ms - drum_swing_ms) > th.swing_info_ms and sample_swing_ms > 0:
                target = min(1.0, max(0.0, 3.0 * sample_swing_ms / 1000.0 / step_s))
                alarms.append(
                    make_alarm(
                        tl,
                        "swing_mismatch",
                        "info",
                        f"`{tid}` tiene swing (~{sample_swing_ms:.0f} ms en los contratiempos) y "
                        f"`{drum.id}` va con {drum_swing_ms:.0f} ms: el groove puede chocar.",
                        track=tid,
                        value=sample_swing_ms,
                        threshold=th.swing_info_ms,
                        unit="ms",
                        fix=f"probá `tracks.{drum.id}.pattern.swing: {target:.2f}`.",
                    )
                )
    return res


def _clip_track_alarms(ctx, tid, inst, off, t_sel, own_sel, rel, clip_offs, alarms) -> None:
    """Drift first (it also shifts the median), then single clip instances
    that disagree with the rest, then a systematic offset of the track."""
    th = ctx.config.thresholds
    tl = ctx.timeline
    chops = sorted({c.chop for c in inst if c.chop})
    chop_obj = next((c for c in ctx.song.chops if c.id == chops[0]), None) if chops else None
    inst_len = float(np.median([c.end_s - c.start_s for c in inst]))

    # 1. drift inside the chop (tempo of the loop != grid)
    if off.size >= th.timing_min_onsets and np.ptp(rel) > 0.5 * inst_len:
        slope, icpt = np.polyfit(rel, off, 1)
        drift = float(slope * inst_len)
        resid = off - (slope * rel + icpt)
        resid_mad = float(np.median(np.abs(resid - np.median(resid))))
        if (
            abs(drift) > th.timing_drift_warn_ms
            and resid_mad <= th.timing_max_mad_ms
            and abs(drift) > 3.0 * max(resid_mad, 1.0)
        ):
            modes = {c.warp.mode for c in ctx.song.chops if c.id in chops}
            if "resample" in modes:
                fix = (
                    "poné `warp.mode: stretch` en "
                    + ", ".join(f"`chops.{c}`" for c in chops)
                    + " (alinea cada beat de la fuente a la grilla) o revisá `beats_s` del "
                    "análisis."
                )
            else:
                fix = (
                    "revisá `beats_s` del análisis de la fuente (el beat tracking puede estar mal)."
                )
            alarms.append(
                make_alarm(
                    tl,
                    "grid_drift",
                    "warn" if abs(drift) < 2 * th.timing_error_ms else "error",
                    f"`{tid}` se va corriendo dentro de cada chop ({float(icpt):+.0f} ms al "
                    f"empezar, {float(icpt) + drift:+.0f} ms al terminar): el tempo del loop no "
                    "coincide con la grilla.",
                    track=tid,
                    chop=chops[0] if len(chops) == 1 else None,
                    value=drift,
                    threshold=th.timing_drift_warn_ms,
                    unit="ms",
                    fix=fix,
                )
            )
            return

    # 2. clip instances that disagree with the others
    tight = [co for co in clip_offs if co.n_onsets >= 3]
    outliers: set[tuple[str, int]] = set()
    if len(tight) >= 2:
        for co in tight:
            others = float(np.median([x.offset_ms for x in tight if x is not co]))
            if (
                abs(co.offset_ms - others) > th.timing_clip_warn_ms
                and abs(co.offset_ms) > th.timing_warn_ms
            ):
                outliers.add((co.clip, co.repeat_index))
                ci = int(co.clip.rsplit("[", 1)[1].rstrip("]"))
                clip = ctx.track(tid).clips[ci]
                beats = co.offset_ms * tl.bpm / 60000.0
                if clip.repeat == 1:
                    fix = (
                        f"cambiá `{co.clip}.offset_beats` de {clip.offset_beats:g} a "
                        f"{clip.offset_beats - beats:.3f}."
                    )
                else:
                    fix = (
                        f"revisá `{co.clip}` (sólo la repetición {co.repeat_index + 1} está "
                        "corrida)."
                    )
                late = co.offset_ms > 0
                alarms.append(
                    make_alarm(
                        tl,
                        "clip_offset",
                        "error" if abs(co.offset_ms) > th.timing_error_ms else "warn",
                        f"`{co.clip}` (compás {co.bar}) cae {abs(co.offset_ms):.0f} ms "
                        f"{'tarde' if late else 'antes'} de la grilla, el resto del loop no: "
                        "ese chop se siente desfasado.",
                        at_s=co.at_s,
                        track=tid,
                        clip=co.clip,
                        chop=co.chop,
                        value=co.offset_ms,
                        threshold=th.timing_warn_ms,
                        unit="ms",
                        fix=fix,
                    )
                )

    # 3. systematic offset of the whole track (outlier instances excluded)
    keep = np.array(
        [not (o is not None and (o.ref, o.repeat_index) in outliers) for o in own_sel], dtype=bool
    )
    if np.count_nonzero(keep) < th.timing_min_onsets:
        return
    vals = off[keep]
    med = float(np.median(vals))
    mad = float(np.median(np.abs(vals - med)))
    if mad > th.timing_max_mad_ms or abs(med) <= th.timing_warn_ms:
        return
    late = med > 0
    beats_fix = abs(med) * tl.bpm / 60000.0
    if len(chops) == 1 and chop_obj is not None:
        new = chop_obj.start_offset_beats + (beats_fix if late else -beats_fix)
        fix = (
            f"cambiá `chops.{chop_obj.id}.start_offset_beats` de "
            f"{chop_obj.start_offset_beats:g} a {new:.3f} "
            f"({'arranca el chop más tarde' if late else 'más temprano'} en la fuente)."
        )
    else:
        fix = (
            f"corré `start_offset_beats` de los chops de `{tid}` "
            f"{'+' if late else '-'}{beats_fix:.3f} beats."
        )
    t_keep = t_sel[keep]
    alarms.append(
        make_alarm(
            tl,
            "grid_offset",
            "error" if abs(med) > th.timing_error_ms else "warn",
            f"`{tid}` cae {abs(med):.0f} ms {'tarde' if late else 'antes'} respecto de la "
            f"grilla en todo el loop: se siente {'arrastrado' if late else 'apurado'} contra "
            "la batería.",
            at_s=float(t_keep[0]),
            where=[float(t) for t in t_keep],
            track=tid,
            chop=chops[0] if len(chops) == 1 else None,
            value=med,
            threshold=th.timing_warn_ms if late else -th.timing_warn_ms,
            unit="ms",
            count=int(vals.size),
            fix=fix,
        )
    )
