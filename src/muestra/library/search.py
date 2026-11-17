"""Library search (WP4): free text in English + Spanish, hard filters,
quality-aware ranking and "similar to" by feature distance.

The agent cannot hear. Everything it needs to *justify* a pick comes back
on each hit: which query terms matched, how well BPM/key fit, the quality
prior (clean / unclipped / sane level), a plain-language sound summary and
(with ``detail="full"``) the measured features.

Vocabulary, scoring weights and the query grammar are documented in
docs/library.md.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..music import KeyParseError, parse_key
from ..schemas._base import LIBRARY_SCHEME
from ..schemas.library import LibraryHit, LibraryIndexEntry
from .classify import bpm_relation
from .core import LoadedIndex, hit_from_entry, loaded_index, root_label

if TYPE_CHECKING:
    from ..project.core import Project

__all__ = [
    "SearchOutcome",
    "compatible_key",
    "describe",
    "parse_query",
    "quality",
    "search",
]

# ---------------------------------------------------------------------------
# vocabulary: word -> concepts
# ---------------------------------------------------------------------------

_TYPE, _SUB, _KIND, _TAG = "type", "subtype", "kind", "tag"

#: word -> [(aspect, ((value, weight), ...))]
_VOCAB: dict[str, list[tuple[str, tuple[tuple[str, float], ...]]]] = {}


def _norm(text: str) -> str:
    """Lowercase and strip accents (``güiro`` -> ``guiro``)."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _v(words: str, aspect: str, *values: str) -> None:
    parsed: list[tuple[str, float]] = []
    for v in values:
        val, _, w = v.partition(":")
        parsed.append((val, float(w) if w else 1.0))
    for word in words.split():
        _VOCAB.setdefault(_norm(word), []).append((aspect, tuple(parsed)))


