"""Compact inline summary and version-to-version comparison.

The summary is injected into the agent's context on EVERY render, so it
is capped: one headline line plus at most ``MAX_ALARM_LINES`` alarm
lines (worst first), each with its fix. Everything else lives in
metrics.json.
"""

from __future__ import annotations

from ..schemas.metrics import Alarm, AlarmRef, BalanceReport, Metrics, MetricsDelta
from .alarms import SEVERITY_RANK, sort_alarms
from .balance import _ROLE

__all__ = ["summarize", "compare_metrics", "MAX_ALARM_LINES"]

MAX_ALARM_LINES = 5
_TAG = {"error": "PROBLEMA", "warn": "aviso", "info": "info"}


def _where(a: Alarm) -> str:
    parts = []
    if a.bar is not None:
        parts.append(f"c.{a.bar}" + (f" b.{a.beat:g}" if a.beat and a.beat != 1.0 else ""))
    if a.count and a.count > 1:
        parts.append(f"x{a.count}")
    return f" [{', '.join(parts)}]" if parts else ""


def summarize(m: Metrics) -> str:
    lo = m.loudness
    head = f"{m.version}: {lo.integrated_lufs:.1f} LUFS"
    if lo.target_lufs is not None:
        head += f" (target {lo.target_lufs:g})"
    head += f", pico {lo.true_peak_dbtp:+.1f} dBTP"
    if lo.plr_db is not None:
        head += f", PLR {lo.plr_db:.1f}"
    counts = {s: sum(1 for a in m.alarms if a.severity == s) for s in SEVERITY_RANK}
    if counts["error"] or counts["warn"]:
        head += f" | {counts['error']} problema(s), {counts['warn']} aviso(s)"
    else:
        head += (
            " | sin problemas en las comprobaciones realizadas"
            if m.skipped
            else " | sin problemas detectados"
        )
    lines = [head]
    shown = [a for a in sort_alarms(m.alarms) if a.severity != "info"][:MAX_ALARM_LINES]
    if len(shown) < MAX_ALARM_LINES:
        infos = [a for a in sort_alarms(m.alarms) if a.severity == "info"]
        shown += infos[: max(0, min(2, MAX_ALARM_LINES - len(shown)))]
    # the balance readout is always inlined: it answers "what sits where
    # in the mix" even when nothing alarms (WP18)
    bal = next((a for a in m.alarms if a.code == "balance"), None)
    if bal is not None and bal not in shown:
        shown.append(bal)
    for a in shown:
        line = f"- {_TAG[a.severity]} {a.code}{_where(a)}: {a.message}"
        if a.fix:
            line += f" -> {a.fix}"
        lines.append(line)
    rest = len(m.alarms) - len(shown)
    if rest > 0:
        lines.append(f"(+{rest} más en metrics.json)")
    if m.skipped:
        lines.append("Sin comprobar: " + "; ".join(m.skipped))
    return "\n".join(lines)


def _key(a: Alarm | AlarmRef) -> tuple[str, str | None]:
    return a.code, a.track


def _anchor(role: str, report: BalanceReport) -> tuple[str, float] | None:
    """The role a given role's balance is read against, and its level.

    kick/drums are read against the loudest melodic role (the sample or
    other sustained content); everything else against the drums — the
    genre's anchor. Falls back to each version's loudest role.
    """
    lv = {r: rb.level_db for r, rb in report.roles.items()}
    if role in ("kick", "drums"):
        ref = max((r for r in ("sample", "other") if r in lv), key=lambda r: lv[r], default=None)
        ref = ref or report.loudest
    else:
        ref = max((r for r in ("kick", "drums") if r in lv), key=lambda r: lv[r], default=None)
        ref = ref or report.loudest
    if ref is None or ref == role:
        return None
    return ref, lv[ref]


def _balance_notes(a: Metrics, b: Metrics, d: MetricsDelta) -> list[str]:
    """Role balance changes between two versions, in Spanish."""
    notes: list[str] = []
    la = a.balance.roles
    lb = b.balance.roles
    order = {r: i for i, r in enumerate(("sample", "bass", "kick", "drums", "other"))}
    for role in sorted(set(la) & set(lb), key=lambda r: order.get(r, 99)):
        d.balance_delta_db[role] = round(lb[role].level_db - la[role].level_db, 1)
        pa, pb = _anchor(role, a.balance), _anchor(role, b.balance)
        if pa is None or pb is None:
            continue
        gap = (lb[role].level_db - pb[1]) - (la[role].level_db - pa[1])
        if abs(gap) < 2.0:
            continue
        ref = _ROLE[pa[0]] if pa[0] == pb[0] else "el elemento más fuerte"
        verb = "subió" if gap > 0 else "bajó"
        notes.append(f"{_ROLE[role]} {verb} {abs(gap):.0f} dB respecto de {ref}")
    return notes


