"""Low-end masking (40-150 Hz) and sidechain verification (spec 10, bullet 3).

Collisions: at every kick hit (times from the song's pattern) the kick's
low-band level in its own decay window is compared with every other
track's level in the same window. Both levels come from POST-fx track
stems (the kick's track stem; the kick lane stem only sets the hit
times and the window length).

Sidechain: for each sidechain fx in the song, the sidechained stem's
level is aligned on the key triggers and the per-trigger profiles
(each relative to its own pre-trigger level) are median-averaged. The
music's own fluctuations average out; the duck, locked to the
triggers, does not. Dip depth and recovery time are read off that
average profile.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import soundfile as sf

from ..schemas.metrics import Alarm, LowEndCollision, LowEndMasking, LowEndPair, SidechainCheck
from ..schemas.song import SidechainFx
from . import dsp
from .alarms import (
    fx_param_path,
    hp_fix,
    kick_lane,
    make_alarm,
    sample_tracks,
    sidechain_fix,
)
from .context import RenderContext

__all__ = ["measure_low_end", "BAND"]

BAND = (40.0, 150.0)
_HOP = 0.005
MAX_COLLISIONS = 12


def _window_mean(t: np.ndarray, p: np.ndarray, a: float, b: float) -> float:
    i0, i1 = np.searchsorted(t, [a, b])
    if i1 <= i0:
        i1 = min(i0 + 1, p.size)
    return float(np.mean(p[i0:i1])) if i1 > i0 else 0.0


def _kick_length(t: np.ndarray, p: np.ndarray, hits: list[float]) -> float:
    """Typical kick decay: time from the hit until -12 dB below its peak."""
    lens = []
    for h in hits[:32]:
        i0, i1 = np.searchsorted(t, [h - 0.005, h + 0.3])
        seg = p[i0:i1]
        if seg.size < 4 or seg.max() <= 0:
            continue
        k = int(np.argmax(seg))
        below = np.flatnonzero(seg[k:] < seg[k] * 10 ** (-1.2))
        lens.append((k + (below[0] if below.size else seg.size - k)) * _HOP)
    return float(np.clip(np.median(lens), 0.04, 0.2)) if lens else 0.1


def measure_low_end(ctx: RenderContext) -> tuple[LowEndMasking, list[Alarm]]:
    tl = ctx.timeline
    alarms: list[Alarm] = []
    envs: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def env(key: str) -> tuple[np.ndarray, np.ndarray]:
        if key not in envs:
            envs[key] = dsp.band_envelope(ctx.mono(key), ctx.sr, *BAND, hop_s=_HOP, win_s=0.02)
        return envs[key]

    energy = {}
    for tid in ctx.stems:
        track = ctx.track(tid)
        if track is not None and track.mute:
            continue
        energy[tid] = round(dsp.band_power_db(ctx.spec(tid, fine=True), *BAND), 2)
    audible = {k: v for k, v in energy.items() if v > dsp.FLOOR_DB + 1}
    dominant = max(audible, key=audible.get) if audible else None
    report = LowEndMasking(energy_db=energy, dominant_track=dominant)

    kl = kick_lane(ctx)
    if kl is None:
        ctx.skipped.append("low_end: no hay carril de bombo (pattern) para comparar")
    else:
        _collisions(ctx, kl, env, energy, report, alarms)
    report.sidechain = _sidechains(ctx, alarms)

    # the genre's signature: a sample that breathes with the kick
    if kl is not None and not report.sidechain:
        samples = sample_tracks(ctx)
        has_sc = any(isinstance(fx, SidechainFx) for tid in samples for fx in ctx.track(tid).fx)
        if samples and not has_sc:
            sample = ctx.track(samples[0])
            alarms.append(
                make_alarm(
                    tl,
                    "no_sidechain",
                    "info",
                    f"`{sample.id}` no respira con el bombo: el bombeo por sidechain es una "
                    "firma del future funk.",
                    track=sample.id,
                    fix=sidechain_fix(sample, kl[0], kl[1], 0.6),
                )
            )
    return report, alarms


def _collisions(ctx, kl, env, energy, report: LowEndMasking, alarms: list[Alarm]) -> None:
    th = ctx.config.thresholds
    tl = ctx.timeline
    ktrack, klane = kl
    report.kick = f"{ktrack}.{klane}"
    hits = [e.t for e in tl.lane_hits(ktrack).get(klane, [])]
    if not hits or ktrack not in ctx.stems:
        ctx.skipped.append("low_end: el bombo no suena en este render")
        return
    lane_key = f"{ktrack}.{klane}" if klane in ctx.lanes.get(ktrack, {}) else ktrack
    kt, kp_lane = env(lane_key)
    klen = _kick_length(kt, kp_lane, hits)
    kt, kp = env(ktrack)
    k_levels = np.array([_window_mean(kt, kp, h, h + klen) for h in hits])
    ref = max((10 ** (v / 10) for v in energy.values()), default=0.0)
    if ref <= 0 or float(np.median(k_levels)) < ref * 10 ** (th.low_end_min_level_db / 10):
        ctx.skipped.append("low_end: el bombo casi no tiene energía en 40-150 Hz")
        return
    collisions: list[LowEndCollision] = []
    for tid in ctx.stems:
        track = ctx.track(tid)
        if tid == ktrack or track is None or track.mute:
            continue
        if energy.get(tid, dsp.FLOOR_DB) < 10 * math.log10(ref) + th.low_end_min_level_db:
            continue
        t, p = env(tid)
        margins = []
        hit_col = []
        for h, kl_ in zip(hits, k_levels, strict=True):
            o = _window_mean(t, p, h, h + klen)
            m = 10 * math.log10(max(o, 1e-20) / max(kl_, 1e-20))
            margins.append(m)
            if kl_ > 0 and m > -th.collision_margin_db:
                bar, beat = tl.where(h)
                hit_col.append(
                    LowEndCollision(
                        at_s=round(h, 3),
                        bar=bar,
                        beat=beat,
                        track=tid,
                        kick_db=round(float(dsp.power_db(kl_)), 1),
                        track_db=round(float(dsp.power_db(o)), 1),
                        margin_db=round(m, 1),
                    )
                )
        med = float(np.median(margins))
        report.pairs.append(
            LowEndPair(
                track=tid,
                kick_hits=len(hits),
                collisions=len(hit_col),
                median_margin_db=round(med, 1),
            )
        )
        collisions += hit_col
        share = len(hit_col) / len(hits)
        if share < th.collision_warn_share or len(hit_col) < 2:
            continue
        sev = "error" if share >= th.collision_error_share and med >= 0.0 else "warn"
        is_sample = track.clips is not None
        if is_sample:
            what = (
                f"el bombo y los graves de `{tid}` se pisan en 40-150 Hz "
                f"({len(hit_col)} de {len(hits)} golpes): el bombo se come al sample"
            )
            fix = hp_fix(track, 150) + " o " + sidechain_fix(track, ktrack, klane, 0.7)
        else:
            what = (
                f"`{tid}` y el bombo suenan juntos en 40-150 Hz en {len(hit_col)} de "
                f"{len(hits)} golpes: se tapan y el grave se vuelve una bola"
            )
            fix = sidechain_fix(track, ktrack, klane, 0.6)
            if track.instrument is not None:
                fix += " o mové las notas del bajo a los contratiempos"
        if med >= 0:
            what += f" (`{tid}` está {med:+.0f} dB sobre el bombo)"
        worst = max(hit_col, key=lambda c: c.margin_db)
        alarms.append(
            make_alarm(
                tl,
                "low_end_collision",
                sev,
                what[0].upper() + what[1:] + ".",
                at_s=worst.at_s,
                where=[c.at_s for c in hit_col],
                track=tid,
                lane=klane,
                value=med,
                threshold=-th.collision_margin_db,
                unit="dB",
                count=len(hit_col),
                fix=fix + ".",
            )
        )
    collisions.sort(key=lambda c: -c.margin_db)
    report.collisions = collisions[:MAX_COLLISIONS]


def _sidechains(ctx: RenderContext, alarms: list[Alarm]) -> list[SidechainCheck]:
    th = ctx.config.thresholds
    tl = ctx.timeline
    checks: list[SidechainCheck] = []
    for track in ctx.song.tracks:
        if track.mute or track.id not in ctx.stems:
            continue
        for i, fx in enumerate(track.fx):
            if not isinstance(fx, SidechainFx) or fx.depth <= 0.0:
                continue
            check = _measure_duck(ctx, track.id, fx)
            if check is None:
                continue
            checks.append(check)
            depth_path = fx_param_path(track, i, "depth")
            exp = check.expected_dip_db
            if check.measured_dip_db < th.duck_min_db:
                alarms.append(
                    make_alarm(
                        tl,
                        "sidechain_missing",
                        "error",
                        f"`{track.id}` no respira con `{fx.key_track}`: el sidechain declarado "
                        f"(depth {fx.depth:g}, esperado -{exp:.0f} dB) no se oye "
                        f"(medido -{max(check.measured_dip_db, 0):.1f} dB).",
                        track=track.id,
                        value=check.measured_dip_db,
                        threshold=th.duck_min_db,
                        unit="dB",
                        fix=f"revisá `key_track`/`key_lane` de `{depth_path.rsplit('.', 1)[0]}`"
                        " "
                        "(¿el key track tiene golpes en este rango?) o subí `depth` a 0.7.",
                    )
                )
            elif check.measured_dip_db < th.duck_fraction * min(exp, 20.0):
                alarms.append(
                    make_alarm(
                        tl,
                        "sidechain_weak",
                        "warn",
                        f"El sidechain de `{track.id}` apenas se siente: baja "
                        f"{check.measured_dip_db:.1f} dB y con depth {fx.depth:g} deberían ser "
                        f"~{exp:.0f}.",
                        track=track.id,
                        value=check.measured_dip_db,
                        threshold=round(th.duck_fraction * min(exp, 20.0), 1),
                        unit="dB",
                        fix=f"subí `{depth_path}` a {min(0.9, fx.depth + 0.2):.1f} o revisá "
                        "que nada después del sidechain lo esté aplastando.",
                    )
                )
            gap = _median_gap(tl.triggers(fx.key_track, fx.key_lane))
            if (
                check.ducking
                and gap
                and (check.recovery_ms is None or check.recovery_ms > 0.9 * gap * 1000)
            ):
                rel_path = fx_param_path(track, i, "release_ms")
                alarms.append(
                    make_alarm(
                        tl,
                        "sidechain_no_recovery",
                        "warn",
                        f"El sidechain de `{track.id}` no alcanza a soltar entre golpes "
                        f"({gap * 1000:.0f} ms): el sample queda siempre agachado.",
                        track=track.id,
                        value=check.recovery_ms,
                        threshold=round(0.9 * gap * 1000, 1),
                        unit="ms",
                        fix=f"bajá `{rel_path}` (hoy {fx.release_ms:g}) a ~{gap * 400:.0f}.",
                    )
                )
    return checks


def _median_gap(times: list[float]) -> float | None:
    if len(times) < 2:
        return None
    return float(np.median(np.diff(times)))


_ENV_HOP = 0.002
_ENV_WIN = 0.005
_REF_HP = 200.0


def _envelope(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    return dsp.ms_envelope(dsp.highpass(y, sr, _REF_HP), sr, _ENV_HOP, _ENV_WIN)


def dry_reference(ctx: RenderContext, tid: str, times: np.ndarray) -> np.ndarray | None:
    """The un-processed chop audio's envelope on the stem's envelope grid.

    Each clip instance maps project time to source time through its
    chop's warp (resample: one rate; stretch: beat-wise on beats_s) and
    the source stem's envelope (cache/stems, read only over the chop
    span) is sampled there. NaN where there is no reference; None when no
    chop of the track has a readable source stem.
    """
    tl = ctx.timeline
    out = np.full(times.size, np.nan)
    cache: dict[str, tuple | None] = {}
    found = False
    for inst in tl.clip_instances(tid):
        if inst.chop is None:
            continue
        if inst.chop not in cache:
            cache[inst.chop] = _source_env(ctx, inst.chop)
        src = cache[inst.chop]
        if src is None:
            continue
        found = True
        src_t, src_p, to_src = src
        sel = (times >= inst.start_s) & (times < inst.end_s)
        u = (times[sel] - inst.start_s) / tl.spb
        out[sel] = np.interp(to_src(u), src_t, src_p, left=np.nan, right=np.nan)
    return out if found else None


def _source_env(ctx: RenderContext, chop_id: str):
    chop = next((c for c in ctx.song.chops if c.id == chop_id), None)
    analysis, span = ctx.analysis_for_chop(chop_id)
    if chop is None or analysis is None or span is None:
        return None
    if chop.stem == "mix":
        rel = analysis.file
    else:
        rel = getattr(analysis.stems, chop.stem, None) if analysis.stems else None
    if not rel:
        return None
    path = ctx.project.path(rel)
    if not path.is_file():
        return None
    try:
        info = sf.info(str(path))
        sr = int(info.samplerate)
        a = max(0, int((span[0] - 0.1) * sr))
        b = min(int(info.frames), int((span[1] + 0.1) * sr))
        x, _ = sf.read(str(path), start=a, stop=b, dtype="float64", always_2d=True)
    except (RuntimeError, sf.LibsndfileError):
        return None
    if x.shape[0] < 64:
        return None
    t, p = _envelope(dsp.mono(x), sr)
    t = t + a / sr
    start, end = span
    if chop.warp.mode == "resample":
        scale = (end - start) / chop.length_beats

        def to_src(u: np.ndarray) -> np.ndarray:
            return start + u * scale

    else:  # stretch: each source beat lands on a project beat
        beats = np.asarray(analysis.beats_s)
        idx = np.arange(beats.size, dtype=np.float64)
        b0 = float(np.interp(start, beats, idx))

        def to_src(u: np.ndarray) -> np.ndarray:
            return np.interp(b0 + u, idx, beats)

    return t, p, to_src


def _align_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> int:
    """Lag (frames) that best aligns envelope b (dB) onto a, from their rises."""
    ok = np.isfinite(a) & np.isfinite(b)
    if np.count_nonzero(ok) < 100:
        return 0
    da = np.diff(np.where(ok, a, 0.0), prepend=0.0).clip(min=0) * ok
    db_ = np.diff(np.where(ok, b, 0.0), prepend=0.0).clip(min=0) * ok
    best, best_v = 0, -np.inf
    n = da.size
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            v = float(np.dot(da[lag:], db_[: n - lag]))
        else:
            v = float(np.dot(da[: n + lag], db_[-lag:]))
        if v > best_v:
            best, best_v = lag, v
    return best


def duck_profile(
    stem_p: np.ndarray, dry_p: np.ndarray, times: np.ndarray, triggers: list[float], span_s: float
) -> tuple[np.ndarray, np.ndarray, int]:
    """Trigger-locked gain profile in dB: (tau_s, profile, n_triggers_used).

    gain(t) = stem / dry (dB). Static filters and the fader are a
    constant offset and slow automation is removed by referencing each
    trigger to its own pre-trigger gain; what is left, median-averaged
    over the triggers, is the duck.
    """
    finite = np.isfinite(dry_p)
    floor = float(np.max(dry_p[finite])) * 1e-5 if np.any(finite) else 0.0
    ok = finite & (dry_p > floor) & (stem_p > 1e-14)
    g = np.full(stem_p.size, np.nan)
    g[ok] = 10.0 * np.log10(stem_p[ok] / dry_p[ok])
    n_post = max(2, int(span_s / _ENV_HOP))
    n_pre0, n_pre1 = int(0.03 / _ENV_HOP), int(0.004 / _ENV_HOP)
    tau = np.arange(n_post) * _ENV_HOP
    profiles = []
    for t in triggers:
        i = int(np.searchsorted(times, t))
        if i - n_pre0 < 0 or i + n_post > g.size:
            continue
        pre = g[i - n_pre0 : i - n_pre1]
        post = g[i : i + n_post]
        if np.count_nonzero(np.isfinite(pre)) < 4:
            continue
        if np.count_nonzero(np.isfinite(post)) < n_post // 2:
            continue
        profiles.append(post - np.nanmedian(pre))
    if not profiles:
        return tau, np.zeros(n_post), 0
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        prof = np.nanmedian(np.stack(profiles), axis=0)
    return tau, np.nan_to_num(prof), len(profiles)


def _measure_duck(ctx: RenderContext, tid: str, fx: SidechainFx) -> SidechainCheck | None:
    th = ctx.config.thresholds
    tl = ctx.timeline
    trig = tl.triggers(fx.key_track, fx.key_lane)
    key = ctx.track(fx.key_track)
    if not trig and key is not None and key.clips is not None and fx.key_track in ctx.stems:
        o = dsp.onsets(ctx.mono(fx.key_track), ctx.sr).strongest(0.3)
        trig = [float(t) for t in o.times]
    y = ctx.mono(tid)
    if not np.any(y) or len(trig) < th.duck_min_triggers:
        ctx.skipped.append(f"sidechain de '{tid}': {len(trig)} disparos en el render")
        return None
    times, stem_p = _envelope(y, ctx.sr)
    dry = dry_reference(ctx, tid, times)
    if dry is None:
        track = ctx.track(tid)
        if track is not None and track.instrument is not None:
            ctx.skipped.append(
                f"sidechain de '{tid}': es un instrumento; falta una referencia seca "
                "para distinguir el bombeo de la envolvente de sus notas"
            )
            return None
        ctx.skipped.append(
            f"sidechain de '{tid}': sin el stem fuente del chop en cache/stems no se puede "
            "separar el bombeo del contenido del sample"
        )
        return None
    lag = _align_lag(10 * np.log10(stem_p + 1e-14), 10 * np.log10(np.nan_to_num(dry) + 1e-14), 10)
    if lag > 0:
        dry = np.concatenate([np.full(lag, np.nan), dry[:-lag]])
    elif lag < 0:
        dry = np.concatenate([dry[-lag:], np.full(-lag, np.nan)])
    gap = _median_gap(trig) or 0.5
    span = min(0.35, gap - 0.01)
    tau, prof, used = duck_profile(stem_p, dry, times, trig, span)
    if used < th.duck_min_triggers:
        ctx.skipped.append(
            f"sidechain de '{tid}': {used} disparos medibles (< {th.duck_min_triggers})"
        )
        return None
    prof = np.convolve(prof, np.ones(3) / 3.0, mode="same")
    search = prof[tau <= min(0.06, span)]
    k = int(np.argmin(search))
    dip = float(-search[k])
    rec = np.flatnonzero(prof[k:] >= -1.0)
    recovery = float(tau[k + rec[0]]) * 1000.0 if rec.size else None
    expected = min(40.0, -20.0 * math.log10(max(1.0 - fx.depth, 1e-2)))
    ducking = dip >= max(th.duck_min_db, th.duck_fraction * min(expected, 20.0))
    return SidechainCheck(
        track=tid,
        fx_id=fx.id,
        key_track=fx.key_track,
        key_lane=fx.key_lane,
        depth=fx.depth,
        release_ms=fx.release_ms,
        expected_dip_db=round(expected, 2),
        measured_dip_db=round(dip, 2),
        recovery_ms=None if recovery is None else round(recovery, 1),
        n_triggers=used,
        ducking=ducking,
    )