# drum types (English + Spanish)
_v("kick kicks bombo bombos bassdrum bd bumbo", _TYPE, "kick")
_v("snare snares caja redoblante redoblantes tambor snr sd", _TYPE, "snare")
_v("clap claps palmas palma palmada handclap aplauso", _TYPE, "clap")
_v("chat", _TYPE, "chat")
_v("ohat", _TYPE, "ohat")
_v("hat hats hihat hihats charles charleston hi-hat", _TYPE, "hat", "chat", "ohat")
_v("tom toms timbal", _TYPE, "tom")
_v("cym cymbal cymbals platillo platillos", _TYPE, "cym")
_v("crash crashes", _SUB, "crash")
_v("ride", _SUB, "ride")
_v("splash", _SUB, "splash")
_v("china", _SUB, "china")
_v("gong", _SUB, "gong")
_v("rim rimshot rimshots aro sidestick", _TYPE, "rim")
_v("perc percs percussion percusion", _TYPE, "perc")
_v("conga congas tumbadora", _SUB, "conga")
_v("cowbell cencerro", _SUB, "cowbell")
_v("shaker shakers shake", _SUB, "shaker")
_v("maraca maracas", _SUB, "maracas")
_v("tamb tambourine pandereta", _SUB, "tambourine")
_v("bongo bongos bongo", _SUB, "bongo")
_v("block woodblock bloque", _SUB, "block")
_v("bell bells campana agogo", _SUB, "bell")
_v("guiro", _SUB, "guiro")
_v("claves clave", _SUB, "claves")
_v("djembe", _SUB, "djembe")
_v("cajon", _SUB, "cajon")
_v("triangle triangulo", _SUB, "triangle")
_v("timbale timbales", _SUB, "timbale")
_v("snap chasquido", _SUB, "snap")
_v("fill fills relleno rellenos redoble roll", _TYPE, "fill")
_v("drums beat beats groove ritmo bateria", _TYPE, "drums")
_v("bass bajo", _TYPE, "bass")
_v("keys piano rhodes teclado teclas organ organo epiano wurlitzer", _TYPE, "keys")
_v("synth synths sintetizador arp arpeggio lead stab", _TYPE, "synth")
_v("guitar guitars guitarra", _TYPE, "guitar")
_v("pad pads strings cuerdas", _TYPE, "pad")
_v("vox vocal vocals voz coro choir", _TYPE, "vox")
_v("fx riser risers sweep impact whoosh efecto efectos", _TYPE, "fx")
# kinds
_v("loop loops bucle bucles lazo", _KIND, "loop")
_v("oneshot oneshots hit golpe", _KIND, "one-shot")
_v("phrase phrases frase", _KIND, "phrase")
# descriptive tags (see docs/library.md for thresholds)
_v("punchy punch punzante pegada", _TAG, "punchy")
_v("boomy retumbante", _TAG, "boomy")
_v("subby sub subs subgrave grave graves", _TAG, "subby")
_v("clicky click", _TAG, "clicky")
_v("short corto corta seco seca breve", _TAG, "short")
_v("long largo larga cola", _TAG, "long")
_v("bright brillante agudo brillo", _TAG, "bright")
_v("dark oscuro oscura opaco", _TAG, "dark")
_v("noisy ruidoso ruidosa", _TAG, "noisy")
_v("tonal afinado", _TAG, "tonal")
_v("tuned", _TAG, "tuned")
_v("clean limpio limpia", _TAG, "clean")
_v("hot caliente", _TAG, "hot")
_v("quiet bajito silencioso", _TAG, "quiet")
_v("stereo estereo", _TAG, "stereo", "wide")
_v("wide ancho", _TAG, "wide")
_v("lofi lo-fi", _TAG, "lofi")
# machines / sources
_v("808", _TAG, "808")
_v("909", _TAG, "909", "tr8:0.6")
_v("606", _TAG, "606")
_v("707 727", _TAG, "707")
_v("linn linndrum", _TAG, "linn", "80s:0.5")
_v("tr8 tr-8", _TAG, "tr8", "909:0.6")
_v("tr808", _TAG, "808")
_v("tr909", _TAG, "909", "tr8:0.6")
_v("tr606", _TAG, "606")
_v("tr707", _TAG, "707")
_v("roland", _TAG, "roland")
_v("sp1200", _TAG, "sp1200")
_v("mpc", _TAG, "mpc60")
_v("dmx oberheim", _TAG, "dmx")
_v("tape cinta cassette valvula valve", _TAG, "tape")
_v("goldbaby", _TAG, "goldbaby")
_v("legowelt", _TAG, "legowelt")
_v("acoustic acoustico acustico acustica", _TAG, "acoustic")
_v("vinyl vinilo", _TAG, "vinyl")
_v("electronic electro electronica electronico", _TAG, "electronic")
_v("analog analogico", _TAG, "electronic:0.5")
_v("machine maquina drumbox", _TAG, "vintage:0.5", "electronic:0.5")
_v("vintage retro", _TAG, "vintage", "80s:0.5")
_v("80s ochentas ochenta", _TAG, "80s")
_v("gated", _TAG, "gated-reverb")
_v("foley", _TAG, "foley")
_v("chiptune", _TAG, "chiptune")
_v("world mundo", _TAG, "hand-percussion:0.7")
_v("french-house frenchhouse", _TAG, "french-house")
_v("digital", _TAG, "digital")

