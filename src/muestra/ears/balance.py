"""Mix balance (WP18): the relative loudness of the roles in the mix.

The human hears the mix; the agent only sees ``gain_db`` numbers. The
owner's gap: an agent pushed the sample +9..+11 dB until only the record
was audible and ears said nothing — whole-file RMS was never a fair
yardstick anyway (a kick is silent most of the bar), so each role is
measured **while it sounds**:

- **drum lanes** (kick and the rest of the kit): mean K-weighted power
  inside ``[hit, hit + W]`` at the pattern's own hit times,
  ``W = min(0.3 s, 0.8 * median gap)``, on the per-lane stem, rescaled
  to the post-fx track bus by the ratio of bus power to lane-power over
  the union of the track's hit windows. Event-locked, so a sparse kick
  and a dense hat lane compare fairly.
- **sustained tracks** (bass, sample, other instruments): self-gated
  active-block loudness — 400 ms blocks every 100 ms, keeping only the
  blocks within ``balance_active_gate_db`` of the track's loudest one.
  Notes are where they are; the gate drops silence between them.

All levels are K-weighted (BS.1770) mean powers on the **pre-master**
stems converted to a LUFS-like dB, so they are comparable across roles:
what leaves each fader, before the master normalises toward
``target_lufs``. A role's level is the power-sum of its members'.

The role/reference split is musical, not generic: in future funk the
drums + bass are the engine and the sample is the body. Alarms measure
the rhythm section **against the loudest melodic role** (sample or
other sustained content) — a rhythm role under the drums' own bus is
normal and stays the low-end check's business. ``balance_buried`` uses
the loudest role of any kind: 16 dB under anything is inaudible.

Reference ranges are starting points calibrated on the real projects
under ``~/Documents/MuestraProjects`` (docs/ears.md) — nobody has
validated them by ear. Every threshold is configurable in
``ears.toml``.
"""

from __future__ import annotations

import math

import numpy as np

from ..schemas.metrics import Alarm, BalanceReport, RoleBalance
from . import dsp
from .alarms import _KICK_NAMES, bass_tracks, is_automated, kick_lane, make_alarm, sample_tracks
from .context import RenderContext

__all__ = ["measure_balance"]

#: role -> short Spanish name for the readout line
ROLE_NAME = {
    "kick": "bombo",
    "drums": "resto de batería",
    "bass": "bajo",
    "sample": "sample",
    "other": "otros",
}
#: role -> Spanish name with article, for messages
_ROLE = {
    "kick": "el bombo",
    "drums": "el resto de la batería",
    "bass": "el bajo",
    "sample": "el sample",
    "other": "los otros instrumentos",
}
#: role -> "debajo/encima <role>" phrasing (del/de la/de los)
_BENEATH = {
    "kick": "del bombo",
    "drums": "del resto de la batería",
    "bass": "del bajo",
    "sample": "del sample",
    "other": "de los otros instrumentos",
}
#: roles that carry the genre (can be "buried" or "too far back")
_RHYTHM = ("kick", "drums", "bass")
#: roles the rhythm section is compared against (the melodic body)
_MELODIC = ("sample", "other")
#: roles that can raise balance_buried
_BURIED_SUBJECTS = ("kick", "drums", "bass", "sample")

_MAX_HIT_WINDOW_S = 0.3
_ABS_FLOOR = 10.0 ** ((-70.0 + 0.691) / 10.0)  # -70 LUFS as mean K-power


def _lufs(power: float) -> float:
    return -0.691 + 10.0 * math.log10(max(float(power), 1e-300))


def _csum(ctx: RenderContext, key: str) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(ctx.wpower(key))])


def _window_energy(csum: np.ndarray, sr: int, times: np.ndarray, win_s: float) -> tuple[float, int]:
    """Summed power and samples over [t, t + win_s] windows."""
    n = csum.size - 1
    a = np.clip((times * sr).astype(int), 0, n)
    b = np.clip((times * sr + win_s * sr).astype(int), 0, n)
    energy = float(np.sum(csum[b] - csum[a]))
    return energy, int(np.sum(b - a))


def _median_gap(times: np.ndarray) -> float:
    return float(np.median(np.diff(times))) if times.size > 1 else 1.0


