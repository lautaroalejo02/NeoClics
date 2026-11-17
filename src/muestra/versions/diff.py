"""Structured diff between two songs (WP5).

:func:`song_diff` compares two parsed :class:`~muestra.schemas.song.Song`
objects (never raw text, so formatting, comments and omitted defaults can't
produce phantom changes) and returns :class:`DiffEntry` records with
ID-based paths per the contract grammar (docs/contracts.md, R10)::

    meta.bpm                                   chops.c1.warp.pitch_semitones
    tracks.sample.fx.lp.cutoff_hz              tracks.drums.pattern.lanes.kick.steps
    tracks.sample.clips[2].at_bar              tracks.bass.notes[0].pitch
    tracks.bass.fx[1].ratio   (fx without id)  master.fx.limiter.ceiling_db
    automation.tracks.sample.fx.lp.cutoff_hz.points

Matching, not positions:

* sources/chops/tracks/lanes/id'd fx are matched by id, so inserting,
  deleting or reordering them never shifts anybody's address;
* clips, inline notes and id-less fx have no id, so they are *aligned*:
  identical items first, then the most similar leftovers (a moved clip or a
  transposed note is one modification, not a removal plus an addition);
* an added/removed element is a single entry (``from`` or ``to`` is None and
  the other side is the whole element); a modified element yields one entry
  per changed field;
* indices (``clips[i]``, ``notes[i]``, ``fx[i]``) are 0-based and refer to the
  *new* version (``b``), except for removed elements, which use their index in
  the *old* version (``a``);
* automations are keyed by their ``target``: ``automation.<target>`` for an
  added/removed automation, ``automation.<target>.curve|points`` for edits
  (the trailing ``curve``/``points`` is always the field);
* reordering is only reported where order is musical: the relative order of
  fx in a chain (``tracks.<id>.fx`` / ``master.fx``) and of tracks
  (``tracks``, the mixer order). Reordering clips, notes, chops, lanes or
  automations changes nothing audible, so it is not a change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..schemas.render_meta import DiffEntry

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..schemas.song import Song

__all__ = [
    "Alignment",
    "FxAlignment",
    "align_items",
    "clip_score",
    "fx_alignment",
    "note_score",
    "same",
    "song_diff",
]

_EPS = 1e-9
#: leftover pairs above this many comparisons skip fuzzy matching (huge
#: note lists): they are reported as removed + added.
_FUZZY_LIMIT = 90_000


# ---------------------------------------------------------------------------
# value helpers
# ---------------------------------------------------------------------------


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def same(a: Any, b: Any) -> bool:
    """Deep equality with a float tolerance (1e-9)."""
    if _num(a) and _num(b):
        return abs(float(a) - float(b)) < _EPS
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def _round(x: Any) -> Any:
    if isinstance(x, float):
        return round(x, 9) + 0.0
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_round(v) for v in x]
    return x


def _canon(x: Any) -> str:
    return json.dumps(_round(x), sort_keys=True, separators=(",", ":"), default=str)


def _entry(path: str, from_: Any, to: Any) -> DiffEntry:
    return DiffEntry(path=path, **{"from": from_}, to=to)


# ---------------------------------------------------------------------------
# alignment of id-less lists
# ---------------------------------------------------------------------------


@dataclass
class Alignment:
    """Result of aligning two lists: ``pairs`` are ``(index_in_a, index_in_b)``
    sorted by b's index; ``removed`` are a-indices, ``added`` b-indices."""

    pairs: list[tuple[int, int]] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    added: list[int] = field(default_factory=list)

    def modified(self, a: list[dict], b: list[dict]) -> list[tuple[int, int]]:
        """Pairs whose items are not identical."""
        return [(ia, ib) for ia, ib in self.pairs if not same(a[ia], b[ib])]

    @property
    def reordered(self) -> bool:
        """True when matched items appear in a different relative order."""
        seq = [ia for ia, _ in self.pairs]
        return seq != sorted(seq)