#: multi-word phrases rewritten to single vocabulary words before tokenising.
_PHRASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:open|abiert[oa]s?)\s+(?:hi\s*-?\s*)?hat(?:s)?\b"), "ohat"),
    (re.compile(r"\b(?:hi\s*-?\s*)?hat(?:s)?\s+(?:open|abiert[oa]s?)\b"), "ohat"),
    (re.compile(r"\bcharles\s+abiert[oa]s?\b"), "ohat"),
    (re.compile(r"\b(?:closed|cerrad[oa]s?)\s+(?:hi\s*-?\s*)?hat(?:s)?\b"), "chat"),
    (re.compile(r"\b(?:hi\s*-?\s*)?hat(?:s)?\s+(?:closed|cerrad[oa]s?)\b"), "chat"),
    (re.compile(r"\bcharles\s+cerrad[oa]s?\b"), "chat"),
    (re.compile(r"\bhi\s*-?\s*hat(?:s)?\b"), "hat"),
    (re.compile(r"\bbass\s+drum\b"), "kick"),
    (re.compile(r"\bone\s*-?\s*shots?\b"), "oneshot"),
    (re.compile(r"\bside\s*-?\s*stick\b"), "sidestick"),
    (re.compile(r"\bcow\s*-?\s*bell\b"), "cowbell"),
    (re.compile(r"\btr\s*-?\s*(808|909|606|707|8)\b"), r"tr\1"),
    (re.compile(r"\bgolpe\s+de\s+bombo\b"), "bombo"),
    (re.compile(r"\b([a-g][#b]?)\s+(minor|major|min|maj)\b"), r"\1\2"),
]

_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "of", "in", "sample", "samples", "sound",
    "sounds", "sonido", "sonidos", "un", "una", "el", "la", "los", "las", "de", "con", "para",
    "y", "o", "que", "bpm", "key", "tipo", "type", "kit", "something", "algo",
}  # fmt: skip

_BPM_TOKEN = re.compile(r"^(\d{2,3})(?:\.\d)?(?:bpm)?$")
_KEY_TOKEN = re.compile(r"^([a-g][#b]?)(m|min|minor|maj|major)$|^[a-g]#$")

#: 'hat' = the whole hat family etc. is expressed in the vocabulary values.
_CYM_SUBTYPES = {"crash", "ride", "splash", "china", "gong"}


# ---------------------------------------------------------------------------
# query model
# ---------------------------------------------------------------------------


@dataclass
class Query:
    """A parsed free-text query."""

    types: list[str] = field(default_factory=list)  # OR group
    subtypes: list[str] = field(default_factory=list)  # OR group
    kinds: list[str] = field(default_factory=list)  # OR group
    tag_concepts: list[tuple[str, tuple[tuple[str, float], ...]]] = field(default_factory=list)
    free: list[str] = field(default_factory=list)  # must appear in name/kit/tags
    bpms: list[float] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    interpreted: list[str] = field(default_factory=list)


def parse_query(text: str) -> Query:
    """Free text -> :class:`Query` (English + Spanish, accents ignored)."""
    q = Query()
    s = _norm(text)
    for pat, repl in _PHRASES:
        s = pat.sub(repl, s)
    for raw in re.findall(r"[a-z0-9#.+_-]+", s):
        if raw in _STOPWORDS:
            continue
        concepts = _VOCAB.get(raw)
        if concepts is None and "-" in raw:
            concepts = _VOCAB.get(raw.replace("-", ""))
        if concepts:
            for aspect, values in concepts:
                if aspect == _TYPE:
                    for v, _w in values:
                        if v not in q.types:
                            q.types.append(v)
                    q.interpreted.append(f"type={'|'.join(v for v, _ in values)}")
                elif aspect == _SUB:
                    for v, _w in values:
                        if v not in q.subtypes:
                            q.subtypes.append(v)
                    q.interpreted.append(f"subtype={values[0][0]}")
                elif aspect == _KIND:
                    for v, _w in values:
                        if v not in q.kinds:
                            q.kinds.append(v)
                    q.interpreted.append(f"kind={values[0][0]}")
                else:
                    q.tag_concepts.append((aspect, values))
                    q.interpreted.append(f"prefer-tag={values[0][0]}")
            continue
        m = _BPM_TOKEN.match(raw)
        if m and 40 <= float(m.group(1)) <= 240:
            q.bpms.append(float(m.group(1)))
            q.interpreted.append(f"bpm~{m.group(1)}")
            continue
        if _KEY_TOKEN.match(raw):
            try:
                parse_key(raw)
                q.keys.append(raw)
                q.interpreted.append(f"key~{raw}")
                continue
            except KeyParseError:
                pass
        q.free.append(raw)
        q.interpreted.append(f"text~{raw}")
    return q