def _lane_power(
    ctx: RenderContext, tid: str, lane: str, hits: np.ndarray, corr: float
) -> float | None:
    """Mean K-power density (post-fx scale) inside this lane's hit windows."""
    w = min(_MAX_HIT_WINDOW_S, 0.8 * _median_gap(hits))
    if w <= 0.005:
        return None
    on_lane = lane in ctx.lanes.get(tid, {})
    csum = _csum(ctx, f"{tid}.{lane}" if on_lane else tid)
    e, n = _window_energy(csum, ctx.sr, hits, w)
    if n <= 0:
        return None
    return (e / n) * (corr if on_lane else 1.0)


def _bus_corr(ctx: RenderContext, tid: str, lanes: list[str], all_hits: np.ndarray) -> float:
    """post-fx scale: bus power / sum of lane powers over the union windows.

    Lane stems are pre track fx; the track stem is post fx. Over one
    identical window set their ratio is the fader/fx gain the lanes are
    missing (a compressor makes it approximate — documented).
    """
    if all_hits.size == 0 or tid not in ctx.stems:
        return 1.0
    have_lanes = [ln for ln in lanes if ln in ctx.lanes.get(tid, {})]
    if not have_lanes:
        return 1.0
    w = min(_MAX_HIT_WINDOW_S, 0.8 * _median_gap(all_hits))
    if w <= 0.005:
        return 1.0
    e_bus, _ = _window_energy(_csum(ctx, tid), ctx.sr, all_hits, w)
    e_lanes = sum(
        _window_energy(_csum(ctx, f"{tid}.{ln}"), ctx.sr, all_hits, w)[0] for ln in have_lanes
    )
    if e_lanes <= 0.0 or e_bus <= 0.0:
        return 1.0
    return e_bus / e_lanes


def _active_power(ctx: RenderContext, key: str, gate_db: float) -> float | None:
    """Self-gated active-block mean K-power of a sustained track stem."""
    p = ctx.wpower(key)
    blocks = dsp.loudness_blocks(ctx.audio(key), ctx.sr, 0.4, 0.1, power=p)
    if blocks.size == 0:
        return None
    mx = float(np.max(blocks))
    if mx <= _ABS_FLOOR:
        return None
    act = blocks[blocks > mx * 10.0 ** (-gate_db / 10.0)]
    act = act[act > _ABS_FLOOR]
    return float(np.mean(act)) if act.size else None


def _track_gain_fix(ctx: RenderContext, tid: str, delta_db: float) -> str:
    """'`gain_db` de X a ~Y' fix for a track, honouring automation."""
    tr = ctx.track(tid)
    cur = tr.gain_db if tr else 0.0
    if is_automated(ctx.song, f"tracks.{tid}.gain_db"):
        return f"`tracks.{tid}.gain_db` está automatizado: bajá los puntos ~{abs(delta_db):.0f} dB"
    verb = "subí" if delta_db > 0 else "bajá"
    return f"{verb} `tracks.{tid}.gain_db` de {cur:g} a ~{round(cur + delta_db):g}"


def _role_fix(ctx: RenderContext, role: str, members: list[str], delta_db: float) -> str:
    """Suggested level change for a role (applied to its loudest track)."""
    tid = members[0] if members else role
    base = _track_gain_fix(ctx, tid, delta_db)
    if role == "drums":
        base += f" (o los `gain_db` de los lanes en `tracks.{tid}.pattern.lanes`)"
    elif role == "kick":
        tr = ctx.track(tid)
        names = [
            ln
            for ln in (tr.pattern.lanes if tr and tr.pattern else [])
            if ln.lower() in _KICK_NAMES or ln.lower().startswith("kick")
        ]
        if names:
            base += f" (o `tracks.{tid}.pattern.lanes.{names[0]}.gain_db`)"
    return base


