"""Musical description of a song diff, in Spanish (rioplatense) (WP5).

:func:`describe_song` turns the change between two songs into sentences the
way a producer would say them ("Subí el chop c1 un semitono", "Bajé el bajo
2 dB", "El bombo pasa de negras a un patrón con síncopa en el compás 4"),
not as field names. Rules:

* **Grouped**: a new track is one sentence, not thirty; an fx added together
  with its automation is one sentence ("Agregué un filtro pasa-bajos
  automatizado de 800 Hz a 18 kHz en los compases 1 a 4 del track sample");
  automation of a removed/added track or fx is folded into that sentence.
* **Musical**: drum patterns are described rhythmically (negras, corcheas,
  contratiempos, síncopa), notes as added/removed/transposed, delays in note
  values, filters as "abrí/cerré".
* **Total**: every field family of the song schema has a phrase; anything
  unanticipated (a field added to the schema later) still gets a readable
  generic sentence instead of being dropped.

It works on the same id-matched alignment as :mod:`muestra.versions.diff`,
so inserting or reordering things produces no phantom sentences.
"""

# ruff: noqa: E501  (Spanish phrase tables: long f-strings read better unbroken)

from __future__ import annotations

import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from ..schemas.song import BEATS_PER_BAR, parse_automation_target, parse_steps
from .diff import CLIPS, NOTES, align_items, fx_alignment, same

if TYPE_CHECKING:
    from ..schemas.song import Song

__all__ = ["describe_song", "musical_summary"]

_EPS = 1e-9


# ---------------------------------------------------------------------------
# small formatting helpers
# ---------------------------------------------------------------------------


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _n(v: Any) -> str:
    """Compact number: 18000.0 -> '18000', 0.60 -> '0.6', -2 -> '-2'."""
    if v is None:
        return "nada"
    if isinstance(v, bool):
        return "sí" if v else "no"
    if isinstance(v, (int, float)):
        s = f"{float(v):.3f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(v)


def _hz(v: Any) -> str:
    x = _f(v)
    return f"{_n(x / 1000)} kHz" if abs(x) >= 1000 else f"{_n(x)} Hz"


def _val(leaf: str, v: Any) -> str:
    """A value with the unit its parameter name implies."""
    if leaf.endswith("_hz") or leaf == "centre_frequency_hz":
        return _hz(v)
    if leaf.endswith("_db"):
        return f"{_n(v)} dB"
    if leaf.endswith("_ms"):
        return f"{_n(v)} ms"
    if leaf.endswith("_beats"):
        return f"{_n(v)} beats"
    if leaf == "target_lufs":
        return f"{_n(v)} LUFS"
    return _n(v)


def _plural(n: float, one: str, many: str) -> str:
    return one if abs(n - 1) < _EPS else many


def _beats(n: float) -> str:
    if abs(n - 0.5) < _EPS:
        return "medio beat"
    if abs(n - 1) < _EPS:
        return "un beat"
    return f"{_n(n)} beats"


def _semitones(n: float) -> str:
    n = abs(n)
    if abs(n - 1) < _EPS:
        return "un semitono"
    if abs(n - 0.5) < _EPS:
        return "medio semitono"
    return f"{_n(n)} semitonos"


def _del(np: str) -> str:
    """'el bajo' -> 'del bajo', 'la batería' -> 'de la batería'."""
    if np.startswith("el "):
        return "del " + np[3:]
    return "de " + np


def _al(np: str) -> str:
    if np.startswith("el "):
        return "al " + np[3:]
    return "a " + np


def _bare(np: str) -> str:
    """'el bombo' -> 'bombo' (drop the article)."""
    head, _, rest = np.partition(" ")
    return rest if head in ("el", "la", "los", "las") else np


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _base(ref: Any) -> str:
    """'presets/slap-bass-01.fxp' -> 'slap-bass-01'."""
    s = str(ref).rsplit("/", 1)[-1]
    return s.rsplit(".", 1)[0] if "." in s else s


def _bar_of(beat: float) -> int:
    return int(math.floor((beat - 1) / BEATS_PER_BAR + _EPS)) + 1


def _bars_text(lo: int, hi: int) -> str:
    if lo == hi:
        return f"el compás {lo}"
    if hi == lo + 1:
        return f"los compases {lo} y {hi}"
    return f"los compases {lo} a {hi}"


def _bar_list(bars: list[int]) -> str:
    bars = sorted(bars)
    if len(bars) == 1:
        return f"el compás {bars[0]}"
    if bars == list(range(bars[0], bars[-1] + 1)):
        return _bars_text(bars[0], bars[-1])
    return "los compases " + ", ".join(str(b) for b in bars[:-1]) + f" y {bars[-1]}"


# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------

_TRACK_NOUNS = {
    "drums": "la batería",
    "drum": "la batería",
    "bateria": "la batería",
    "kick": "el bombo",
    "bombo": "el bombo",
    "snare": "la caja",
    "hats": "los hi-hats",
    "hat": "el hi-hat",
    "bass": "el bajo",
    "bajo": "el bajo",
    "vocals": "la voz",
    "vocal": "la voz",
    "vox": "la voz",
    "voz": "la voz",
    "chords": "los acordes",
    "keys": "las teclas",
    "piano": "el piano",
    "guitar": "la guitarra",
    "guitarra": "la guitarra",
    "lead": "el lead",
    "pad": "el pad",
    "arp": "el arpegio",
    "perc": "la percusión",
    "strings": "las cuerdas",
    "brass": "los vientos",
    "melody": "la melodía",
    "melodia": "la melodía",
}

_LANE_NOUNS = {
    "kick": "el bombo",
    "bd": "el bombo",
    "bombo": "el bombo",
    "snare": "la caja",
    "sd": "la caja",
    "caja": "la caja",
    "clap": "el clap",
    "hat": "el hi-hat",
    "hihat": "el hi-hat",
    "hh": "el hi-hat",
    "chat": "el hi-hat cerrado",
    "ch": "el hi-hat cerrado",
    "ohat": "el hi-hat abierto",
    "oh": "el hi-hat abierto",
    "ride": "el ride",
    "crash": "el crash",
    "tom": "el tom",
    "rim": "el rim",
    "shaker": "el shaker",
    "perc": "la percusión",
    "cowbell": "el cowbell",
    "conga": "la conga",
    "tamb": "la pandereta",
    "clave": "la clave",
}