# ---------------------------------------------------------------------------
# key / bpm compatibility
# ---------------------------------------------------------------------------


def compatible_key(query: str, entry_key: str | None, *, exact: bool = False) -> float | None:
    """Key-compatibility weight, or None when incompatible/unparseable.

    same key 1.0; relative major/minor 0.9; dominant/subdominant (a fifth
    away, same mode) 0.7; parallel major/minor 0.45.
    """
    if entry_key is None:
        return None
    try:
        qpc, qmode = parse_key(query)
        epc, emode = parse_key(entry_key)
    except KeyParseError:
        return None
    if qpc == epc and qmode == emode:
        return 1.0
    if exact:
        return None
    diff = (epc - qpc) % 12
    if qmode != emode:
        if (qmode == "major" and diff == 9) or (qmode == "minor" and diff == 3):
            return 0.9
        if diff == 0:
            return 0.45
        return None
    if diff in (5, 7):
        return 0.7
    return None


def _bpm_fit(
    entry_bpm: float | None, target: float, tol: float, half_double: bool
) -> tuple[float, str] | None:
    """(fit 0..1, reason) when the entry's BPM matches ``target``, else None."""
    if entry_bpm is None:
        return None
    rel = bpm_relation(target, entry_bpm, tol)  # entry relative to target
    if rel == "same":
        dev = abs(entry_bpm - target) / target
        fit = 1.0 - 0.5 * min(1.0, dev / max(tol, 1e-6))
        why = f"bpm {entry_bpm:g} (target {target:g}"
        return fit, why + (", exact)" if dev < 0.002 else f", {entry_bpm - target:+.1f})")
    if half_double and rel == "half":
        return 0.55, f"bpm {entry_bpm:g} = half-time of {target:g}"
    if half_double and rel == "double":
        return 0.55, f"bpm {entry_bpm:g} = double-time of {target:g}"
    return None


# ---------------------------------------------------------------------------
# quality prior
# ---------------------------------------------------------------------------


def quality(e: LibraryIndexEntry) -> tuple[float, list[str]]:
    """Quality prior 0..1 (clean, unclipped, sane level, well recorded)."""
    q = 0.5
    notes: list[str] = []
    if e.clipping:
        q -= 0.3
        notes.append("clipped")
    else:
        q += 0.2
    peak = e.peak_dbfs
    if peak is not None:
        if peak <= -20:
            q -= 0.3
            notes.append(f"very quiet (peak {peak:.0f} dBFS)")
        elif peak <= -12:
            q -= 0.1
            notes.append(f"quiet (peak {peak:.0f} dBFS)")
        elif peak <= -0.3:
            q += 0.1
        # hotter than -0.3: neutral (normalised hits are fine)
    if (e.bit_depth or 0) >= 24:
        q += 0.05
    if e.sample_rate is not None:
        q += 0.05 if e.sample_rate >= 44100 else -0.05
    for f in e.flags:
        if f.startswith("type-mismatch"):
            q -= 0.25
            notes.append("name/audio type mismatch")
        elif f.startswith("bpm-disagreement"):
            q -= 0.1
            notes.append("bpm: name and audio disagree")
        elif f.startswith("type-inferred"):
            q -= 0.1
        elif f == "unusable-uri":
            q -= 0.4
            notes.append("name has characters library:// cannot express: rename the file")
        elif f == "very-short":
            q -= 0.2
        elif f == "low-confidence-key":
            q -= 0.03
    if not notes:
        notes.append("clean" if not e.clipping else "clipped")
    return max(0.0, min(1.0, q)), notes


# ---------------------------------------------------------------------------
# similarity
# ---------------------------------------------------------------------------

_FEATURE_WEIGHTS = np.array([1.0, 1.5, 0.7, 1.5, 1.0, 1.0, 1.0, 1.0, 1.0, 0.7, 0.7])