def measure_balance(ctx: RenderContext) -> tuple[BalanceReport | None, list[Alarm]]:
    th = ctx.config.thresholds
    tl = ctx.timeline
    alarms: list[Alarm] = []
    powers: dict[str, float] = {}
    members: dict[str, list[str]] = {}
    member_power: dict[str, dict[str, float]] = {}
    lanes_db: dict[str, float] = {}

    def add(role: str, tid: str, p: float) -> None:
        if p <= 0.0:
            return
        powers[role] = powers.get(role, 0.0) + p
        member_power.setdefault(role, {})[tid] = member_power.get(role, {}).get(tid, 0.0) + p
        if tid not in members.setdefault(role, []):
            members[role].append(tid)

    # --- pattern tracks: per-lane event-window loudness ---------------
    found_kick_lane = False
    for t in ctx.song.tracks:
        if t.pattern is None or t.mute or t.id not in ctx.stems:
            continue
        hits_by_lane = tl.lane_hits(t.id)
        all_hits = np.array(sorted(e.t for hs in hits_by_lane.values() for e in hs))
        corr = _bus_corr(ctx, t.id, list(t.pattern.lanes), all_hits)
        for lane in t.pattern.lanes:
            hits = np.array([e.t for e in hits_by_lane.get(lane, [])])
            if hits.size == 0:
                continue
            p = _lane_power(ctx, t.id, lane, hits, corr)
            if p is None:
                continue
            lanes_db[f"{t.id}.{lane}"] = round(_lufs(p), 1)
            is_kick = lane.lower() in _KICK_NAMES or lane.lower().startswith("kick")
            role = "kick" if is_kick else "drums"
            found_kick_lane = found_kick_lane or role == "kick"
            add(role, t.id, p)
    if not found_kick_lane:
        kl = kick_lane(ctx)  # energy fallback: the lane with most 40-150 Hz
        if kl is not None:
            hits = np.array([e.t for e in tl.lane_hits(kl[0]).get(kl[1], [])])
            if hits.size:
                p = _lane_power(ctx, kl[0], kl[1], hits, 1.0)
                if p:
                    lanes_db[f"{kl[0]}.{kl[1]}"] = round(_lufs(p), 1)
                    add("kick", kl[0], p)

    # --- sustained tracks ---------------------------------------------
    used = {tid for m in members.values() for tid in m}
    groups = {
        "bass": bass_tracks(ctx),
        "sample": sample_tracks(ctx),
    }
    groups["other"] = [
        t.id
        for t in ctx.song.tracks
        if t.id in ctx.stems
        and not t.mute
        and t.id not in used
        and t.id not in groups["bass"]
        and t.id not in groups["sample"]
    ]
    for role in ("bass", "sample", "other"):
        for tid in groups[role]:
            if tid in used or tid not in ctx.stems:
                continue
            p = _active_power(ctx, tid, th.balance_active_gate_db)
            if p is not None:
                add(role, tid, p)

    if not powers:
        return None, alarms

    levels = {r: _lufs(p) for r, p in powers.items()}
    order = sorted(levels, key=lambda r: -levels[r])
    loudest = order[0]
    levels_db = {r: round(levels[r] - levels[loudest], 1) for r in order}

    # keep only the loudest member track per role (the one the fix should name)
    reps = {r: [max(member_power[r], key=member_power[r].get)] for r in member_power}

    report = BalanceReport(
        roles={r: RoleBalance(level_db=round(levels[r], 1), tracks=members[r]) for r in order},
        lanes=lanes_db,
        loudest=loudest,
        levels_db=levels_db,
    )

    if len(order) < 2:
        readout = "balance: " + ", ".join(f"{ROLE_NAME[r]} {levels_db[r]:g}" for r in order) + " dB"
        alarms.append(make_alarm(tl, "balance", "info", readout))
        return report, alarms

    melodic_level = max((levels[r] for r in _MELODIC if r in levels), default=None)
    melodic_role = (
        max((r for r in _MELODIC if r in levels), key=lambda r: levels[r])
        if melodic_level is not None
        else None
    )
    runner_up = order[1]
    margin = levels[loudest] - levels[runner_up]
    buried_roles: set[str] = set()

    # --- buried: a core role >= buried_db under the loudest anything ---
    for role in order[1:]:
        if role not in _BURIED_SUBJECTS:
            continue
        gap = levels[loudest] - levels[role]
        if gap < th.balance_buried_db:
            continue
        buried_roles.add(role)
        tr = reps[role][0]
        fix = (
            _role_fix(ctx, role, reps[role], math.ceil(gap * 0.6))
            + " o "
            + _role_fix(ctx, loudest, reps[loudest], -math.ceil(gap * 0.6))
            + "; el master nivela hacia `master.target_lufs`: lo que cambia el "
            "balance es la relación, no subir todo"
        )
        alarms.append(
            make_alarm(
                tl,
                "balance_buried",
                "error",
                f"{_ROLE[role].capitalize()} queda {gap:.0f} dB por debajo "
                f"{_BENEATH[loudest]}: no se va a escuchar.",
                track=tr,
                value=-gap,
                threshold=-th.balance_buried_db,
                unit="dB",
                fix=fix + ".",
            )
        )

    # --- too far back: rhythm section under the melodic body ----------
    _BACK_MSG = {
        "kick": "el groove pierde la pegada",
        "drums": "la batería no empuja",
        "bass": "en el mix casi no se nota",
    }
    if melodic_level is not None:
        for role in _RHYTHM:
            if role not in levels or role in buried_roles:
                continue
            gap = melodic_level - levels[role]
            if gap < th.balance_back_db:
                continue
            tr = reps[role][0]
            fix = (
                _role_fix(ctx, role, reps[role], math.ceil(gap * 0.5))
                + " o "
                + _role_fix(ctx, melodic_role, reps[melodic_role], -math.ceil(gap * 0.5))
            )
            alarms.append(
                make_alarm(
                    tl,
                    f"balance_{role}_back",
                    "warn",
                    f"{_ROLE[role].capitalize()} queda {gap:.0f} dB por debajo "
                    f"{_BENEATH[melodic_role]}: {_BACK_MSG[role]}.",
                    track=tr,
                    value=-gap,
                    threshold=-th.balance_back_db,
                    unit="dB",
                    fix=fix + ".",
                )
            )

    # --- the sample itself too far back -------------------------------
    if "sample" in levels and "kick" in levels and "sample" not in buried_roles:
        gap = levels["kick"] - levels["sample"]
        if gap >= th.balance_sample_back_db:
            tr = reps["sample"][0]
            fix = (
                _role_fix(ctx, "sample", reps["sample"], math.ceil(gap * 0.5))
                + " o "
                + _role_fix(ctx, "kick", reps["kick"], -math.ceil(gap * 0.5))
            )
            alarms.append(
                make_alarm(
                    tl,
                    "balance_sample_back",
                    "warn",
                    f"El sample queda {gap:.0f} dB por debajo del bombo: se pierde el tema.",
                    track=tr,
                    value=-gap,
                    threshold=-th.balance_sample_back_db,
                    unit="dB",
                    fix=fix + ".",
                )
            )

    # --- too far forward ----------------------------------------------
    if "bass" in levels and "kick" in levels and "kick" not in buried_roles:
        over = levels["bass"] - levels["kick"]
        if over >= th.balance_front_db:
            alarms.append(
                make_alarm(
                    tl,
                    "balance_bass_front",
                    "warn",
                    f"El bajo queda {over:.0f} dB por encima del bombo: el "
                    "groove lo tiene que llevar el bombo.",
                    track=reps["bass"][0],
                    value=over,
                    threshold=th.balance_front_db,
                    unit="dB",
                    fix=_role_fix(ctx, "bass", reps["bass"], -math.ceil(over * 0.5))
                    + f" o {_role_fix(ctx, 'kick', reps['kick'], math.ceil(over * 0.5))}.",
                )
            )
    if "drums" in levels and "sample" in levels and "drums" not in buried_roles:
        over = levels["drums"] - levels["sample"]
        if over >= th.balance_drums_front_db:
            alarms.append(
                make_alarm(
                    tl,
                    "balance_drums_front",
                    "warn",
                    f"El resto de la batería queda {over:.0f} dB por encima "
                    "del sample: tapa el tema.",
                    track=reps["drums"][0],
                    value=over,
                    threshold=th.balance_drums_front_db,
                    unit="dB",
                    fix=_role_fix(ctx, "drums", reps["drums"], -math.ceil(over * 0.5)) + ".",
                )
            )

    # --- one element alone drives the master --------------------------
    if margin >= th.balance_dominant_db:
        tr = reps[loudest][0]
        alarms.append(
            make_alarm(
                tl,
                "balance_dominant",
                "warn",
                f"{_ROLE[loudest].capitalize()} manda {margin:.0f} dB sobre lo "
                f"segundo ({_ROLE[runner_up]}): subir `tracks.{tr}.gain_db` ya "
                "no lo hace sonar más — el master nivela y el limitador "
                "trabaja por ese track.",
                track=tr,
                value=margin,
                threshold=th.balance_dominant_db,
                unit="dB",
                fix=f"para mezclar, {_role_fix(ctx, loudest, reps[loudest], -6)} "
                "o subí el resto; nunca subas todo (la normalización lo anula).",
            )
        )

    # --- the readout, always -------------------------------------------
    readout = "balance: " + ", ".join(f"{ROLE_NAME[r]} {levels_db[r]:g}" for r in order) + " dB"
    alarms.append(make_alarm(tl, "balance", "info", readout))

    return report, alarms
