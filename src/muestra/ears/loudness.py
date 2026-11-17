"""Loudness, true peak, clipping and master dynamics (spec 10, bullet 1)."""

from __future__ import annotations

import numpy as np

from ..schemas.metrics import Alarm, Loudness
from . import dsp
from .alarms import make_alarm
from .context import RenderContext

__all__ = ["measure_loudness"]


def _clip_times(x: np.ndarray, sr: int, threshold: float = 0.9999) -> list[float]:
    hot = np.any(np.abs(x) >= threshold, axis=1)
    idx = np.flatnonzero(hot)
    if idx.size == 0:
        return []
    starts = idx[np.concatenate([[True], np.diff(idx) > int(0.05 * sr)])]
    return [float(i) / sr for i in starts]


def measure_loudness(ctx: RenderContext) -> tuple[Loudness, list[Alarm]]:
    th = ctx.config.thresholds
    tl = ctx.timeline
    x, sr = ctx.mix, ctx.sr
    power = dsp.weighted_power(x, sr)
    integrated = dsp.integrated_loudness(x, sr, power=power)
    st = dsp.short_term_loudness(x, sr, power=power)
    lra = dsp.loudness_range(st)
    tp_lin = dsp.true_peak(x, sr)
    tp = dsp.db(tp_lin)
    sp = dsp.db(float(np.max(np.abs(x))) if x.size else 0.0)
    rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
    crest = sp - dsp.db(rms) if rms > 0 else None
    total, in_runs = dsp.clipped_samples(x)
    target = ctx.song.master.target_lufs
    silent = integrated <= -70.0
    delta = None if target is None or silent else integrated - target
    plr = None if silent else tp - integrated
    alarms: list[Alarm] = []
    target_txt = f"{target:g}" if target is not None else "sin definir"

    if silent:
        alarms.append(
            make_alarm(
                tl,
                "mix_silent",
                "error",
                "El mix está en silencio (o casi): no hay nada para escuchar.",
                value=integrated,
                threshold=-70.0,
                unit="LUFS",
                fix="Revisá `mute`/`gain_db` de los tracks y que los clips caigan dentro "
                "de `range_bars`.",
            )
        )
    if in_runs >= th.clip_run_error:
        times = _clip_times(x, sr)
        alarms.append(
            make_alarm(
                tl,
                "clipping",
                "error",
                f"El master satura: {in_runs} muestras recortadas contra 0 dBFS, "
                "se oye como distorsión en los golpes.",
                at_s=times[0] if times else None,
                where=times,
                value=in_runs,
                threshold=th.clip_run_error,
                unit="samples",
                count=len(times) or None,
                fix=f"bajá `master.target_lufs` (hoy {target_txt}) 2-3 dB o poné "
                "`{type: limiter, ceiling_db: -1.0}` al final de `master.fx`.",
            )
        )
    elif tp > th.true_peak_warn_dbtp:
        alarms.append(
            make_alarm(
                tl,
                "true_peak_hot",
                "warn",
                f"El pico real llega a {tp:+.1f} dBTP: puede distorsionar al pasarlo a "
                "mp3/streaming.",
                value=tp,
                threshold=th.true_peak_warn_dbtp,
                unit="dBTP",
                fix="poné `{type: limiter, ceiling_db: -1.0}` al final de `master.fx` o "
                f"bajá `master.target_lufs` (hoy {target_txt}) 1 dB.",
            )
        )
    if delta is not None and abs(delta) > th.target_warn_lu:
        sev = "error" if abs(delta) > th.target_error_lu else "warn"
        if delta < 0 and plr is not None and plr < th.plr_warn_db + 1.0:
            why = "el limitador ya está al tope y no deja llegar más fuerte"
            fix = f"bajá `master.target_lufs` a {round(integrated):g} (lo que el master da)."
        elif delta < 0:
            why = "suena más bajo de lo pedido"
            fix = "revisá `master.gain_db` y los `gain_db` de los tracks."
        else:
            why = "suena más fuerte de lo pedido"
            fix = f"revisá `master.gain_db` (hoy {ctx.song.master.gain_db:g})."
        alarms.append(
            make_alarm(
                tl,
                "loudness_off_target",
                sev,
                f"El mix mide {integrated:.1f} LUFS contra un target de {target:g}: {why}.",
                value=delta,
                threshold=th.target_warn_lu,
                unit="LU",
                fix=fix,
            )
        )
    if plr is not None and plr < th.plr_warn_db:
        sev = "error" if plr < th.plr_error_db else "warn"
        alarms.append(
            make_alarm(
                tl,
                "over_compressed",
                sev,
                f"El master está aplastado (pico/loudness {plr:.1f} dB): el bombo pierde "
                "golpe y el sidechain se nota menos.",
                value=plr,
                threshold=th.plr_warn_db,
                unit="dB",
                fix=f"bajá `master.target_lufs` (hoy {target_txt}) 2 dB o el `ratio` del "
                "compresor de `master.fx`.",
            )
        )
    if target is None and not silent:
        alarms.append(
            make_alarm(
                tl,
                "no_loudness_target",
                "info",
                f"No hay `master.target_lufs`; el mix mide {integrated:.1f} LUFS "
                "(future funk suele andar entre -10 y -8).",
                value=integrated,
                unit="LUFS",
                fix="agregá `master.target_lufs: -10`.",
            )
        )
    loud = Loudness(
        integrated_lufs=round(integrated, 2),
        true_peak_dbtp=round(tp, 2),
        clipping=in_runs >= th.clip_run_error,
        clipped_samples=total,
        sample_peak_dbfs=round(sp, 2),
        short_term_max_lufs=round(float(np.max(st)), 2) if st.size else None,
        lra_lu=None if lra is None else round(lra, 2),
        target_lufs=target,
        target_delta_lu=None if delta is None else round(delta, 2),
        plr_db=None if plr is None else round(plr, 2),
        crest_factor_db=None if crest is None else round(crest, 2),
    )
    return loud, alarms