def _raw_vector(e: LibraryIndexEntry) -> np.ndarray | None:
    f = e.features
    if f is None or f.spectral_centroid_hz is None:
        return None
    nan = float("nan")
    return np.array(
        [
            math.log10(f.fundamental_hz) if f.fundamental_hz else nan,
            math.log10((f.decay_ms or 0.0) + 10.0) if f.decay_ms is not None else nan,
            math.log10((f.attack_ms or 0.0) + 1.0) if f.attack_ms is not None else nan,
            math.log10(max(f.spectral_centroid_hz, 20.0)),
            math.sqrt(f.sub or 0.0),
            math.sqrt(f.low or 0.0),
            math.sqrt(f.mid or 0.0),
            math.sqrt(f.high or 0.0),
            math.sqrt(f.air or 0.0),
            f.tonality if f.tonality is not None else nan,
            math.log10((e.duration_s or 0.0) + 0.05),
        ]
    )


def _vectors(loaded: LoadedIndex) -> tuple[np.ndarray, dict[str, int]]:
    """Standardised feature matrix (n x d, NaN = unknown) + path -> row."""
    cached = loaded.cache.get("vectors")
    if cached is not None:
        return cached
    entries = loaded.index.entries
    raw = np.full((len(entries), len(_FEATURE_WEIGHTS)), np.nan)
    for i, e in enumerate(entries):
        v = _raw_vector(e)
        if v is not None:
            raw[i] = v
    known = np.isfinite(raw)
    count = np.maximum(known.sum(axis=0), 1)
    mean = np.where(known, raw, 0.0).sum(axis=0) / count
    var = np.where(known, (raw - mean) ** 2, 0.0).sum(axis=0) / count
    std = np.where(var > 1e-12, np.sqrt(var), 1.0)
    z = (raw - mean) / std
    rows = {e.path: i for i, e in enumerate(entries)}
    loaded.cache["vectors"] = (z, rows)
    return z, rows


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    """Weighted RMS distance over the features both sides know."""
    ok = np.isfinite(a) & np.isfinite(b)
    if not ok.any():
        return float("inf")
    w = _FEATURE_WEIGHTS[ok]
    return float(math.sqrt(np.sum(w * (a[ok] - b[ok]) ** 2) / np.sum(w)))


# ---------------------------------------------------------------------------
# description
# ---------------------------------------------------------------------------

_DESCRIPTIVE = (
    "punchy", "boomy", "subby", "clicky", "short", "long", "bright", "dark", "noisy", "tonal",
    "tuned", "wide", "hot", "quiet", "lofi",
)  # fmt: skip


def describe(e: LibraryIndexEntry) -> str:
    """One plain-language line about the sound."""
    parts = [e.type or "sample" + (f"/{e.subtype}" if e.subtype else "")]
    if e.type and e.subtype:
        parts = [f"{e.type}/{e.subtype}"]
    if e.kind and e.kind != "one-shot":
        parts[0] += f" {e.kind}"
    if e.duration_s is not None:
        parts.append(f"{e.duration_s:.2f}s")
    if e.bpm:
        parts.append(f"{e.bpm:g} bpm")
    if e.key:
        parts.append(f"key {e.key}")
    f = e.features
    if f is not None:
        if f.fundamental_hz and f.note:
            parts.append(f"pitch {f.note} ({f.fundamental_hz:.0f} Hz)")
        if f.decay_ms is not None and e.kind == "one-shot":
            parts.append(f"decay {f.decay_ms:.0f} ms")
        if f.attack_ms is not None and e.kind == "one-shot":
            parts.append(f"attack {f.attack_ms:.1f} ms")
        if f.spectral_centroid_hz is not None:
            c = f.spectral_centroid_hz
            parts.append(f"centroid {c / 1000:.1f} kHz" if c >= 1000 else f"centroid {c:.0f} Hz")
    chars = [t for t in e.tags if t in _DESCRIPTIVE]
    if chars:
        parts.append(" ".join(chars))
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# the search
# ---------------------------------------------------------------------------


