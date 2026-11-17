"""Spectral balance vs a dance-music reference tilt (spec 10, bullet 2)."""

from __future__ import annotations

import numpy as np

from ..schemas.metrics import (
    BAND_EDGES_HZ,
    DETAIL_BAND_EDGES_HZ,
    Alarm,
    SpectralBalance,
    SpectralReport,
)
from ..schemas.song import HighpassFx
from . import dsp
from .alarms import find_fx, fx_param_path, hp_fix, is_automated, lp_path, make_alarm
from .context import RenderContext

__all__ = ["measure_spectral", "reference_shares"]

_BAND_HZ_TXT = {
    "sub": "20-60 Hz",
    "bass": "60-250 Hz",
    "low_mid": "250-500 Hz",
    "mid": "500 Hz-2 kHz",
    "high_mid": "2-4 kHz",
    "presence": "4-8 kHz",
    "air": "8-20 kHz",
}
_EXCESS_TXT = {
    "sub": "sobra sub-grave: retumba y se come el headroom",
    "bass": "graves pesados: el mix suena embarrado abajo",
    "low_mid": "barro en los medios-graves: suena opaco, encajonado",
    "mid": "medios al frente: suena nasal, de radio",
    "high_mid": "medios-agudos ásperos: cansa al oído",
    "presence": "agudos filosos: hats/claps lastiman",
    "air": "demasiado brillo arriba: suena sibilante",
}
_DEFICIT_TXT = {
    "sub": "le falta sub-grave: el bombo no empuja",
    "bass": "flaco abajo: le falta cuerpo al bajo/bombo",
    "low_mid": "hueco en los medios-graves: suena delgado",
    "mid": "hueco en los medios: el sample queda lejos",
    "high_mid": "le falta definición en 2-4 kHz",
    "presence": "apagado: le falta presencia arriba",
    "air": "oscuro: le falta aire/brillo",
}


def reference_shares(levels_db: dict[str, float]) -> dict[str, float]:
    """Normalise reference levels to shares (dB) of the total power."""
    p = {b: 10.0 ** (v / 10.0) for b, v in levels_db.items()}
    tot = sum(p.values())
    return {b: 10.0 * np.log10(v / tot) for b, v in p.items()}


def _balance(spec: dsp.Spectrogram) -> SpectralBalance:
    vals = [round(dsp.band_power_db(spec, lo, hi), 2) for lo, hi in BAND_EDGES_HZ.values()]
    return SpectralBalance(low_db=vals[0], mid_db=vals[1], high_db=vals[2])


def _detail(spec: dsp.Spectrogram) -> dict[str, float]:
    return {b: float(np.mean(spec.band(lo, hi))) for b, (lo, hi) in DETAIL_BAND_EDGES_HZ.items()}


def measure_spectral(ctx: RenderContext) -> tuple[SpectralReport, list[Alarm]]:
    tl = ctx.timeline
    ref = ctx.config.reference
    mix_spec = ctx.spec("mix", fine=True)
    mix_detail = _detail(mix_spec)
    total = sum(mix_detail.values())
    alarms: list[Alarm] = []
    tracks_bal: dict[str, SpectralBalance] = {}
    tracks_detail: dict[str, dict[str, float]] = {}
    for tid in ctx.stems:
        spec = ctx.spec(tid, fine=True)
        tracks_bal[tid] = _balance(spec)
        tracks_detail[tid] = {b: float(dsp.power_db(v)) for b, v in _detail(spec).items()}
    report = SpectralReport(
        mix=_balance(mix_spec),
        tracks=tracks_bal,
        tracks_detail_db={
            t: {b: round(v, 2) for b, v in d.items()} for t, d in tracks_detail.items()
        },
        reference=ref,
    )
    if total <= 1e-14:  # silence: nothing to compare
        return report, alarms
    shares = {b: float(dsp.power_db(v / total)) for b, v in mix_detail.items()}
    ref_shares = reference_shares(ref.levels_db)
    dev = {b: shares[b] - ref_shares[b] for b in shares}
    report.mix_detail_db = {b: round(v, 2) for b, v in shares.items()}
    report.deviation_db = {b: round(v, 2) for b, v in dev.items()}

    # one alarm per band beyond tolerance, worst first, max 3 (they correlate)
    offenders = sorted(
        (b for b in dev if abs(dev[b]) > ref.tolerance_db.get(b, 6.0)),
        key=lambda b: -abs(dev[b]) / ref.tolerance_db.get(b, 6.0),
    )[:3]
    for band in offenders:
        d = dev[band]
        tol = ref.tolerance_db.get(band, 6.0)
        excess = d > 0
        culprit = _culprit(tracks_detail, band) if tracks_detail else None
        message, fix, automated = _advice(ctx, band, excess, culprit, d)
        sev = "error" if abs(d) > 2 * tol else "warn"
        if automated:
            sev = "info"
        who = f" (sobre todo `{culprit}`)" if culprit and excess else ""
        alarms.append(
            make_alarm(
                tl,
                f"spectral_{band}_{'excess' if excess else 'deficit'}",
                sev,
                f"{_BAND_HZ_TXT[band]}: {d:+.0f} dB vs referencia '{ref.name}' — "
                f"{(_EXCESS_TXT if excess else _DEFICIT_TXT)[band]}{who}.{message}",
                track=culprit if excess else None,
                value=d,
                threshold=tol if excess else -tol,
                unit="dB",
                fix=fix,
            )
        )
    return report, alarms


