"""Technical extras (spec 10 + WP6): DC, mono compatibility, silent and
inaudible tracks."""

from __future__ import annotations

import numpy as np

from ..schemas.metrics import DETAIL_BAND_EDGES_HZ, Alarm, TechnicalReport, TrackLevel
from . import dsp
from .alarms import make_alarm
from .context import RenderContext

__all__ = ["measure_technical"]


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 1.0
    return float(np.clip(np.corrcoef(a, b)[0, 1], -1.0, 1.0))


def measure_technical(ctx: RenderContext) -> tuple[TechnicalReport, list[Alarm]]:
    th = ctx.config.thresholds
    tl = ctx.timeline
    alarms: list[Alarm] = []
    x = ctx.mix
    dc = max(abs(float(np.mean(x[:, 0]))), abs(float(np.mean(x[:, 1]))))
    corr = _corr(x[:, 0], x[:, 1])
    low = dsp.lowpass(x, ctx.sr, 150.0)
    low_corr = _corr(low[:, 0], low[:, 1])
    p_lr = float(np.mean(low[:, 0] ** 2) + np.mean(low[:, 1] ** 2)) / 2.0
    p_mono = float(np.mean(((low[:, 0] + low[:, 1]) / 2.0) ** 2))
    mono_loss = 0.0 if p_lr <= 1e-14 else max(0.0, -10.0 * np.log10(max(p_mono, 1e-20) / p_lr))
    low_has_energy = p_lr > 10 ** (-50 / 10)

    if dc > 10 ** (th.dc_warn_db / 20.0):
        alarms.append(
            make_alarm(
                tl,
                "dc_offset",
                "warn",
                f"El mix tiene corrimiento de continua ({dsp.db(dc):.0f} dBFS): come headroom y "
                "puede hacer clicks al cortar.",
                value=dsp.db(dc),
                threshold=th.dc_warn_db,
                unit="dBFS",
                fix="agregá `{type: highpass, cutoff_hz: 20}` al principio de `master.fx` "
                "(o al track que lo genera, ver technical.tracks).",
            )
        )
    if low_has_energy and (low_corr < th.low_corr_warn or mono_loss > th.mono_loss_warn_db):
        alarms.append(
            make_alarm(
                tl,
                "low_end_phase",
                "warn",
                f"Los graves están abiertos/en contrafase (correlación {low_corr:.2f}, en mono "
                f"se pierden {mono_loss:.1f} dB): en un parlante mono o un club el grave "
                "desaparece.",
                value=low_corr,
                threshold=th.low_corr_warn,
                fix="dejá bajo y bombo centrados (`pan: 0`) y sacá chorus/reverb/phaser de los "
                "tracks con graves (o ponele highpass a esos efectos).",
            )
        )

    levels: dict[str, TrackLevel] = {}
    silent, inaudible = [], []
    tracks = {t.id: t for t in ctx.song.tracks}
    specs = {}
    for tid, y in ctx.stems.items():
        track = tracks.get(tid)
        muted = bool(track and track.mute)
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        rms = float(np.sqrt(np.mean(y * y))) if y.size else 0.0
        tdc = max(abs(float(np.mean(y[:, 0]))), abs(float(np.mean(y[:, 1]))))
        levels[tid] = TrackLevel(
            peak_dbfs=round(dsp.db(peak), 2),
            rms_dbfs=round(dsp.db(rms), 2),
            dc_offset_db=round(dsp.db(tdc), 2),
            muted=muted,
        )
        if muted:
            continue
        if dsp.db(peak) < th.silent_peak_db:
            silent.append(tid)
            alarms.append(
                make_alarm(
                    tl,
                    "track_silent",
                    "warn",
                    f"`{tid}` no suena en este render (pico {dsp.db(peak):.0f} dBFS) y no está "
                    "muteado.",
                    track=tid,
                    value=dsp.db(peak),
                    threshold=th.silent_peak_db,
                    unit="dBFS",
                    fix=f"revisá `tracks.{tid}` (clips/notas dentro de `range_bars`, `gain_db`, "
                    "plugin/preset) o muteálo con `mute: true`.",
                )
            )
            continue
        specs[tid] = dsp.band_frames(ctx.spec(tid), DETAIL_BAND_EDGES_HZ)

    # inaudible: below the rest of the mix by the margin in every band,
    # in (almost) every frame where the track is playing
    if len(specs) >= 2:
        total = sum(specs.values())
        margin = 10 ** (-th.inaudible_margin_db / 10.0)
        for tid, bands in specs.items():
            rest = total - bands
            frame_pow = bands.sum(axis=0)
            active = frame_pow > float(frame_pow.max()) * 1e-4
            if not np.any(active):
                continue
            audible_frames = np.any(bands >= rest * margin, axis=0) & active
            share = float(np.count_nonzero(audible_frames) / np.count_nonzero(active))
            levels[tid].audible_share = round(share, 3)
            if share < th.inaudible_max_share:
                inaudible.append(tid)
                tr = tracks.get(tid)
                gain = f" (hoy {tr.gain_db:g})" if tr else ""
                alarms.append(
                    make_alarm(
                        tl,
                        "track_inaudible",
                        "warn",
                        f"`{tid}` queda tapado por el resto en todas las bandas: en el mix no "
                        "se escucha.",
                        track=tid,
                        value=share,
                        threshold=th.inaudible_max_share,
                        unit="share",
                        fix=f"subí `tracks.{tid}.gain_db`{gain} unos 6-10 dB o sacalo si no "
                        "aporta.",
                    )
                )
    report = TechnicalReport(
        dc_offset_db=round(dsp.db(dc), 2),
        stereo_correlation=round(corr, 3),
        low_band_correlation=round(low_corr, 3),
        low_band_mono_loss_db=round(float(mono_loss), 2),
        tracks=levels,
        silent_tracks=silent,
        inaudible_tracks=inaudible,
    )
    return report, alarms