@dataclass
class SearchOutcome:
    results: list[LibraryHit]
    total: int
    searched_roots: list[str]
    unindexed_roots: list[str]
    interpreted: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _haystack(e: LibraryIndexEntry) -> str:
    return _norm(f"{e.path} {' '.join(e.tags)} {e.subtype or ''}")


def _type_ok(e: LibraryIndexEntry, types: list[str], subtypes: list[str]) -> str | None:
    """Reason string when the entry satisfies the type/subtype groups."""
    reason = []
    if types:
        if e.type in types:
            reason.append(f"type {e.type}")
        else:
            return None
    if subtypes:
        if e.subtype in subtypes or (e.type == "cym" and e.subtype in subtypes):
            reason.append(f"subtype {e.subtype}")
        else:
            return None
    return " + ".join(reason) if reason else ""


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _filter_types(type_param: str | None) -> tuple[list[str], list[str], list[str]]:
    """The ``type`` param -> (types, subtypes, kinds) via the vocabulary."""
    types: list[str] = []
    subs: list[str] = []
    kinds: list[str] = []
    for word in _split(type_param):
        w = _norm(word)
        concepts = _VOCAB.get(w) or _VOCAB.get(w.replace("-", ""))
        if not concepts:
            types.append(w)  # custom types from hand-written indexes
            continue
        for aspect, values in concepts:
            target = {_TYPE: types, _SUB: subs, _KIND: kinds}.get(aspect)
            if target is not None:
                target.extend(v for v, _ in values if v not in target)
    return types, subs, kinds