def align_items(
    a: list[Any],
    b: list[Any],
    score: Callable[[Any, Any], int | None] | None = None,
) -> Alignment:
    """Align two id-less lists.

    1. Identical items are paired (multiset semantics, earliest first).
    2. Leftovers are paired greedily by ``score(x, y)`` (higher = more
       alike; ``None`` = not allowed to pair), ties broken by index
       distance. Without a ``score`` leftovers are never paired.
    """
    pairs: list[tuple[int, int]] = []
    by_key: dict[str, list[int]] = {}
    for ia, item in enumerate(a):
        by_key.setdefault(_canon(item), []).append(ia)
    free_b: list[int] = []
    for ib, item in enumerate(b):
        bucket = by_key.get(_canon(item))
        if bucket:
            pairs.append((bucket.pop(0), ib))
        else:
            free_b.append(ib)
    free_a = sorted(ia for bucket in by_key.values() for ia in bucket)

    if score is not None and free_a and free_b and len(free_a) * len(free_b) <= _FUZZY_LIMIT:
        cands: list[tuple[int, int, int, int]] = []
        for ia in free_a:
            for ib in free_b:
                s = score(a[ia], b[ib])
                if s is not None:
                    cands.append((-s, abs(ia - ib), ia, ib))
        cands.sort()
        used_a: set[int] = set()
        used_b: set[int] = set()
        for _, _, ia, ib in cands:
            if ia in used_a or ib in used_b:
                continue
            used_a.add(ia)
            used_b.add(ib)
            pairs.append((ia, ib))
        free_a = [i for i in free_a if i not in used_a]
        free_b = [i for i in free_b if i not in used_b]

    pairs.sort(key=lambda p: p[1])
    return Alignment(pairs=pairs, removed=free_a, added=free_b)


def _equal_fields(x: dict, y: dict) -> int:
    return sum(1 for k in set(x) | set(y) if same(x.get(k), y.get(k)))


def clip_score(x: dict, y: dict) -> int | None:
    """Clips pair up when they play the same material or sit at the same spot."""
    same_source = x.get("chop") == y.get("chop") and x.get("sample") == y.get("sample")
    same_spot = x.get("at_bar") == y.get("at_bar") and same(
        x.get("offset_beats"), y.get("offset_beats")
    )
    if not (same_source or same_spot):
        return None
    return _equal_fields(x, y)


def note_score(x: dict, y: dict) -> int | None:
    """Notes pair up when they start together (pitch/length/velocity edit) or
    keep pitch and length (moved in time)."""
    same_beat = same(x.get("beat"), y.get("beat"))
    moved = x.get("pitch") == y.get("pitch") and same(x.get("length_beats"), y.get("length_beats"))
    if not (same_beat or moved):
        return None
    return _equal_fields(x, y)


def _fx_score(x: dict, y: dict) -> int | None:
    if x.get("type") != y.get("type"):
        return None
    return _equal_fields(x, y)


@dataclass
class FxAlignment:
    """Alignment of one fx chain. Index lists refer to positions in the chain;
    ``label_*`` give each fx's address segment (``<id>`` or ``fx[<i>]``)."""

    pairs: list[tuple[int, int]]
    removed: list[int]
    added: list[int]
    reordered: bool

    @staticmethod
    def label(fx: dict, index: int) -> str:
        return fx["id"] if fx.get("id") else f"fx[{index}]"


def fx_alignment(a_fx: list[dict], b_fx: list[dict]) -> FxAlignment:
    """Match fx by id; fx without an id are aligned by type within the chain."""
    a_ids = {f["id"]: i for i, f in enumerate(a_fx) if f.get("id")}
    b_ids = {f["id"]: i for i, f in enumerate(b_fx) if f.get("id")}
    pairs = [(a_ids[k], b_ids[k]) for k in b_ids if k in a_ids]
    removed = [i for k, i in a_ids.items() if k not in b_ids]
    added = [i for k, i in b_ids.items() if k not in a_ids]
    a_anon = [i for i, f in enumerate(a_fx) if not f.get("id")]
    b_anon = [i for i, f in enumerate(b_fx) if not f.get("id")]
    al = align_items([a_fx[i] for i in a_anon], [b_fx[i] for i in b_anon], _fx_score)
    pairs += [(a_anon[ia], b_anon[ib]) for ia, ib in al.pairs]
    removed += [a_anon[i] for i in al.removed]
    added += [b_anon[i] for i in al.added]
    pairs.sort(key=lambda p: p[1])
    seq = [ia for ia, _ in pairs]
    return FxAlignment(pairs, sorted(removed), sorted(added), seq != sorted(seq))