#: fx type -> (indefinite, definite, gender, bare name)
_FX_NOUNS = {
    "highpass": ("un filtro pasa-altos", "el filtro pasa-altos", "m", "filtro pasa-altos"),
    "lowpass": ("un filtro pasa-bajos", "el filtro pasa-bajos", "m", "filtro pasa-bajos"),
    "sidechain": ("un sidechain", "el sidechain", "m", "sidechain"),
    "compressor": ("un compresor", "el compresor", "m", "compresor"),
    "limiter": ("un limiter", "el limiter", "m", "limiter"),
    "gain": ("una ganancia", "la ganancia", "f", "ganancia"),
    "reverb": ("una reverb", "la reverb", "f", "reverb"),
    "delay": ("un delay", "el delay", "m", "delay"),
    "chorus": ("un chorus", "el chorus", "m", "chorus"),
    "phaser": ("un phaser", "el phaser", "m", "phaser"),
    "saturation": ("una saturación", "la saturación", "f", "saturación"),
    "plugin": ("un plugin", "el plugin", "m", "plugin"),
}

_PARAM_NOUNS = {
    "cutoff_hz": "el corte",
    "gain_db": "la ganancia",
    "depth": "la profundidad",
    "threshold_db": "el umbral",
    "ratio": "el ratio",
    "ceiling_db": "el techo",
    "drive_db": "la saturación",
    "wet_level": "el nivel de reverb",
    "dry_level": "el nivel seco",
    "room_size": "el tamaño de la sala",
    "damping": "el amortiguamiento",
    "width": "el ancho estéreo",
    "time_beats": "el tiempo",
    "feedback": "el feedback",
    "mix": "la mezcla",
    "rate_hz": "la velocidad",
    "attack_ms": "el ataque",
    "release_ms": "el release",
    "knee_db": "la rodilla",
    "makeup_gain_db": "la ganancia de compensación",
    "centre_delay_ms": "el retardo central",
    "centre_frequency_hz": "la frecuencia central",
}

_NOTE_VALUES = {
    0.25: "semicorcheas",
    0.5: "corcheas",
    0.75: "corcheas con puntillo",
    1.0: "negras",
    1.5: "negras con puntillo",
    2.0: "blancas",
    3.0: "blancas con puntillo",
    4.0: "redondas",
}

_STEM_OF = {
    "drums": "la batería",
    "bass": "el bajo",
    "vocals": "la voz",
    "other": "el resto de los instrumentos",
    "mix": "la mezcla completa",
}


def _track_np(tid: str) -> str:
    np = _TRACK_NOUNS.get(tid)
    if np:
        return np
    head = re.split(r"[-_0-9]", tid)[0]
    if head in _TRACK_NOUNS and head != tid:
        return f"{_TRACK_NOUNS[head]} ({tid})"
    return f"el track {tid}"


def _lane_np(lane: str) -> str:
    np = _LANE_NOUNS.get(lane)
    if np:
        return np
    head = re.split(r"[-_0-9]", lane)[0]
    if head in _LANE_NOUNS and head != lane:
        return f"{_LANE_NOUNS[head]} ({lane})"
    return f"el lane {lane}"


def _scope_np(tid: str | None) -> str:
    return "el master" if tid is None else _track_np(tid)


def _fx_kind(fx: dict) -> tuple[str, str, str, str]:
    t = str(fx.get("type"))
    ind, dfn, g, bare = _FX_NOUNS.get(t, (f"un fx {t}", f"el fx {t}", "m", f"fx {t}"))
    if t == "plugin" and fx.get("plugin"):
        ind = f"un plugin ({fx['plugin']})"
        dfn = f"el plugin {fx['plugin']}"
    return ind, dfn, g, bare


def _fx_def(fx: dict, chain: list[dict]) -> str:
    """'el filtro pasa-bajos' (+ its id when the chain has several of that type)."""
    _, dfn, _, _ = _fx_kind(fx)
    if fx.get("id") and sum(1 for f in chain if f.get("type") == fx.get("type")) > 1:
        return f"{dfn} '{fx['id']}'"
    return dfn


def _note_value(beats: float) -> str:
    for k, name in _NOTE_VALUES.items():
        if abs(beats - k) < 1e-6:
            return name
    return f"{_n(beats)} beats"


# ---------------------------------------------------------------------------
# rhythm
# ---------------------------------------------------------------------------

_SUBDIV = {0.25: "e", 0.5: "y", 0.75: "a"}