def search(
    project: Project,
    *,
    query: str = "",
    bpm: float | None = None,
    bpm_tolerance: float = 0.04,
    half_double: bool = True,
    key: str | None = None,
    key_exact: bool = False,
    type: str | None = None,  # noqa: A002 - tool param name
    subtype: str | None = None,
    kind: str | None = None,
    kit: str | None = None,
    tags: list[str] | None = None,
    duration_min: float | None = None,
    duration_max: float | None = None,
    similar: str | None = None,
    clean_only: bool = False,
    detail: str = "compact",
    limit: int = 10,
    offset: int = 0,
) -> SearchOutcome:
    """Search every configured library root (see the module docstring)."""
    pool: list[tuple[Path, str, LoadedIndex]] = []
    searched: list[str] = []
    unindexed: list[str] = []
    for root in project.library_dirs():
        label = root_label(project, root)
        loaded = loaded_index(root)
        if loaded is None:
            # The scaffold's empty local folder should not drown out a usable
            # global index. Nonempty unindexed roots still deserve a hint.
            if root.exists() and any(root.iterdir()):
                unindexed.append(label)
            continue
        searched.append(label)
        pool.append((root, label, loaded))

    notes: list[str] = []
    q = parse_query(query)
    p_types, p_subs, p_kinds = _filter_types(type)
    types = p_types or []
    kinds = p_kinds or []
    if kind:
        kinds = [kind]
    subtypes = [_norm(s) for s in _split(subtype)] + p_subs
    q_types, q_subs, q_kinds = q.types, q.subtypes, q.kinds
    interpreted = list(q.interpreted)
    want_bpms = ([bpm] if bpm else []) + q.bpms
    want_keys = ([key] if key else []) + q.keys

    # ---- 'similar to' anchor ----------------------------------------------
    anchor: tuple[LoadedIndex, LibraryIndexEntry] | None = None
    if similar:
        rel = similar[len(LIBRARY_SCHEME) :] if similar.startswith(LIBRARY_SCHEME) else similar
        rel = rel.replace("\\", "/").lstrip("/")
        for _root, _label, loaded in pool:
            e = loaded.by_path.get(rel)
            if e is None:
                cands = [x for p_, x in loaded.by_path.items() if p_.endswith("/" + rel)]
                e = cands[0] if len(cands) == 1 else None
            if e is not None:
                anchor = (loaded, e)
                break
        if anchor is None:
            notes.append(f"similar: {similar!r} is not in the index (use a library:// URI).")
        else:
            interpreted.append(f"similar-to={anchor[1].path}")
            if not (types or q_types) and anchor[1].type:
                types = [anchor[1].type]
                interpreted.append(f"type={anchor[1].type} (same as the anchor)")

    kit_n = _norm(kit) if kit else None
    require_tags = [_norm(t) for t in (tags or [])]

    def run(strict_free: bool, use_bpm: bool = True) -> list[tuple[float, LibraryHit]]:
        out: list[tuple[float, LibraryHit]] = []
        for _root, label, loaded in pool:
            entries = loaded.index.entries
            if anchor is not None:
                z, rows = _vectors(loaded) if anchor[0] is loaded else (None, None)
            for ent in entries:
                reasons: list[str] = []
                # -- hard filters ------------------------------------------
                if types and ent.type not in types:
                    continue
                if kinds and ent.kind not in kinds:
                    continue
                if subtypes and ent.subtype not in subtypes:
                    continue
                if kit_n and not any(kit_n in _norm(seg) for seg in ent.path.split("/")[:-1]):
                    continue
                if require_tags and any(t not in ent.tags for t in require_tags):
                    continue
                if duration_min is not None and (ent.duration_s or 0.0) < duration_min:
                    continue
                if duration_max is not None and (ent.duration_s or 0.0) > duration_max:
                    continue
                if clean_only and (
                    ent.clipping
                    or any(
                        f.startswith(("type-mismatch", "low-level", "bpm-disagreement"))
                        for f in ent.flags
                    )
                ):
                    continue
                # -- free-text type groups (OR within, AND across) --------------
                grp = _type_ok(ent, q_types, q_subs)
                if grp is None:
                    continue
                if grp:
                    reasons.append(grp)
                if q_kinds:
                    if ent.kind not in q_kinds:
                        continue
                    reasons.append(ent.kind or "")
                # -- bpm / key ----------------------------------------------
                fits: list[float] = []
                if want_bpms and use_bpm:
                    best = None
                    for target in want_bpms:
                        r = _bpm_fit(ent.bpm, target, bpm_tolerance, half_double)
                        if r is not None and (best is None or r[0] > best[0]):
                            best = r
                    if best is None:
                        continue
                    fits.append(best[0])
                    reasons.append(best[1])
                if want_keys:
                    best_w = None
                    for target in want_keys:
                        w = compatible_key(target, ent.key, exact=key_exact)
                        if w is not None and (best_w is None or w > best_w):
                            best_w = w
                    if best_w is None:
                        continue
                    fits.append(best_w)
                    rel_word = "=" if best_w == 1.0 else "compatible with"
                    reasons.append(f"key {ent.key} {rel_word} {want_keys[0]}")
                # -- free words + soft tags ---------------------------------
                hay = None
                miss_free = False
                for word in q.free:
                    hay = hay or _haystack(ent)
                    if word in hay:
                        reasons.append(f"name/tag has '{word}'")
                    elif strict_free:
                        miss_free = True
                        break
                if miss_free:
                    continue
                soft_total = len(q.tag_concepts)
                soft_hit = 0.0
                for _aspect, values in q.tag_concepts:
                    got = max((w for v, w in values if v in ent.tags), default=0.0)
                    if got > 0:
                        soft_hit += got
                        shown = next(v for v, w in values if v in ent.tags and w == got)
                        reasons.append(f"tag {shown}" + ("" if got == 1.0 else " (similar)"))
                text = (soft_hit / soft_total) if soft_total else 0.5
                # -- similarity ---------------------------------------------
                sim = None
                if anchor is not None:
                    if ent.path == anchor[1].path and loaded is anchor[0]:
                        continue
                    if z is not None and rows is not None:
                        d = _distance(z[rows[ent.path]], z[rows[anchor[1].path]])
                        sim = math.exp(-d) if math.isfinite(d) else 0.0
                        reasons.insert(0, f"sound distance {d:.2f} to the reference (0 = same)")
                    else:
                        sim = 0.0
                # -- score -----------------------------------------------------
                qual, qnotes = quality(ent)
                fit = sum(fits) / len(fits) if fits else 0.5
                if sim is not None:
                    score = 0.6 * sim + 0.2 * text + 0.2 * qual
                else:
                    score = 0.4 * text + 0.3 * qual + 0.3 * fit if fits else 0.5 * text + 0.5 * qual
                reasons.extend(qnotes[:2])
                hit = hit_from_entry(ent, label, kits=loaded.index.kits, detail=(detail == "full"))
                hit.score = round(score, 3)
                hit.reasons = [r for r in reasons if r]
                hit.summary = describe(ent)
                out.append((score, hit))
        return out

    scored = run(strict_free=True)
    if not scored and q.free:
        scored = run(strict_free=False)
        if scored:
            notes.append(
                "No sample matched all of: "
                + ", ".join(f"'{w}'" for w in q.free)
                + "; showing results for the recognised terms only."
            )
    if not scored and want_bpms:
        tempos = sorted({h.bpm for _s, h in run(strict_free=True, use_bpm=False) if h.bpm})
        if tempos:
            near = sorted(tempos, key=lambda b: min(abs(b - t) for t in want_bpms))[:6]
            notes.append(
                f"Nothing within {bpm_tolerance:.0%} of {want_bpms[0]:g} bpm (or its half/double "
                "time); tempos available for this selection: "
                + ", ".join(f"{b:g}" for b in sorted(near))
                + "."
            )
    scored.sort(key=lambda t: (-t[0], t[1].path))
    total = len(scored)
    page = [h for _s, h in _diversify(scored, offset + limit)[offset : offset + limit]]
    for h in page:
        _compact(h, detail)
    return SearchOutcome(
        results=page,
        total=total,
        searched_roots=searched,
        unindexed_roots=unindexed,
        interpreted=interpreted,
        notes=notes,
    )