def _culprit(tracks_detail: dict[str, dict[str, float]], band: str) -> str | None:
    best = max(tracks_detail.items(), key=lambda kv: kv[1][band], default=None)
    if best is None or best[1][band] <= dsp.FLOOR_DB + 1:
        return None
    return best[0]


def _advice(
    ctx: RenderContext, band: str, excess: bool, culprit: str | None, d: float
) -> tuple[str, str, bool]:
    """(extra message, fix, caused-by-automation) for a band deviation."""
    song = ctx.song
    amount = max(2, int(round(abs(d) / 2)))
    track = ctx.track(culprit) if culprit else None
    samples = [t for t in song.tracks if t.clips is not None and not t.mute]
    automated = False
    extra = ""
    if band in ("presence", "air", "high_mid") and not excess:
        # dark: a closed low-pass is the usual cause; filter sweeps are on purpose
        for t in samples or song.tracks:
            lp = lp_path(t)
            if lp:
                path, hz = lp
                automated = is_automated(song, path)
                if automated:
                    extra = " Hay un barrido de low-pass automatizado: si es a propósito, ignoralo."
                    return extra, f"revisá la automatización de `{path}`.", True
                return extra, f"subí `{path}` (hoy {hz:g}) a ~16000.", False
        return extra, "subí el `gain_db` de los hats/clap o del sample unos dB.", False
    if track is None:
        return extra, "ajustá los `gain_db` de los tracks.", automated
    if excess and band in ("sub", "bass", "low_mid"):
        if track.clips is not None:
            to = {"sub": 60, "bass": 150, "low_mid": 250}[band]
            return (
                extra,
                hp_fix(track, to) + f" o bajá `tracks.{track.id}.gain_db` {amount} dB.",
                False,
            )
        return (
            extra,
            f"bajá `tracks.{track.id}.gain_db` (hoy {track.gain_db:g}) {amount} dB.",
            False,
        )
    if excess:
        lp = lp_path(track)
        if lp and band in ("presence", "air"):
            return extra, f"bajá `{lp[0]}` (hoy {lp[1]:g}) o el `gain_db` de `{track.id}`.", False
        return (
            extra,
            f"bajá `tracks.{track.id}.gain_db` (hoy {track.gain_db:g}) {amount} dB.",
            False,
        )
    # deficits in the low/mid region
    if band in ("sub", "bass"):
        basses = [t for t in song.tracks if t.instrument is not None and not t.mute]
        if basses:
            b = basses[0]
            return extra, f"subí `tracks.{b.id}.gain_db` (hoy {b.gain_db:g}) {amount} dB.", False
    elif samples:  # mids: the sample is what carries them in this genre
        smp = samples[0]
        hp = find_fx(smp, HighpassFx)
        if band == "low_mid" and hp and hp[1].cutoff_hz > 200:
            path = fx_param_path(smp, hp[0], "cutoff_hz")
            return extra, f"bajá `{path}` (hoy {hp[1].cutoff_hz:g}) a ~150.", False
        return extra, f"subí `tracks.{smp.id}.gain_db` (hoy {smp.gain_db:g}) {amount} dB.", False
    return extra, f"subí `tracks.{track.id}.gain_db` (hoy {track.gain_db:g}) {amount} dB.", False