def _ref(a: Alarm) -> AlarmRef:
    return AlarmRef(code=a.code, severity=a.severity, track=a.track, message=a.message)


def _share_db(m: Metrics, bands: tuple[str, ...]) -> float | None:
    vals = [m.spectral.mix_detail_db.get(b) for b in bands]
    if any(v is None for v in vals):
        return None
    import math

    return 10.0 * math.log10(sum(10 ** (v / 10.0) for v in vals))  # type: ignore[operator]


def compare_metrics(a: Metrics, b: Metrics, *, same_db: float = 0.5) -> MetricsDelta:
    """Delta from version ``a`` to version ``b`` (b relative to a)."""
    d = MetricsDelta(version_a=a.version, version_b=b.version)
    notes: list[str] = []
    d.lufs_delta = round(b.loudness.integrated_lufs - a.loudness.integrated_lufs, 2)
    d.true_peak_delta_db = round(b.loudness.true_peak_dbtp - a.loudness.true_peak_dbtp, 2)
    if a.loudness.plr_db is not None and b.loudness.plr_db is not None:
        d.plr_delta_db = round(b.loudness.plr_db - a.loudness.plr_db, 2)
    d.loudness = (
        "same" if abs(d.lufs_delta) < same_db else "louder" if d.lufs_delta > 0 else "quieter"
    )
    if d.loudness != "same":
        notes.append(
            f"{'más fuerte' if d.loudness == 'louder' else 'más bajo'} ({d.lufs_delta:+.1f} LU)"
        )
    hi_a, hi_b = _share_db(a, ("presence", "air")), _share_db(b, ("presence", "air"))
    lo_a, lo_b = _share_db(a, ("sub", "bass")), _share_db(b, ("sub", "bass"))
    if hi_a is None or hi_b is None:  # older metrics: coarse bands
        hi_a = a.spectral.mix.high_db - a.spectral.mix.mid_db
        hi_b = b.spectral.mix.high_db - b.spectral.mix.mid_db
    if lo_a is None or lo_b is None:
        lo_a = a.spectral.mix.low_db - a.spectral.mix.mid_db
        lo_b = b.spectral.mix.low_db - b.spectral.mix.mid_db
    d.high_delta_db = round(hi_b - hi_a, 2)
    d.low_delta_db = round(lo_b - lo_a, 2)
    d.brightness = (
        "same" if abs(d.high_delta_db) < 1.0 else "brighter" if d.high_delta_db > 0 else "darker"
    )
    d.low_end = (
        "same" if abs(d.low_delta_db) < 1.0 else "heavier" if d.low_delta_db > 0 else "lighter"
    )
    if d.brightness != "same":
        notes.append(
            f"{'más brillante' if d.brightness == 'brighter' else 'más oscuro'} "
            f"({d.high_delta_db:+.1f} dB arriba)"
        )
    if d.low_end != "same":
        notes.append(
            f"graves {'más pesados' if d.low_end == 'heavier' else 'más livianos'} "
            f"({d.low_delta_db:+.1f} dB)"
        )
    if a.technical and b.technical:
        for tid, lb in b.technical.tracks.items():
            la = a.technical.tracks.get(tid)
            if la is not None and lb.rms_dbfs > -140 and la.rms_dbfs > -140:
                delta = round(lb.rms_dbfs - la.rms_dbfs, 2)
                d.track_level_delta_db[tid] = delta
                if abs(delta) >= 2.0:
                    notes.append(f"`{tid}` {delta:+.1f} dB")
    if a.balance and b.balance:
        notes += _balance_notes(a, b, d)
    keys_a = {_key(x): x for x in a.alarms if x.severity != "info"}
    keys_b = {_key(x): x for x in b.alarms if x.severity != "info"}
    d.alarms_fixed = [_ref(x) for k, x in keys_a.items() if k not in keys_b]
    d.alarms_introduced = [_ref(x) for k, x in keys_b.items() if k not in keys_a]
    d.alarms_kept = [_ref(x) for k, x in keys_b.items() if k in keys_a]
    if d.alarms_fixed:
        notes.append("arreglado: " + ", ".join(x.code for x in d.alarms_fixed))
    if d.alarms_introduced:
        notes.append("nuevo: " + ", ".join(x.code for x in d.alarms_introduced))
    d.notes = notes
    return d