def _diversify(
    scored: list[tuple[float, LibraryHit]], want: int, penalty: float = 0.03
) -> list[tuple[float, LibraryHit]]:
    """Greedy re-rank for variety: each already-picked result from the same
    folder lowers the next one's effective score by ``penalty``, so a vague
    query ("clap") shows several sources instead of one pack's first files."""
    if len(scored) <= 1:
        return scored
    taken: dict[str, int] = {}
    pool = list(scored)
    out: list[tuple[float, LibraryHit]] = []
    while pool and len(out) < want:
        best_i = max(
            range(len(pool)),
            key=lambda i: (
                pool[i][0] - penalty * taken.get(_group(pool[i][1]), 0),
                -i,  # stable: the earlier (better, then alphabetical) wins ties
            ),
        )
        item = pool.pop(best_i)
        out.append(item)
        g = _group(item[1])
        taken[g] = taken.get(g, 0) + 1
    return out


def _group(h: LibraryHit) -> str:
    return "/".join(h.path.split("/")[:3])


#: tags that merely repeat other fields; hidden from compact payloads.
_REDUNDANT_TAGS = {"one-shot", "loop", "phrase", "clean"}


def _compact(h: LibraryHit, detail: str) -> None:
    """Trim a hit for LLM context (full detail keeps everything)."""
    if detail == "full":
        return
    drop = _REDUNDANT_TAGS | {h.type or "", h.subtype or "", h.kit or ""}
    h.tags = [t for t in h.tags if t not in drop and not re.fullmatch(r"[A-G][#b]?m?", t)][:10]
    h.reasons = h.reasons[:5]
    lic = h.license
    if lic is not None:
        for r in lic.restrictions:
            if r in ("no-ai-training", "non-commercial"):
                h.flags.append(f"licence:{r}")
        h.license = None
