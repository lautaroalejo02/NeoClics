"""Clicks: discontinuities at clip edges and at the loop seam (spec 10, bullet 6).

Ears knows where every edge is (clip instances, note starts/ends, the
loop seam), so it only looks there. The score
(``dsp.edge_discontinuity``) is the waveform's linear-prediction error
(second difference) at the edge against its RMS over the 5 ms BEFORE the
edge: a step or kink from an un-faded cut is a spike, low-frequency
content is suppressed by ~(2*pi*f/sr)**2, and dense high-frequency
material being cut raises the reference, so cutting through hats does not
read as a click. A downbeat attack right after the edge does not hide the
step either, because the reference is taken before it.

Scanned: clip-track stems (chop edges), instrument stems (note edges),
and the loop seam (end -> start junction) of those stems (not of the mix,
where the bar-1 kick is an intended attack). Drum lanes are not scanned:
a drum hit IS an intentional discontinuity. For the same reason an
instrument note that starts out of (near) silence -- it rises at least
``click_onset_rise_db`` above what was sounding -- is its preset's attack,
not a click; note starts over a sustained sound (retriggers) and note ends
are still scanned. A note end that coincides with the next note's start
(legato) is scanned once, as that start, and a retrigger whose spike is
within ``click_attack_margin_db`` of the track's own attacks out of silence
(median sharpest second difference in their first 3 ms) is that attack, not
a click (WP12: Surge's FM Slap retriggers were flagged only when their
transient happened to land inside the +-search window).
"""

from __future__ import annotations

import numpy as np

from ..schemas.metrics import Alarm, ClickEvent, ClickReport
from . import dsp
from .alarms import make_alarm
from .context import RenderContext

__all__ = ["measure_clicks"]

MAX_EVENTS = 24


def _onset_rise_db(y: np.ndarray, i: int, sr: int) -> float:
    """Level of the 5 ms after an edge over the 5 ms before it, dB."""
    w = max(16, int(0.005 * sr))
    pre = y[max(0, i - w) : i]
    post = y[i : i + w]
    if pre.size == 0 or post.size == 0:
        return 0.0
    return dsp.db(float(np.sqrt(np.mean(post * post)))) - dsp.db(float(np.sqrt(np.mean(pre * pre))))


def _attack_spike(y: np.ndarray, i: int, sr: int) -> float:
    """Sharpest second difference in the first 3 ms of a note: how 'clicky' this
    preset's attack is by itself (FM slap transients are sharp)."""
    seg = y[max(0, i - 2) : i + int(0.003 * sr)]
    if seg.size < 3:
        return 0.0
    return float(np.max(np.abs(seg[2:] - 2.0 * seg[1:-1] + seg[:-2])))


def _severity(jump_db: float, floor_db: float) -> float:
    return float(np.clip((jump_db - floor_db) / 30.0, 0.0, 1.0))