# ---------------------------------------------------------------------------
# structured diff
# ---------------------------------------------------------------------------


def _walk(path: str, a: Any, b: Any, out: list[DiffEntry]) -> None:
    """Field-level entries for two values; dicts recurse, everything else is
    compared whole."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            _walk(f"{path}.{k}", a.get(k), b.get(k), out)
    elif not same(a, b):
        out.append(_entry(path, a, b))


def _diff_keyed(
    path: str,
    a_items: list[dict],
    b_items: list[dict],
    out: list[DiffEntry],
    key: str = "id",
    skip: tuple[str, ...] = (),
) -> None:
    """Diff lists of dicts matched by ``key`` (sources, chops)."""
    a_by = {x[key]: x for x in a_items}
    b_by = {x[key]: x for x in b_items}
    for k in [*b_by, *(k for k in a_by if k not in b_by)]:
        xa, xb = a_by.get(k), b_by.get(k)
        p = f"{path}.{k}"
        if xa is None:
            out.append(_entry(p, None, xb))
        elif xb is None:
            out.append(_entry(p, xa, None))
        else:
            for fld in sorted((set(xa) | set(xb)) - {key, *skip}):
                _walk(f"{p}.{fld}", xa.get(fld), xb.get(fld), out)


def _diff_fx_chain(path: str, a_fx: list[dict], b_fx: list[dict], out: list[DiffEntry]) -> None:
    al = fx_alignment(a_fx, b_fx)
    for ia, ib in al.pairs:
        fa, fb = a_fx[ia], b_fx[ib]
        if same(fa, fb):
            continue
        p = f"{path}.{fb['id']}" if fb.get("id") else f"{path}[{ib}]"
        for fld in sorted((set(fa) | set(fb)) - {"id"}):
            _walk(f"{p}.{fld}", fa.get(fld), fb.get(fld), out)
    for ia in al.removed:
        fa = a_fx[ia]
        out.append(_entry(f"{path}.{fa['id']}" if fa.get("id") else f"{path}[{ia}]", fa, None))
    for ib in al.added:
        fb = b_fx[ib]
        out.append(_entry(f"{path}.{fb['id']}" if fb.get("id") else f"{path}[{ib}]", None, fb))
    if al.reordered:
        out.append(
            _entry(
                path,
                [FxAlignment.label(a_fx[i], i) for i in range(len(a_fx))],
                [FxAlignment.label(b_fx[i], i) for i in range(len(b_fx))],
            )
        )


def _diff_aligned_list(
    path: str,
    a_items: list[dict],
    b_items: list[dict],
    score: Callable[[Any, Any], int | None],
    out: list[DiffEntry],
) -> None:
    al = align_items(a_items, b_items, score)
    for ia, ib in al.modified(a_items, b_items):
        _walk(f"{path}[{ib}]", a_items[ia], b_items[ib], out)
    for ia in al.removed:
        out.append(_entry(f"{path}[{ia}]", a_items[ia], None))
    for ib in al.added:
        out.append(_entry(f"{path}[{ib}]", None, b_items[ib]))


def _diff_pattern(path: str, a: dict, b: dict, out: list[DiffEntry]) -> None:
    for k in sorted((set(a) | set(b)) - {"lanes"}):
        _walk(f"{path}.{k}", a.get(k), b.get(k), out)
    a_lanes, b_lanes = a.get("lanes") or {}, b.get("lanes") or {}
    for lane in [*b_lanes, *(k for k in a_lanes if k not in b_lanes)]:
        la, lb = a_lanes.get(lane), b_lanes.get(lane)
        p = f"{path}.lanes.{lane}"
        if la is None:
            out.append(_entry(p, None, lb))
        elif lb is None:
            out.append(_entry(p, la, None))
        else:
            _walk(p, la, lb, out)


def _diff_track(tid: str, a: dict, b: dict, out: list[DiffEntry]) -> None:
    p = f"tracks.{tid}"
    for k in ("at_bar", "until_bar", "gain_db", "pan", "mute"):
        _walk(f"{p}.{k}", a.get(k), b.get(k), out)

    ca, cb = a.get("clips"), b.get("clips")
    if ca is not None and cb is not None:
        _diff_aligned_list(f"{p}.clips", ca, cb, clip_score, out)
    elif not same(ca, cb):
        out.append(_entry(f"{p}.clips", ca, cb))

    pa, pb = a.get("pattern"), b.get("pattern")
    if pa is not None and pb is not None:
        _diff_pattern(f"{p}.pattern", pa, pb, out)
    elif not same(pa, pb):
        out.append(_entry(f"{p}.pattern", pa, pb))

    ia, ib = a.get("instrument"), b.get("instrument")
    if ia is not None and ib is not None:
        _walk(f"{p}.instrument", ia, ib, out)
    elif not same(ia, ib):
        out.append(_entry(f"{p}.instrument", ia, ib))

    _walk(f"{p}.midi", a.get("midi"), b.get("midi"), out)

    na, nb = a.get("notes"), b.get("notes")
    if na is not None and nb is not None:
        _diff_aligned_list(f"{p}.notes", na, nb, note_score, out)
    elif not same(na, nb):
        out.append(_entry(f"{p}.notes", na, nb))

    _diff_fx_chain(f"{p}.fx", a.get("fx") or [], b.get("fx") or [], out)


def _diff_automation(a_list: list[dict], b_list: list[dict], out: list[DiffEntry]) -> None:
    a_by = {x["target"]: x for x in a_list}
    b_by = {x["target"]: x for x in b_list}
    for target in [*b_by, *(t for t in a_by if t not in b_by)]:
        xa, xb = a_by.get(target), b_by.get(target)
        p = f"automation.{target}"
        if xa is None:
            out.append(_entry(p, None, xb))
        elif xb is None:
            out.append(_entry(p, xa, None))
        else:
            for fld in ("curve", "points"):
                _walk(f"{p}.{fld}", xa.get(fld), xb.get(fld), out)


def song_diff(a: Song, b: Song) -> list[DiffEntry]:
    """Structured diff of two songs with ID-based paths (see module docs)."""
    da = a.model_dump(mode="json")
    db = b.model_dump(mode="json")
    out: list[DiffEntry] = []
    _walk("schema_version", da.get("schema_version"), db.get("schema_version"), out)
    _walk("meta", da["meta"], db["meta"], out)
    _diff_keyed("sources", da.get("sources", []), db.get("sources", []), out)
    _diff_keyed("chops", da.get("chops", []), db.get("chops", []), out)

    a_tracks = {t["id"]: t for t in da.get("tracks", [])}
    b_tracks = {t["id"]: t for t in db.get("tracks", [])}
    common_a = [t for t in a_tracks if t in b_tracks]
    common_b = [t for t in b_tracks if t in a_tracks]
    if common_a != common_b:
        out.append(_entry("tracks", list(a_tracks), list(b_tracks)))
    for tid in [*b_tracks, *(t for t in a_tracks if t not in b_tracks)]:
        ta, tb = a_tracks.get(tid), b_tracks.get(tid)
        if ta is None:
            out.append(_entry(f"tracks.{tid}", None, tb))
        elif tb is None:
            out.append(_entry(f"tracks.{tid}", ta, None))
        else:
            _diff_track(tid, ta, tb, out)

    _diff_automation(da.get("automation", []), db.get("automation", []), out)

    ma, mb = da.get("master") or {}, db.get("master") or {}
    for k in sorted((set(ma) | set(mb)) - {"fx"}):
        _walk(f"master.{k}", ma.get(k), mb.get(k), out)
    _diff_fx_chain("master.fx", ma.get("fx") or [], mb.get("fx") or [], out)
    return out