def _hit_positions(steps: str, spp: int) -> list[list[float]]:
    """Per bar, the beat positions (0-based within the bar) of every hit."""
    vel = parse_steps(steps)
    bars = max(1, len(vel) // spp)
    out: list[list[float]] = []
    for bar in range(bars):
        chunk = vel[bar * spp : (bar + 1) * spp]
        out.append([i * BEATS_PER_BAR / spp for i, v in enumerate(chunk) if v is not None])
    return out


def _rhythm_name(pos: list[float], spp: int) -> str:
    if not pos:
        return "silencio"
    s = set(round(p, 6) for p in pos)
    whole = [p for p in s if abs(p - round(p)) < _EPS]
    if s == {0.0, 1.0, 2.0, 3.0}:
        return "negras"
    if s == {0.5, 1.5, 2.5, 3.5}:
        return "contratiempos"
    if s == {i * 0.5 for i in range(8)}:
        return "corcheas"
    if len(s) == 16 and all(abs(p * 4 - round(p * 4)) < _EPS for p in s):
        return "semicorcheas"
    if spp % 3 == 0 and len(s) == 12 and all(abs(p * 3 - round(p * 3)) < _EPS for p in s):
        return "tresillos"
    if len(whole) == len(s):
        if s == {1.0, 3.0}:
            return "los tiempos 2 y 4"
        if s == {0.0, 2.0}:
            return "los tiempos 1 y 3"
        beats = sorted(int(p) + 1 for p in s)
        if len(beats) == 1:
            return f"un golpe en el tiempo {beats[0]}"
        return "golpes en los tiempos " + ", ".join(map(str, beats[:-1])) + f" y {beats[-1]}"
    return "un patrón con síncopa"


def _step_pos(pos: float) -> str:
    beat = int(pos + _EPS) + 1
    frac = round(pos - int(pos + _EPS), 6)
    if abs(frac) < _EPS:
        return f"el tiempo {beat}"
    sub = _SUBDIV.get(frac)
    return f"el '{sub}' del {beat}" if sub else f"el {beat}.{round(frac * 100):02d}"


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" y {items[-1]}"


def _steps_change(
    lane: str, tid: str, at_bar: int, va: str, vb: str, spp_a: int, spp_b: int
) -> list[str]:
    """Sentences for one lane whose steps changed."""
    subj = _cap(_lane_np(lane))
    if spp_a != spp_b or len(parse_steps(va)) != len(parse_steps(vb)):
        names_b = [_rhythm_name(p, spp_b) for p in _hit_positions(vb, spp_b)]
        return [f"Rehice el patrón {_del(_lane_np(lane))}: ahora toca {names_b[0]}"]
    va_vel, vb_vel = parse_steps(va), parse_steps(vb)
    pa, pb = _hit_positions(va, spp_a), _hit_positions(vb, spp_b)
    nbars = len(pa)
    groups: dict[tuple, list[int]] = {}
    for k in range(nbars):
        lo, hi = k * spp_a, (k + 1) * spp_a
        if va_vel[lo:hi] == vb_vel[lo:hi]:
            continue
        ca, cb = _rhythm_name(pa[k], spp_a), _rhythm_name(pb[k], spp_b)
        if ca != cb:
            key = ("class", ca, cb)
        else:
            adds = [_step_pos(p) for p in pb[k] if p not in pa[k]]
            rem = [_step_pos(p) for p in pa[k] if p not in pb[k]]
            hard, soft = [], []
            for i in range(lo, hi):
                x, y = va_vel[i], vb_vel[i]
                if x is not None and y is not None and abs(x - y) > _EPS:
                    (hard if y > x else soft).append(_step_pos((i - lo) * BEATS_PER_BAR / spp_a))
            key = ("detail", tuple(adds), tuple(rem), tuple(hard), tuple(soft))
        groups.setdefault(key, []).append(k + 1)
    if not groups:
        return [f"Cambié el patrón {_del(_lane_np(lane))}"]
    if len(groups) > 3:
        return [
            f"Reescribí el patrón {_del(_lane_np(lane))} (cambia en {_n(sum(map(len, groups.values())))} compases)"
        ]
    out: list[str] = []
    for key, bars in groups.items():
        where = ""
        if nbars > 1 and len(bars) < nbars:
            suffix = " del patrón" if at_bar > 1 else ""
            where = f" en {_bar_list(bars)}{suffix}"
        if key[0] == "class":
            out.append(f"{subj} pasa de {key[1]} a {key[2]}{where}")
        else:
            _, adds, rem, hard, soft = key
            acts: list[str] = []
            if adds:
                acts.append(
                    ("agregué un golpe en " if len(adds) == 1 else "agregué golpes en ")
                    + _join(list(adds))
                )
            if rem:
                acts.append(
                    ("saqué el golpe de " if len(rem) == 1 else "saqué los golpes de ")
                    + _join(list(rem))
                )
            if hard:
                acts.append("golpes más fuertes en " + _join(list(hard)))
            if soft:
                acts.append("golpes más suaves en " + _join(list(soft)))
            out.append(f"En {_lane_np(lane)} {'; '.join(acts)}{where}")
    return out


# ---------------------------------------------------------------------------
# automation helpers
# ---------------------------------------------------------------------------


def _bar_span(points: list) -> str:
    """'los compases 1 a 4' for points at bars 1 and 5 (bar 5 = start of bar 5)."""
    first, last = _f(points[0][0]), _f(points[-1][0])
    if abs(first - round(first)) < _EPS and abs(last - round(last)) < _EPS:
        lo, hi = int(round(first)), int(round(last)) - 1
        return _bars_text(lo, max(lo, hi))
    return f"desde el compás {_n(first)} hasta el {_n(last)}"


def _auto_values(leaf: str, auto: dict) -> str:
    pts = auto["points"]
    txt = f"de {_val(leaf, pts[0][1])} a {_val(leaf, pts[-1][1])}"
    if len(pts) > 2:
        txt += f" pasando por {len(pts) - 2} {_plural(len(pts) - 2, 'punto', 'puntos')} intermedios"
    return txt


def _curve_note(auto: dict) -> str:
    return {"step": " (a saltos)", "exp": "", "lin": ""}.get(auto.get("curve", "lin"), "")


# ---------------------------------------------------------------------------
# the describer
# ---------------------------------------------------------------------------


class _Describer:
    def __init__(self, a: Song, b: Song) -> None:
        self.da = a.model_dump(mode="json")
        self.db = b.model_dump(mode="json")
        self.a_tracks = {t["id"]: t for t in self.da.get("tracks", [])}
        self.b_tracks = {t["id"]: t for t in self.db.get("tracks", [])}
        self.a_auto = {x["target"]: x for x in self.da.get("automation", [])}
        self.b_auto = {x["target"]: x for x in self.db.get("automation", [])}
        self.consumed: set[str] = set()

    # -- automation bookkeeping ------------------------------------------------

    def _targets_for(self, scope: str, tid: str | None, fxid: str | None = None) -> list[str]:
        """Automation targets (in a or b) addressing a track/master, optionally one fx."""
        out: list[str] = []
        for t in [*self.b_auto, *(t for t in self.a_auto if t not in self.b_auto)]:
            parsed = parse_automation_target(t)
            if parsed is None:
                continue
            sc, ptid, pfx, _param = parsed
            if sc != scope or ptid != tid:
                continue
            if fxid is not None and pfx != fxid:
                continue
            out.append(t)
        return out

    def _auto_noun(self, target: str) -> str:
        parsed = parse_automation_target(target)
        if parsed is None:
            return f"'{target}'"
        scope, tid, fxid, param = parsed
        owner = _scope_np(tid) if scope == "tracks" else "el master"
        if fxid is None:
            if param == "gain_db":
                return f"el volumen {_del(owner)}"
            return f"el paneo {_del(owner)}"
        chain = self._chain(tid)
        fx = next((f for f in chain if f.get("id") == fxid), {"type": "plugin", "id": fxid})
        dfn = _fx_def(fx, chain)
        if param.startswith("params."):
            return f"el parámetro '{param[7:]}' {_del(dfn)} {_del(owner)}"
        noun = _PARAM_NOUNS.get(param, f"'{param}'")
        return f"{noun} {_del(dfn)} {_del(owner)}"

    def _chain(self, tid: str | None) -> list[dict]:
        if tid is None:
            return (
                (self.db.get("master") or {}).get("fx")
                or (self.da.get("master") or {}).get("fx")
                or []
            )
        t = self.b_tracks.get(tid) or self.a_tracks.get(tid) or {}
        return t.get("fx") or []

    def _auto_sentences(self, targets: list[str]) -> list[str]:
        out: list[str] = []
        for t in targets:
            self.consumed.add(t)
            xa, xb = self.a_auto.get(t), self.b_auto.get(t)
            leaf = t.rsplit(".", 1)[-1]
            noun = self.auto_noun_cached(t)
            if xa is None and xb is not None:
                out.append(
                    f"Automaticé {noun} {_auto_values(leaf, xb)} en {_bar_span(xb['points'])}"
                    + _curve_note(xb)
                )
            elif xb is None and xa is not None:
                out.append(f"Saqué la automatización {_del(noun)}")
            elif xa is not None and xb is not None and not same(xa, xb):
                if not same(xa["points"], xb["points"]):
                    out.append(
                        f"Rehice la automatización {_del(noun)}: ahora va {_auto_values(leaf, xb)} "
                        f"en {_bar_span(xb['points'])}"
                    )
                if xa.get("curve") != xb.get("curve"):
                    names = {"lin": "lineal", "exp": "exponencial", "step": "a saltos"}
                    out.append(
                        f"La automatización {_del(noun)} ahora tiene curva "
                        f"{names.get(xb.get('curve'), xb.get('curve'))}"
                    )
        return out

    def auto_noun_cached(self, target: str) -> str:
        return self._auto_noun(target)

    # -- top level -------------------------------------------------------------------

    def run(self) -> list[str]:
        out: list[str] = []
        out += self._meta()
        out += self._sources()
        out += self._chops()
        out += self._tracks()
        out += self._master()
        leftover = [
            t
            for t in [*self.b_auto, *(t for t in self.a_auto if t not in self.b_auto)]
            if t not in self.consumed
        ]
        out += self._auto_sentences(leftover)
        seen: set[str] = set()
        return [s for s in out if not (s in seen or seen.add(s))]

    # -- meta / sources / chops ------------------------------------------------

    def _meta(self) -> list[str]:
        a, b = self.da["meta"], self.db["meta"]
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            va, vb = a.get(k), b.get(k)
            if same(va, vb):
                continue
            if k == "bpm":
                word = "Subí" if _f(vb) > _f(va) else "Bajé"
                out.append(f"{word} el tempo de {_n(va)} a {_n(vb)} BPM")
            elif k == "key":
                out.append(f"Cambié la tonalidad de {va} a {vb}")
            elif k == "length_bars":
                word = "Alargué" if _f(vb) > _f(va) else "Acorté"
                out.append(f"{word} el tema de {_n(va)} a {_n(vb)} compases")
            elif k == "title":
                out.append(f"Renombré el tema a '{vb}'")
            elif k == "seed":
                out.append(f"Cambié el seed de {_n(va)} a {_n(vb)} (cambia todo lo aleatorio)")
            else:
                out.append(f"Cambié {k} del tema de {_n(va)} a {_n(vb)}")
        return out

    def _sources(self) -> list[str]:
        a = {s["id"]: s for s in self.da.get("sources", [])}
        b = {s["id"]: s for s in self.db.get("sources", [])}
        out: list[str] = []
        for sid in [*b, *(s for s in a if s not in b)]:
            xa, xb = a.get(sid), b.get(sid)
            if xa is None:
                out.append(f"Agregué la fuente {sid} ({xb['file']})")
            elif xb is None:
                out.append(f"Saqué la fuente {sid}")
            elif not same(xa, xb):
                out.append(f"La fuente {sid} ahora apunta a '{xb['file']}' (antes '{xa['file']}')")
        return out

    def _chops(self) -> list[str]:
        a = {c["id"]: c for c in self.da.get("chops", [])}
        b = {c["id"]: c for c in self.db.get("chops", [])}
        out: list[str] = []
        for cid in [*b, *(c for c in a if c not in b)]:
            ca, cb = a.get(cid), b.get(cid)
            if ca is None:
                stem = _STEM_OF.get(cb.get("stem"), cb.get("stem"))
                out.append(
                    f"Agregué el chop {cid}: {_n(cb['length_beats'])} beats {_del(stem)} de "
                    f"{cb['source']} (desde el downbeat {cb['start_downbeat']})"
                )
            elif cb is None:
                out.append(f"Saqué el chop {cid}")
            else:
                out += self._chop_change(cid, ca, cb)
        return out

    def _chop_change(self, cid: str, a: dict, b: dict) -> list[str]:
        out: list[str] = []
        for k in sorted((set(a) | set(b)) - {"id", "warp"}):
            va, vb = a.get(k), b.get(k)
            if same(va, vb):
                continue
            if k == "stem":
                out.append(f"El chop {cid} ahora corta de {_STEM_OF.get(vb, vb)}")
            elif k == "source":
                out.append(f"El chop {cid} ahora corta de la fuente {vb}")
            elif k == "start_downbeat":
                out.append(f"Moví el inicio del chop {cid} del downbeat {va} al {vb}")
            elif k == "start_offset_beats":
                d = _f(vb) - _f(va)
                word = "Atrasé" if d > 0 else "Adelanté"
                out.append(f"{word} el inicio del chop {cid} {_beats(abs(d))}")
            elif k == "length_beats":
                word = "Alargué" if _f(vb) > _f(va) else "Acorté"
                out.append(f"{word} el chop {cid} de {_n(va)} a {_n(vb)} beats")
            elif k == "fade_ms":
                out.append(f"Cambié el fade del chop {cid} de {_n(va)} a {_n(vb)} ms")
            else:
                out.append(f"Cambié {k} del chop {cid} de {_n(va)} a {_n(vb)}")
        wa, wb = a.get("warp") or {}, b.get("warp") or {}
        for k in sorted(set(wa) | set(wb)):
            va, vb = wa.get(k), wb.get(k)
            if same(va, vb):
                continue
            if k == "pitch_semitones":
                d = _f(vb) - _f(va)
                word = "Subí" if d > 0 else "Bajé"
                tail = f" (queda en {_n(vb)})" if abs(_f(va)) > _EPS else ""
                out.append(f"{word} el chop {cid} {_semitones(d)}{tail}")
            elif k == "mode":
                what = (
                    "resample (el tono acompaña al tempo, como un vinilo)"
                    if vb == "resample"
                    else "stretch (conserva el tono)"
                )
                out.append(f"Pasé el chop {cid} a {what}")
            else:
                out.append(f"Cambié {k} del warp del chop {cid} de {_n(va)} a {_n(vb)}")
        return out

    # -- tracks ------------------------------------------------------------------------

    def _tracks(self) -> list[str]:
        out: list[str] = []
        common_a = [t for t in self.a_tracks if t in self.b_tracks]
        common_b = [t for t in self.b_tracks if t in self.a_tracks]
        if common_a != common_b:
            out.append("Reordené los tracks")
        for tid in [*self.b_tracks, *(t for t in self.a_tracks if t not in self.b_tracks)]:
            ta, tb = self.a_tracks.get(tid), self.b_tracks.get(tid)
            if ta is None:
                out.append(self._track_added(tid, tb))
            elif tb is None:
                out.append(self._track_removed(tid, ta))
            else:
                out += self._track_change(tid, ta, tb)
        return out

    def _track_summary(self, tb: dict) -> str:
        """What a track is, for 'Agregué <track> (<summary>)'."""
        if tb.get("clips") is not None:
            clips = tb["clips"]
            if not clips:
                return "sin clips"
            chops = sorted({c["chop"] for c in clips if c.get("chop")})
            samples = sorted({_base(c["sample"]) for c in clips if c.get("sample")})
            bits = [f"chop {c}" for c in chops] + [f"sample {s}" for s in samples]
            return f"{len(clips)} {_plural(len(clips), 'clip', 'clips')} ({_join(bits)})"
        if tb.get("pattern") is not None:
            lanes = [_bare(_lane_np(k)) for k in tb["pattern"]["lanes"]]
            return "patrón de batería: " + _join(lanes)
        inst = tb.get("instrument") or {}
        bits = [f"instrumento {inst.get('plugin', '?')}"]
        if inst.get("preset"):
            bits.append(f"preset {_base(inst['preset'])}")
        if tb.get("notes") is not None:
            bits.append(f"{len(tb['notes'])} {_plural(len(tb['notes']), 'nota', 'notas')}")
        elif tb.get("midi"):
            bits.append(f"MIDI {tb['midi']}")
        return ", ".join(bits)

    def _track_added(self, tid: str, tb: dict) -> str:
        s = f"Agregué {_track_np(tid)} ({self._track_summary(tb)})"
        fxs = [_fx_kind(f)[3] for f in tb.get("fx") or []]
        if fxs:
            s += ", con " + _join(fxs)
        autos = self._targets_for("tracks", tid)
        self.consumed.update(autos)
        if autos:
            n = len(autos)
            s += f", más {n} {_plural(n, 'automatización', 'automatizaciones')}"
        if tb.get("mute"):
            s += " (muteado)"
        return s

    def _track_removed(self, tid: str, ta: dict) -> str:
        for t in self._targets_for("tracks", tid):
            self.consumed.add(t)
        return f"Saqué {_track_np(tid)}"

    def _track_change(self, tid: str, a: dict, b: dict) -> list[str]:
        np = _track_np(tid)
        out: list[str] = []
        # mixer
        if not same(a.get("gain_db"), b.get("gain_db")):
            d = _f(b["gain_db"]) - _f(a.get("gain_db"))
            out.append(f"{'Subí' if d > 0 else 'Bajé'} {np} {_n(abs(d))} dB")
        if not same(a.get("pan"), b.get("pan")):
            pa, pb = _f(a.get("pan")), _f(b.get("pan"))
            if abs(pb) < _EPS:
                out.append(f"Centré {np}")
            else:
                side = "derecha" if pb > 0 else "izquierda"
                out.append(f"Paneé {np} a la {side} ({_n(pa)} a {_n(pb)})")
        if a.get("mute") != b.get("mute"):
            out.append(f"{'Muteé' if b.get('mute') else 'Desmuteé'} {np}")
        if a.get("at_bar") != b.get("at_bar"):
            out.append(
                f"{_cap(np)} ahora entra en el compás {b.get('at_bar')} (antes en el {a.get('at_bar')})"
            )
        if a.get("until_bar") != b.get("until_bar"):
            if b.get("until_bar") is None:
                out.append(f"{_cap(np)} ahora suena hasta el final")
            else:
                out.append(f"{_cap(np)} ahora suena hasta el compás {int(b['until_bar']) - 1}")
        out += self._auto_sentences(
            [t for t in self._targets_for("tracks", tid) if parse_automation_target(t)[2] is None]
        )
        # content
        ca, cb = a.get("clips"), b.get("clips")
        if ca is not None and cb is not None:
            out += self._clips(tid, np, ca, cb)
        elif (
            (ca is None) != (cb is None)
            or (a.get("pattern") is None) != (b.get("pattern") is None)
            or (a.get("instrument") is None) != (b.get("instrument") is None)
        ):
            out.append(
                f"{_cap(np)} cambió de tipo: de {self._kind(a)} a {self._kind(b)} "
                f"({self._track_summary(b)})"
            )
        pa, pb = a.get("pattern"), b.get("pattern")
        if pa is not None and pb is not None:
            out += self._pattern(tid, np, b.get("at_bar", 1), pa, pb)
        ia, ib = a.get("instrument"), b.get("instrument")
        if ia is not None and ib is not None:
            out += self._instrument(np, ia, ib)
        if a.get("midi") != b.get("midi") and (a.get("instrument") and b.get("instrument")):
            if a.get("midi") and b.get("midi"):
                out.append(f"{_cap(np)} ahora toca {b['midi']} (antes {a['midi']})")
            elif b.get("midi"):
                out.append(
                    f"{_cap(np)} ahora toca el archivo MIDI {b['midi']} en vez de notas escritas"
                )
            elif a.get("midi"):
                out.append(f"{_cap(np)} ahora usa notas escritas en el song en vez de {a['midi']}")
        na, nb = a.get("notes"), b.get("notes")
        if na is not None and nb is not None:
            out += self._notes(np, na, nb)
        out += self._fx_chain(tid, a.get("fx") or [], b.get("fx") or [])
        return out

    @staticmethod
    def _kind(t: dict) -> str:
        if t.get("clips") is not None:
            return "clips"
        if t.get("pattern") is not None:
            return "patrón de batería"
        return "instrumento"

    # -- clips ---------------------------------------------------------------------------

    @staticmethod
    def _clip_what(c: dict) -> str:
        return f"el chop {c['chop']}" if c.get("chop") else f"el sample {_base(c.get('sample'))}"

    def _clips(self, tid: str, np: str, a: list[dict], b: list[dict]) -> list[str]:
        al = align_items(a, b, CLIPS)
        out: list[str] = []
        added, removed = [b[i] for i in al.added], [a[i] for i in al.removed]

        def where(c: dict) -> str:
            rep = f" (×{c['repeat']})" if c.get("repeat", 1) > 1 else ""
            off = (
                f" y {_n(c['offset_beats'])} beats" if abs(_f(c.get("offset_beats"))) > _EPS else ""
            )
            return f"en el compás {c['at_bar']}{off}{rep}"

        if len(added) > 3:
            out.append(f"Agregué {len(added)} clips {_al(np)}")
        else:
            for c in added:
                out.append(f"Agregué un clip {_del(self._clip_what(c))} {where(c)} {_del(np)}")
        if len(removed) > 3:
            out.append(f"Saqué {len(removed)} clips {_del(np)}")
        else:
            for c in removed:
                out.append(
                    f"Saqué un clip {_del(self._clip_what(c))} del compás {c['at_bar']} {_del(np)}"
                )
        for ia, ib in al.modified(a, b):
            out += self._clip_change(np, a[ia], b[ib])
        return out

    def _clip_change(self, np: str, x: dict, y: dict) -> list[str]:
        what = self._clip_what(y)
        out: list[str] = []
        for k in sorted((set(x) | set(y)) - {"warp"}):
            va, vb = x.get(k), y.get(k)
            if same(va, vb):
                continue
            if k == "at_bar":
                out.append(f"Moví el clip {_del(what)} del compás {va} al {vb} {_del(np)}")
            elif k == "offset_beats":
                d = _f(vb) - _f(va)
                out.append(
                    f"{'Atrasé' if d > 0 else 'Adelanté'} el clip {_del(what)} "
                    f"{_beats(abs(d))} {_del(np)}"
                )
            elif k == "repeat":
                out.append(
                    f"El clip {_del(what)} {_del(np)} ahora se repite {_n(vb)} {_plural(_f(vb), 'vez', 'veces')} (antes {_n(va)})"
                )
            elif k == "length_beats":
                word = "Alargué" if _f(vb) > _f(va) else "Acorté"
                out.append(f"{word} el clip {_del(what)} {_del(np)} de {_n(va)} a {_n(vb)} beats")
            elif k in ("chop", "sample"):
                out.append(
                    f"El clip del compás {y['at_bar']} {_del(np)} ahora usa {self._clip_what(y)} "
                    f"(antes {self._clip_what(x)})"
                )
            else:
                out.append(f"Cambié {k} del clip {_del(what)} {_del(np)} de {_n(va)} a {_n(vb)}")
        wa, wb = x.get("warp") or {}, y.get("warp") or {}
        for k in sorted(set(wa) | set(wb)):
            va, vb = wa.get(k), wb.get(k)
            if same(va, vb):
                continue
            if k == "pitch_semitones":
                d = _f(vb) - _f(va)
                out.append(
                    f"{'Subí' if d > 0 else 'Bajé'} el clip {_del(what)} {_del(np)} {_semitones(d)}"
                )
            elif k == "mode":
                out.append(f"El clip {_del(what)} {_del(np)} ahora usa warp {vb}")
            else:
                out.append(f"Cambié {k} del warp del clip {_del(what)} {_del(np)}")
        return out

    # -- patterns / instruments / notes --------------------------------------------------

    def _pattern(self, tid: str, np: str, at_bar: int, a: dict, b: dict) -> list[str]:
        out: list[str] = []
        spp_a, spp_b = a.get("steps_per_bar", 16), b.get("steps_per_bar", 16)
        if spp_a != spp_b:
            out.append(f"La grilla {_del(np)} ahora es de {spp_b} pasos por compás (antes {spp_a})")
        if a.get("bars") != b.get("bars"):
            out.append(
                f"El patrón {_del(np)} ahora dura {b.get('bars')} "
                f"{_plural(b.get('bars', 1), 'compás', 'compases')} (antes {a.get('bars')})"
            )
        if not same(a.get("swing"), b.get("swing")):
            sa, sb = _f(a.get("swing")), _f(b.get("swing"))
            if sa < _EPS:
                out.append(f"Le puse swing {_n(sb)} {_al(np)}")
            elif sb < _EPS:
                out.append(f"Saqué el swing {_del(np)}")
            else:
                out.append(f"{'Más' if sb > sa else 'Menos'} swing en {np} ({_n(sa)} a {_n(sb)})")
        la, lb = a.get("lanes") or {}, b.get("lanes") or {}
        for lane in [*lb, *(k for k in la if k not in lb)]:
            x, y = la.get(lane), lb.get(lane)
            if x is None:
                pos = _rhythm_name(_hit_positions(y["steps"], spp_b)[0], spp_b)
                out.append(
                    f"Agregué {_lane_np(lane)} {_del(np)} ({pos}, sample {_base(y['sample'])})"
                )
            elif y is None:
                out.append(f"Saqué {_lane_np(lane)} {_del(np)}")
            elif not same(x, y):
                out += self._lane_change(tid, np, at_bar, lane, x, y, spp_a, spp_b)
        return out

    def _lane_change(
        self, tid: str, np: str, at_bar: int, lane: str, x: dict, y: dict, spp_a: int, spp_b: int
    ) -> list[str]:
        out: list[str] = []
        if x.get("steps") != y.get("steps"):
            if same(parse_steps(x["steps"]), parse_steps(y["steps"])):
                out.append(
                    f"Reescribí el patrón {_del(_lane_np(lane))} con otra notación (suena igual)"
                )
            else:
                out += _steps_change(lane, tid, at_bar, x["steps"], y["steps"], spp_a, spp_b)
        if x.get("sample") != y.get("sample"):
            out.append(
                f"Cambié el sample {_del(_lane_np(lane))} de {_base(x['sample'])} a {_base(y['sample'])}"
            )
        if not same(x.get("gain_db"), y.get("gain_db")):
            d = _f(y.get("gain_db")) - _f(x.get("gain_db"))
            out.append(f"{'Subí' if d > 0 else 'Bajé'} {_lane_np(lane)} {_del(np)} {_n(abs(d))} dB")
        if not same(x.get("pan"), y.get("pan")):
            out.append(
                f"Paneé {_lane_np(lane)} {_del(np)} ({_n(x.get('pan'))} a {_n(y.get('pan'))})"
            )
        for k in sorted((set(x) | set(y)) - {"steps", "sample", "gain_db", "pan"}):
            if not same(x.get(k), y.get(k)):
                out.append(
                    f"Cambié {k} de {_lane_np(lane)} {_del(np)} de {_n(x.get(k))} a {_n(y.get(k))}"
                )
        return out

    def _instrument(self, np: str, a: dict, b: dict) -> list[str]:
        out: list[str] = []
        for k in sorted((set(a) | set(b)) - {"params"}):
            va, vb = a.get(k), b.get(k)
            if same(va, vb):
                continue
            if k == "preset":
                if va is None:
                    out.append(f"Le puse el preset {_base(vb)} {_al(np)}")
                elif vb is None:
                    out.append(f"Saqué el preset {_del(np)} (era {_base(va)})")
                else:
                    out.append(f"Cambié el preset {_del(np)} de {_base(va)} a {_base(vb)}")
            elif k == "plugin":
                out.append(f"Cambié el instrumento {_del(np)} de {va} a {vb}")
            else:
                out.append(f"Cambié {k} del instrumento {_del(np)} de {_n(va)} a {_n(vb)}")
        pa, pb = a.get("params") or {}, b.get("params") or {}
        for name in sorted(set(pa) | set(pb)):
            va, vb = pa.get(name), pb.get(name)
            if same(va, vb):
                continue
            if va is None:
                out.append(f"Fijé '{name}' del instrumento {_del(np)} en {_n(vb)}")
            elif vb is None:
                out.append(f"Saqué el ajuste '{name}' del instrumento {_del(np)}")
            else:
                word = "Subí" if _f(vb) > _f(va) else "Bajé"
                out.append(f"{word} '{name}' del instrumento {_del(np)} ({_n(va)} a {_n(vb)})")
        return out

    def _notes(self, np: str, a: list[dict], b: list[dict]) -> list[str]:
        al = align_items(a, b, NOTES)
        out: list[str] = []
        mods = al.modified(a, b)
        trans: list[float] = []
        other: Counter[str] = Counter()
        bars_touched: list[int] = []
        for ia, ib in mods:
            x, y = a[ia], b[ib]
            diff_keys = {k for k in set(x) | set(y) if not same(x.get(k), y.get(k))}
            bars_touched.append(_bar_of(_f(y["beat"])))
            if diff_keys == {"pitch"}:
                trans.append(_f(y["pitch"]) - _f(x["pitch"]))
            elif diff_keys == {"velocity"}:
                other["velocity"] += 1
            elif diff_keys == {"length_beats"}:
                other["longer" if _f(y["length_beats"]) > _f(x["length_beats"]) else "shorter"] += 1
            elif "beat" in diff_keys:
                other["moved"] += 1
            else:
                other["edited"] += 1
        if trans:
            uniq = set(round(t, 6) for t in trans)
            n = len(trans)
            whole = n == len(a) == len(b) and not al.added and not al.removed
            if len(uniq) == 1:
                d = trans[0]
                word = "Subí" if d > 0 else "Bajé"
                if whole:
                    out.append(f"{word} {np} {_semitones(d)}")
                else:
                    out.append(
                        f"{word} {_semitones(d)} {n} {_plural(n, 'nota', 'notas')} {_del(np)}"
                    )
            else:
                out.append(
                    f"Transpuse {n} {_plural(n, 'nota', 'notas')} {_del(np)} (intervalos distintos)"
                )
        for key, text in (
            ("velocity", "Cambié la dinámica de {n} {notas} {np}"),
            ("longer", "Alargué {n} {notas} {np}"),
            ("shorter", "Acorté {n} {notas} {np}"),
            ("moved", "Moví {n} {notas} {np}"),
            ("edited", "Modifiqué {n} {notas} {np}"),
        ):
            n = other.get(key, 0)
            if n:
                out.append(text.format(n=n, notas=_plural(n, "nota", "notas"), np=_del(np)))
        if al.added:
            bars = [_bar_of(_f(b[i]["beat"])) for i in al.added]
            n = len(al.added)
            out.append(
                f"Agregué {n} {_plural(n, 'nota', 'notas')} {_al(np)} en {_bars_text(min(bars), max(bars))}"
            )
        if al.removed:
            bars = [_bar_of(_f(a[i]["beat"])) for i in al.removed]
            n = len(al.removed)
            out.append(
                f"Saqué {n} {_plural(n, 'nota', 'notas')} {_del(np)} {_del(_bars_text(min(bars), max(bars)))}"
            )
        return out

    # -- fx -------------------------------------------------------------------------------

    def _fx_chain(self, tid: str | None, a: list[dict], b: list[dict]) -> list[str]:
        out: list[str] = []
        scope = "tracks" if tid is not None else "master"
        owner = _scope_np(tid)
        al = fx_alignment(a, b)
        for ia, ib in al.pairs:
            fa, fb = a[ia], b[ib]
            fxid = fb.get("id")
            if not same(fa, fb):
                out += self._fx_change(tid, owner, fa, fb, b)
            if fxid:
                out += self._auto_sentences(self._targets_for(scope, tid, fxid))
        for ia in al.removed:
            fa = a[ia]
            s = f"Saqué {_fx_def(fa, a)} {_del(owner)}"
            if fa.get("id"):
                targets = self._targets_for(scope, tid, fa["id"])
                if any(t in self.a_auto and t not in self.b_auto for t in targets):
                    s += " (y su automatización)"
                self.consumed.update(targets)
            out.append(s)
        for ib in al.added:
            fb = b[ib]
            targets = self._targets_for(scope, tid, fb.get("id")) if fb.get("id") else []
            added_autos = [t for t in targets if t in self.b_auto and t not in self.a_auto]
            if added_autos:
                first = added_autos[0]
                xb = self.b_auto[first]
                leaf = first.rsplit(".", 1)[-1]
                ind, _, g, _ = _fx_kind(fb)
                adj = "automatizada" if g == "f" else "automatizado"
                out.append(
                    f"Agregué {ind} {adj} {_auto_values(leaf, xb)} en {_bar_span(xb['points'])} "
                    f"{_del(owner)}{_curve_note(xb)}"
                )
                self.consumed.add(first)
                out += self._auto_sentences([t for t in targets if t != first])
            else:
                out.append(f"Agregué {self._fx_added(fb)} {_al(owner)}")
                out += self._auto_sentences(targets)
        if al.reordered:
            out.append(f"Cambié el orden de los efectos {_del(owner)}")
        return out

    def _fx_added(self, fx: dict) -> str:
        ind, _, _, _ = _fx_kind(fx)
        t = fx.get("type")
        if t in ("lowpass", "highpass"):
            return f"{ind} en {_hz(fx['cutoff_hz'])}"
        if t == "sidechain":
            key = _track_np(fx["key_track"])
            if fx.get("key_lane"):
                key = f"{_lane_np(fx['key_lane'])} {_del(key)}"
            return f"{ind} que responde {_al(key)} (profundidad {_n(fx['depth'])})"
        if t == "compressor":
            return f"{ind} ({_n(fx['ratio'])}:1, umbral {_n(fx['threshold_db'])} dB)"
        if t == "limiter":
            return f"{ind} (techo {_n(fx['ceiling_db'])} dB)"
        if t == "gain":
            return f"una ganancia de {_n(fx['gain_db'])} dB"
        if t == "delay":
            return f"{ind} de {_note_value(_f(fx['time_beats']))}"
        if t == "saturation":
            return f"{ind} ({_n(fx['drive_db'])} dB de drive)"
        if t == "plugin":
            preset = f" con el preset {_base(fx['preset'])}" if fx.get("preset") else ""
            return f"{ind}{preset}"
        return ind

    def _fx_change(
        self, tid: str | None, owner: str, a: dict, b: dict, chain: list[dict]
    ) -> list[str]:
        out: list[str] = []
        dfn = _fx_def(b, chain)
        if a.get("type") != b.get("type"):
            return [f"Cambié {_fx_def(a, chain)} {_del(owner)} por {_fx_kind(b)[0]}"]
        t = b.get("type")
        for k in sorted((set(a) | set(b)) - {"id", "type", "params"}):
            va, vb = a.get(k), b.get(k)
            if same(va, vb):
                continue
            out.append(self._fx_param(t, k, va, vb, dfn, owner))
        pa, pb = a.get("params") or {}, b.get("params") or {}
        for name in sorted(set(pa) | set(pb)):
            va, vb = pa.get(name), pb.get(name)
            if not same(va, vb):
                out.append(
                    f"Ajusté el parámetro '{name}' {_del(dfn)} {_del(owner)} de {_n(va)} a {_n(vb)}"
                )
        return out

    def _fx_param(self, t: str | None, k: str, va: Any, vb: Any, dfn: str, owner: str) -> str:
        up = _f(vb) > _f(va)
        o_in = f"en {owner}"
        of = _del(owner)
        rng = f"({_n(va)} a {_n(vb)})"
        if k == "cutoff_hz":
            if t == "lowpass":
                return f"{'Abrí' if up else 'Cerré'} {dfn} {of} de {_hz(va)} a {_hz(vb)}"
            if t == "highpass":
                return (
                    f"{'Subí' if up else 'Bajé'} el corte {_del(dfn)} {of} de {_hz(va)} a {_hz(vb)}"
                )
        if k == "order":
            return f"{_cap(dfn)} {of} ahora es de orden {_n(vb)} (antes {_n(va)})"
        if t == "sidechain":
            if k == "depth":
                return f"Sidechain {'más' if up else 'menos'} profundo {rng} {o_in}"
            if k == "key_track":
                return f"Ahora el sidechain {of} responde {_al(_track_np(str(vb)))} (antes {_al(_track_np(str(va)))})"
            if k == "key_lane":
                if vb is None:
                    return f"El sidechain {of} ahora responde a toda la pista, no a un solo lane"
                return f"El sidechain {of} ahora responde solo {_al(_lane_np(str(vb)))}"
            if k == "release_ms":
                return f"Release del sidechain {'más largo' if up else 'más corto'} ({_n(va)} a {_n(vb)} ms) {o_in}"
            if k == "attack_ms":
                return f"Ataque del sidechain {'más lento' if up else 'más rápido'} ({_n(va)} a {_n(vb)} ms) {o_in}"
        if t == "compressor":
            if k == "threshold_db":
                return f"{'Subí' if up else 'Bajé'} el umbral {_del(dfn)} {of} de {_n(va)} a {_n(vb)} dB ({'comprime menos' if up else 'comprime más'})"
            if k == "ratio":
                return f"{'Más' if up else 'Menos'} compresión {o_in} ({_n(va)}:1 a {_n(vb)}:1)"
            if k == "makeup_gain_db":
                return f"{'Subí' if up else 'Bajé'} la ganancia de compensación {_del(dfn)} {of} {_n(abs(_f(vb) - _f(va)))} dB"
            if k in ("attack_ms", "release_ms"):
                w = "ataque" if k == "attack_ms" else "release"
                return f"{_cap(w)} {_del(dfn)} {of} {'más lento' if up else 'más rápido'} ({_n(va)} a {_n(vb)} ms)"
            if k == "knee_db":
                return f"Rodilla {'más suave' if up else 'más dura'} en {dfn} {of} ({_n(va)} a {_n(vb)} dB)"
        if t == "limiter":
            if k == "ceiling_db":
                return f"{'Subí' if up else 'Bajé'} el techo {_del(dfn)} {of} a {_n(vb)} dB (antes {_n(va)})"
            if k == "release_ms":
                return f"Release {_del(dfn)} {of} {'más largo' if up else 'más corto'} ({_n(va)} a {_n(vb)} ms)"
        if t == "gain" and k == "gain_db":
            return f"{'Subí' if up else 'Bajé'} {_n(abs(_f(vb) - _f(va)))} dB la ganancia {of} ({_n(va)} a {_n(vb)} dB)"
        if t == "saturation" and k == "drive_db":
            return f"{'Más' if up else 'Menos'} saturación ({_n(va)} a {_n(vb)} dB) {o_in}"
        if t == "reverb":
            if k == "wet_level":
                return f"{'Más' if up else 'Menos'} reverb {rng} {o_in}"
            if k == "dry_level":
                return f"{'Más' if up else 'Menos'} señal seca en la reverb {of} {rng}"
            if k == "room_size":
                return f"Reverb {'más grande' if up else 'más chica'} {rng} {o_in}"
            if k == "damping":
                return f"Reverb {'más apagada' if up else 'más brillante'} {rng} {o_in}"
            if k == "width":
                return f"Reverb {'más ancha' if up else 'más angosta'} {rng} {o_in}"
        if t == "delay":
            if k == "time_beats":
                return (
                    f"El delay {of} ahora es de {_note_value(_f(vb))} (antes {_note_value(_f(va))})"
                )
            if k == "feedback":
                return f"{'Más' if up else 'Menos'} repeticiones en el delay {of} {rng}"
            if k == "mix":
                return f"{'Más' if up else 'Menos'} delay {o_in} {rng}"
        if t in ("chorus", "phaser"):
            name = "chorus" if t == "chorus" else "phaser"
            if k == "rate_hz":
                return f"{_cap(name)} {'más rápido' if up else 'más lento'} {of} ({_n(va)} a {_n(vb)} Hz)"
            if k == "depth":
                return f"{_cap(name)} {'más profundo' if up else 'más sutil'} {of} {rng}"
            if k == "mix":
                return f"{'Más' if up else 'Menos'} {name} {o_in} {rng}"
        if t == "plugin" and k == "preset":
            if va is None:
                return f"Le puse el preset {_base(vb)} {_al(dfn)} {of}"
            return f"Cambié el preset {_del(dfn)} {of} de {_base(va)} a {_base(vb)}"
        if t == "plugin" and k == "plugin":
            return f"Cambié el plugin {of} de {va} a {vb}"
        noun = _PARAM_NOUNS.get(k, f"'{k}'")
        return f"Cambié {noun} {_del(dfn)} {of} de {_val(k, va)} a {_val(k, vb)}"

    # -- master ---------------------------------------------------------------------------

    def _master(self) -> list[str]:
        a, b = self.da.get("master") or {}, self.db.get("master") or {}
        out: list[str] = []
        if not same(a.get("gain_db"), b.get("gain_db")):
            d = _f(b.get("gain_db")) - _f(a.get("gain_db"))
            out.append(f"{'Subí' if d > 0 else 'Bajé'} el master {_n(abs(d))} dB")
        out += self._auto_sentences(
            [t for t in self._targets_for("master", None) if parse_automation_target(t)[2] is None]
        )
        if not same(a.get("target_lufs"), b.get("target_lufs")):
            if b.get("target_lufs") is None:
                out.append("Saqué el objetivo de loudness del master")
            elif a.get("target_lufs") is None:
                out.append(
                    f"Le puse un objetivo de loudness de {_n(b['target_lufs'])} LUFS al master"
                )
            else:
                out.append(
                    f"El loudness objetivo del master ahora es {_n(b['target_lufs'])} LUFS "
                    f"(antes {_n(a['target_lufs'])})"
                )
        for k in sorted((set(a) | set(b)) - {"fx", "gain_db", "target_lufs"}):
            if not same(a.get(k), b.get(k)):
                out.append(f"Cambié {k} del master de {_n(a.get(k))} a {_n(b.get(k))}")
        out += self._fx_chain(None, a.get("fx") or [], b.get("fx") or [])
        return out


def describe_song(a: Song, b: Song) -> list[str]:
    """One Spanish sentence per musical change between two songs (no final
    period, capitalised). Empty when the songs are musically identical."""
    return _Describer(a, b).run()


def musical_summary(sentences: list[str], extra: list[str] | None = None) -> str:
    """Join sentences into one paragraph; ``extra`` (e.g. the metrics delta)
    is appended as a last sentence."""
    parts = [s.rstrip(".") for s in sentences]
    if extra:
        parts.append(_cap("; ".join(extra)))
    if not parts:
        return "Sin cambios musicales."
    return ". ".join(parts) + "."