def measure_clicks(ctx: RenderContext) -> tuple[ClickReport, list[Alarm]]:
    th = ctx.config.thresholds
    tl = ctx.timeline
    sr = ctx.sr
    search = max(1, int(round(th.click_edge_window_ms / 1000.0 * sr)))
    fades = {c.id: int(c.fade_ms / 1000.0 * sr) for c in ctx.song.chops}
    tail_fade = int(0.005 * sr)  # R7: renders end with a 5 ms fade-out
    min_jump = 10 ** (th.click_min_db / 20.0)
    events: list[ClickEvent] = []

    def event(track, j, ratio, jump, edge, ref, chop) -> ClickEvent:
        jd = dsp.db(jump)
        bar, beat = tl.where(j / sr)
        return ClickEvent(
            track=track,
            at_s=round(max(0.0, j / sr), 4),
            severity=round(_severity(jd, th.click_min_db), 3),
            bar=bar,
            beat=beat,
            edge=edge,
            clip=ref,
            chop=chop,
            ratio=round(min(ratio, 1e4), 1),
            level_db=round(jd, 1),
        )

    scanned: list[str] = []
    for track in ctx.song.tracks:
        if track.mute or track.id not in ctx.stems or track.pattern is not None:
            continue
        y = ctx.mono(track.id)
        if not np.any(y):
            continue
        scanned.append(track.id)
        edges: list[tuple[float, str, str | None, str | None]] = []  # t, edge, clip, chop
        if track.clips is not None:
            for c in tl.clip_instances(track.id):
                edges.append((c.start_s, "clip_start", c.ref, c.chop))
                edges.append((c.end_s, "clip_end", c.ref, c.chop))
        elif track.instrument is not None:
            notes = tl.notes(track.id)
            starts = [int(round(n.t * sr)) for n in notes]
            for n in notes:
                edges.append((n.t, "note_start", None, None))
                if n.dur_s:
                    end = int(round((n.t + n.dur_s) * sr))
                    # A note that ends where the next one starts (legato line) is that
                    # next note's attack, not a cut: scan it once, as a note start.
                    if not any(abs(end - k) <= search for k in starts):
                        edges.append((n.t + n.dur_s, "other", None, None))
        seen: list[int] = []
        candidates = []
        attack_jumps: list[float] = []
        for t, edge, ref, chop in sorted(edges, key=lambda e: e[0]):
            i = int(round(t * sr))
            if i <= search + 16 or i >= y.size - search - 2:
                continue  # render boundaries are the seam check's job
            gap = fades.get(chop, 0) if chop else 0
            ratio, jump, j = dsp.edge_discontinuity(y, i, sr, search, gap)
            if ratio < th.click_ratio or jump < min_jump:
                if edge == "note_start" and _onset_rise_db(y, i, sr) >= th.click_onset_rise_db:
                    attack_jumps.append(_attack_spike(y, i, sr))
                continue
            if edge == "note_start" and _onset_rise_db(y, i, sr) >= th.click_onset_rise_db:
                # the preset's own transient, out of silence: remember how sharp it is
                # (its spike may land a few samples after the nominal note time)
                attack_jumps.append(_attack_spike(y, i, sr))
                continue
            candidates.append((j, ratio, jump, edge, ref, chop))
        # WP12 (skill finding 10): a FM-slap retrigger over a still-sounding note has
        # the same transient as every attack out of silence, so it is not a click
        # unless it is clearly bigger than this track's own clean attacks.
        attack_ref = float(np.median(attack_jumps)) if len(attack_jumps) >= 3 else None
        for j, ratio, jump, edge, ref, chop in candidates:
            if (
                edge == "note_start"
                and attack_ref is not None
                and jump <= attack_ref * 10 ** (th.click_attack_margin_db / 20.0)
            ):
                continue
            if edge == "note_start":
                edge = "other"
            if any(abs(j - k) <= 2 * search for k in seen):
                continue
            seen.append(j)
            events.append(event(track.id, j, ratio, jump, edge, ref, chop))

    # loop seam: the player loops the render end -> start. Only the scanned
    # stems: on the mix, the downbeat kick at bar 1 is an intended attack.
    seam_w = max(64, int(0.02 * sr))
    for key in scanned:
        y = ctx.mono(key)
        if y.size < 4 * seam_w or not np.any(y):
            continue
        junction = np.concatenate([y[-seam_w:], y[:seam_w]])
        ratio, jump, _j = dsp.edge_discontinuity(junction, seam_w, sr, search, tail_fade)
        if ratio < th.click_ratio or jump < min_jump:
            continue
        inst = tl.clip_instances(key)
        first = inst[0] if inst and inst[0].start_s < 0.002 else None
        events.append(
            event(
                key,
                0,
                ratio,
                jump,
                "loop_seam",
                first.ref if first else None,
                first.chop if first else None,
            )
        )

    events.sort(key=lambda e: (-e.severity, e.at_s))
    alarms = _alarms(ctx, events)
    kept = sorted(events[:MAX_EVENTS], key=lambda e: e.at_s)
    return ClickReport(count=len(events), events=kept), alarms


def _alarms(ctx: RenderContext, events: list[ClickEvent]) -> list[Alarm]:
    th = ctx.config.thresholds
    tl = ctx.timeline
    groups: dict[tuple, list[ClickEvent]] = {}
    for e in events:
        kind = "seam" if e.edge == "loop_seam" else "edge"
        groups.setdefault((kind, e.track, e.chop), []).append(e)
    out: list[Alarm] = []
    # a seam click on the mix that is explained by a track's seam click is one problem
    track_seams = {k[1] for k in groups if k[0] == "seam" and k[1] is not None}
    for (kind, track, chop), evs in groups.items():
        if kind == "seam" and track is None and track_seams:
            continue
        worst = max(evs, key=lambda e: e.level_db or -200)
        sev = "error" if (worst.level_db or -200) > th.click_error_db else "warn"
        if kind == "seam":
            who = f"`{track}`" if track else "el mix"
            msg = f"Click en la vuelta del loop ({who}): el final no empalma con el principio."
            fix = _fade_fix(ctx, chop) or (
                "agregá fade al primer clip (`fade_ms` del chop) o revisá que nada suene "
                "cortado al empezar el compás 1."
            )
            code = "loop_seam_click"
        else:
            track_obj = ctx.track(track) if track else None
            if track_obj is not None and track_obj.instrument is not None:
                msg = f"Clicks en los bordes de las notas de `{track}` ({len(evs)})."
                fix = (
                    f"usá un preset con ataque/release más suaves en `tracks.{track}.instrument` "
                    "o evitá notas que se corten en seco."
                )
            else:
                where = f" del chop `{chop}`" if chop else ""
                msg = (
                    f"Clicks en los bordes{where} en `{track}` ({len(evs)}): se oyen como "
                    "un 'tic' al repetir."
                )
                fix = _fade_fix(ctx, chop) or "agregá fades en los bordes de los clips."
            code = "click"
        out.append(
            make_alarm(
                tl,
                code,
                sev,
                msg,
                at_s=worst.at_s,
                where=[e.at_s for e in evs],
                track=track,
                clip=worst.clip,
                chop=chop,
                value=worst.level_db,
                threshold=th.click_min_db,
                unit="dBFS",
                count=len(evs),
                fix=fix,
            )
        )
    return out


def _fade_fix(ctx: RenderContext, chop_id: str | None) -> str | None:
    if not chop_id:
        return None
    chop = next((c for c in ctx.song.chops if c.id == chop_id), None)
    if chop is None:
        return None
    new = max(10.0, chop.fade_ms * 2)
    return f"subí `chops.{chop.id}.fade_ms` de {chop.fade_ms:g} a {new:g}."
