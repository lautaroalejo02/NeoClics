"""Structured edit operations on song.yaml (WP16).

Primitive ops (what is applied, journaled and inverted)::

    {op: set,    path, value, expect?}     set a field / list element (creates mapping keys)
    {op: unset,  path, expect?}            delete a mapping key (back to its default)
    {op: insert, path, index?, value}      insert into a list (default: append)
    {op: remove, path, expect?}            remove a list element (or a mapping entry)
    {op: move,   path, to}                 reorder a list element (tracks, fx)
    {op: document, value}                  replace the whole song (revert)

Semantic ops (compiled to primitives against the current song, so their
inverse is primitive too)::

    {op: hit, track, lane, bar, step, velocity|null}   grid hit at song bar + step (null = remove)
    {op: add_hits, track, hits: [{beat, lane, velocity?, sample?}], quantize?}
    {op: split_clip, track, clip, at_beat}             split at a 1-indexed song beat
    {op: duplicate_clip, track, clip, at_bar?, offset_beats?}
    {op: add_point, target, bar, value, curve?}        automation point (creates the lane)
    {op: revert_to, version}                           the song of a version (discard edits)

``expect`` (optional) is the value the client believes is there; a mismatch
is an ``edit_conflict`` (another writer changed that element), never a
silent overwrite.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import MutableMapping, MutableSequence
from typing import Annotated, Any, Literal

from pydantic import Field

from ..errors import MuestraError
from ..schemas._base import ContractModel, FileRef, IdStr, LaneName, VersionStr
from ..schemas.song import BEATS_PER_BAR, Song, lane_bar_steps, parse_steps
from .paths import get_value, resolve
from .yamlio import to_node, to_plain

__all__ = ["EditOp", "PrimitiveOp", "apply_ops", "same_value"]


class _Op(ContractModel):
    pass


class SetOp(_Op):
    op: Literal["set"] = "set"
    path: str = Field(min_length=1)
    value: Any = None
    expect: Any = None


class UnsetOp(_Op):
    op: Literal["unset"] = "unset"
    path: str = Field(min_length=1)
    expect: Any = None


class InsertOp(_Op):
    op: Literal["insert"] = "insert"
    path: str = Field(min_length=1, description="Path of the LIST (e.g. tracks.sample.clips).")
    index: int | None = Field(default=None, ge=0, description="Default: append.")
    value: Any = None


class RemoveOp(_Op):
    op: Literal["remove"] = "remove"
    path: str = Field(
        min_length=1, description="Path of the element (e.g. tracks.sample.clips[2])."
    )
    expect: Any = None


class MoveOp(_Op):
    op: Literal["move"] = "move"
    path: str = Field(min_length=1)
    to: int = Field(ge=0)


class DocumentOp(_Op):
    op: Literal["document"] = "document"
    value: dict[str, Any]


class HitOp(_Op):
    op: Literal["hit"] = "hit"
    track: IdStr
    lane: LaneName
    bar: int = Field(ge=1, description="1-indexed SONG bar.")
    step: int = Field(ge=0, description="0-based step inside the bar.")
    velocity: float | None = Field(default=1.0, ge=0.0, le=1.0, description="null/0 removes it.")


class FreeHit(ContractModel):
    beat: float = Field(ge=1.0)
    lane: LaneName
    velocity: float = Field(default=1.0, gt=0.0, le=1.0)
    sample: FileRef | None = None


class AddHitsOp(_Op):
    op: Literal["add_hits"] = "add_hits"
    track: IdStr
    hits: list[FreeHit] = Field(min_length=1)
    quantize: Literal["off", "step", "beat"] = Field(
        default="off", description="Snap beats to the pattern's step grid or to beats."
    )


class SplitClipOp(_Op):
    op: Literal["split_clip"] = "split_clip"
    track: IdStr
    clip: int = Field(ge=0)
    at_beat: float = Field(ge=1.0, description="1-indexed song beat strictly inside the clip.")


class DuplicateClipOp(_Op):
    op: Literal["duplicate_clip"] = "duplicate_clip"
    track: IdStr
    clip: int = Field(ge=0)
    at_bar: int | None = Field(default=None, ge=1)
    offset_beats: float | None = None


class AddPointOp(_Op):
    op: Literal["add_point"] = "add_point"
    target: str = Field(min_length=1)
    bar: float = Field(ge=1.0)
    value: float
    curve: Literal["lin", "exp", "step"] = "lin"


class RevertToOp(_Op):
    op: Literal["revert_to"] = "revert_to"
    version: VersionStr


PrimitiveOp = Annotated[
    SetOp | UnsetOp | InsertOp | RemoveOp | MoveOp | DocumentOp, Field(discriminator="op")
]
EditOp = Annotated[
    SetOp
    | UnsetOp
    | InsertOp
    | RemoveOp
    | MoveOp
    | DocumentOp
    | HitOp
    | AddHitsOp
    | SplitClipOp
    | DuplicateClipOp
    | AddPointOp
    | RevertToOp,
    Field(discriminator="op"),
]


def _canon(v: Any) -> str:
    def norm(x):
        if isinstance(x, float):
            if x.is_integer():
                return int(x)
            return round(x, 9)
        if isinstance(x, dict):
            return {str(k): norm(y) for k, y in x.items()}
        if isinstance(x, (list, tuple)):
            return [norm(y) for y in x]
        return x

    return json.dumps(norm(v), sort_keys=True)


def same_value(a: Any, b: Any) -> bool:
    return _canon(to_plain(a)) == _canon(to_plain(b))


def _conflict(path: str, expected: Any, actual: Any) -> MuestraError:
    return MuestraError.one(
        "edit_conflict",
        f"La canción cambió mientras editabas: «{path}» ya no es lo que el editor tenía "
        "(lo cambió otra persona o el agente). Recargué; repetí la edición.",
        path=path,
        value={"expected": to_plain(expected), "actual": to_plain(actual)},
        hint="Reload the song (song.state) and rebuild the op from the current values.",
    )


def _check_expect(op, current: Any, exists: bool, path: str) -> None:
    if "expect" not in op.model_fields_set:
        return
    actual = current if exists else None
    if not same_value(actual, op.expect):
        raise _conflict(path, op.expect, actual)


def _list_at(doc, path: str, create: bool) -> MutableSequence:
    container, key = resolve(doc, path, create=create)
    if isinstance(container, MutableMapping) and (key not in container or container[key] is None):
        if not create:
            raise MuestraError.one(
                "invalid_path",
                f"«{path}» no es una lista de la canción.",
                path=path,
                hint="Insert into a list path like tracks.sample.clips.",
            )
        from ruamel.yaml.comments import CommentedSeq

        container[key] = CommentedSeq()
    node = container[key]
    if not isinstance(node, MutableSequence):
        raise MuestraError.one(
            "invalid_path",
            f"«{path}» no es una lista.",
            path=path,
            hint="insert/remove/move work on lists (clips, notes, hits, fx, tracks, points).",
        )
    return node


def _like(seq: MutableSequence) -> Any:
    return seq[-1] if len(seq) else None


def _apply_primitive(doc, op) -> list:
    """Apply one primitive op in place on a ruamel doc; return its inverse ops."""
    if op.op == "set":
        container, key = resolve(doc, op.path, create=True)
        if isinstance(container, MutableSequence):
            if not 0 <= key < len(container):
                raise MuestraError.one(
                    "invalid_path", f"«{op.path}»: no hay elemento {key}.", path=op.path
                )
            exists, old = True, container[key]
        else:
            exists, old = key in container, container.get(key)
        _check_expect(op, old, exists, op.path)
        old_plain = to_plain(old) if exists else None
        container[key] = to_node(
            op.value,
            old if exists else None,
            key if not isinstance(container, MutableSequence) else _leaf(op.path),
        )
        if exists:
            return [SetOp(path=op.path, value=old_plain, expect=to_plain(op.value))]
        return [UnsetOp(path=op.path, expect=to_plain(op.value))]
    if op.op == "unset":
        container, key = resolve(doc, op.path)
        if isinstance(container, MutableSequence):
            raise MuestraError.one(
                "invalid_path", f"«{op.path}» es un elemento de lista: usá remove.", path=op.path
            )
        exists = key in container
        _check_expect(op, container.get(key), exists, op.path)
        if not exists:
            return []
        old = to_plain(container[key])
        del container[key]
        return [SetOp(path=op.path, value=old, expect=None)]
    if op.op == "insert":
        seq = _list_at(doc, op.path, create=True)
        index = len(seq) if op.index is None else min(op.index, len(seq))
        seq.insert(index, to_node(op.value, _like(seq), _leaf(op.path)))
        return [RemoveOp(path=f"{op.path}[{index}]", expect=to_plain(op.value))]
    if op.op == "remove":
        container, key = resolve(doc, op.path)
        if isinstance(container, MutableSequence):
            if not 0 <= key < len(container):
                raise MuestraError.one(
                    "invalid_path", f"«{op.path}»: no hay elemento {key}.", path=op.path
                )
            _check_expect(op, container[key], True, op.path)
            old = to_plain(container[key])
            del container[key]
            parent = _list_path_of(doc, op.path, container)
            if not container:
                _drop_comments(doc, parent, container)
            return [InsertOp(path=parent, index=key, value=old)]
        exists = key in container
        _check_expect(op, container.get(key), exists, op.path)
        if not exists:
            raise MuestraError.one("invalid_path", f"«{op.path}» no existe.", path=op.path)
        old = to_plain(container[key])
        del container[key]
        return [SetOp(path=op.path, value=old)]
    if op.op == "move":
        container, key = resolve(doc, op.path)
        if not isinstance(container, MutableSequence):
            raise MuestraError.one(
                "invalid_path", f"«{op.path}» no es un elemento de lista.", path=op.path
            )
        to = min(op.to, len(container) - 1)
        item = container.pop(key)
        container.insert(to, item)
        parent = _list_path_of(doc, op.path, container)
        return [MoveOp(path=f"{parent}[{to}]", to=key)]
    if op.op == "document":
        old = to_plain(doc)
        for k in list(doc.keys()):
            del doc[k]
        for k, v in op.value.items():
            doc[k] = to_node(v, None, k)
        return [DocumentOp(value=old)]
    raise AssertionError(op.op)


def _drop_comments(doc, list_path: str, seq) -> None:
    """ruamel emits broken YAML for an EMPTY block list that still carries the
    comments of its former items: drop them (undo restores the exact bytes)."""
    ca = getattr(seq, "ca", None)
    if ca is not None:
        ca.comment = None
        ca.items.clear()
    try:
        pmap, pkey = resolve(doc, list_path)
    except MuestraError:
        return
    pca = getattr(pmap, "ca", None)
    if pca is not None:
        pca.items.pop(pkey, None)


def _leaf(path: str) -> str:
    return path.rsplit(".", 1)[-1].split("[", 1)[0]


def _list_path_of(doc, element_path: str, container) -> str:
    """Path of the list holding ``element_path`` (``a.b[3]`` -> ``a.b``; an id step
    ``tracks.drums`` -> ``tracks``)."""
    if element_path.endswith("]"):
        return element_path[: element_path.rindex("[")]
    return element_path.rsplit(".", 1)[0]


# ---------------------------------------------------------------------------
# semantic ops -> primitives
# ---------------------------------------------------------------------------


def _vel_char(v: float | None) -> str:
    if v is None or v <= 0:
        return "."
    if abs(v - 1) < 1e-9:
        return "x"
    if abs(v - 0.6) < 1e-9:
        return "o"
    return str(min(9, max(1, round(v * 9))))


def _track(song: Song, tid: str):
    t = next((t for t in song.tracks if t.id == tid), None)
    if t is None:
        raise MuestraError.one(
            "ref_not_found",
            f"No hay un track «{tid}».",
            path=f"tracks.{tid}",
            hint="Use an existing track id (song.state).",
        )
    return t


def _pattern_track(song: Song, tid: str):
    t = _track(song, tid)
    if t.pattern is None:
        raise MuestraError.one(
            "track_kind",
            f"El track «{tid}» no es de batería (no tiene patrón).",
            path=f"tracks.{tid}.pattern",
            hint="Drum hits go on pattern tracks.",
        )
    return t


def _compile(op, doc) -> list:
    """Semantic op -> primitive ops against the current document."""
    if op.op in ("set", "unset", "insert", "remove", "move", "document"):
        return [op]
    song = Song.model_validate(to_plain(doc))
    if op.op == "hit":
        track = _pattern_track(song, op.track)
        pattern = track.pattern
        lane = pattern.lanes.get(op.lane)
        if lane is None:
            raise MuestraError.one(
                "ref_not_found",
                f"La batería «{op.track}» no tiene el carril «{op.lane}».",
                path=f"tracks.{op.track}.pattern.lanes.{op.lane}",
                hint="Add the lane first (set tracks.<id>.pattern.lanes.<lane>).",
            )
        if op.step >= pattern.steps_per_bar:
            raise MuestraError.one(
                "out_of_range",
                f"El paso {op.step} no existe: el compás tiene {pattern.steps_per_bar} pasos "
                f"(0 a {pattern.steps_per_bar - 1}).",
                path=f"tracks.{op.track}.pattern",
                hint="step is 0-based inside the bar.",
            )
        until = track.until_bar or song.meta.length_bars + 1
        if not track.at_bar <= op.bar < until:
            raise MuestraError.one(
                "out_of_range",
                f"El compás {op.bar} está fuera de la batería «{op.track}» "
                f"(compases {track.at_bar} a {until - 1}).",
                path=f"tracks.{op.track}.pattern.lanes.{op.lane}.overrides.{op.bar}",
                hint="Edit a bar inside the track window.",
            )
        # chars of the bar as written (keeps the author's notation)
        raw_lane = to_plain(get_value(doc, f"tracks.{op.track}.pattern.lanes.{op.lane}"))
        overrides = {int(k): v for k, v in (raw_lane.get("overrides") or {}).items()}
        spp = pattern.steps_per_bar
        tile_steps = (raw_lane.get("steps") or "").replace(" ", "")
        if not tile_steps:
            tile_steps = "." * (spp * pattern.bars)
        tile = (op.bar - track.at_bar) % pattern.bars
        tile_chars = list(tile_steps[tile * spp : (tile + 1) * spp])
        chars = list((overrides.get(op.bar) or "".join(tile_chars)).replace(" ", ""))
        chars[op.step] = _vel_char(op.velocity)
        path = f"tracks.{op.track}.pattern.lanes.{op.lane}.overrides.{op.bar}"
        current = lane_bar_steps(pattern, lane, op.bar, track.at_bar)
        if parse_steps("".join(chars)) == parse_steps("".join(tile_chars)):
            # back to the pattern: drop the exception (and an empty overrides map)
            if op.bar not in overrides:
                return []
            if len(overrides) == 1:
                return [
                    UnsetOp(
                        path=f"tracks.{op.track}.pattern.lanes.{op.lane}.overrides",
                        expect=raw_lane.get("overrides"),
                    )
                ]
            return [UnsetOp(path=path, expect=overrides[op.bar])]
        if parse_steps("".join(chars)) == current and op.bar in overrides:
            return []
        if op.bar in overrides:
            return [SetOp(path=path, value="".join(chars), expect=overrides[op.bar])]
        return [SetOp(path=path, value="".join(chars))]
    if op.op == "add_hits":
        track = _pattern_track(song, op.track)
        step = BEATS_PER_BAR / track.pattern.steps_per_bar
        out = []
        for h in op.hits:
            beat = h.beat
            if op.quantize == "step":
                beat = 1 + round((beat - 1) / step) * step
            elif op.quantize == "beat":
                beat = 1 + round(beat - 1)
            value = {"beat": round(beat, 6), "lane": h.lane}
            if h.velocity != 1.0:
                value["velocity"] = round(h.velocity, 4)
            if h.sample:
                value["sample"] = h.sample
            out.append(InsertOp(path=f"tracks.{op.track}.pattern.hits", value=value))
        return out
    if op.op in ("split_clip", "duplicate_clip"):
        track = _track(song, op.track)
        if track.clips is None or not 0 <= op.clip < len(track.clips):
            raise MuestraError.one(
                "ref_not_found",
                f"El track «{op.track}» no tiene el clip {op.clip}.",
                path=f"tracks.{op.track}.clips[{op.clip}]",
                hint="Clip indices are 0-based (song.state).",
            )
        raw = to_plain(get_value(doc, f"tracks.{op.track}.clips[{op.clip}]"))
        clip = track.clips[op.clip]
        base = next(
            (c.length_beats for c in song.chops if c.id == clip.chop), clip.length_beats or 0
        )
        pass_len = clip.pass_beats(base)
        start = (clip.at_bar - 1) * BEATS_PER_BAR + clip.offset_beats
        path = f"tracks.{op.track}.clips"
        if op.op == "duplicate_clip":
            dup = copy.deepcopy(raw)
            if op.at_bar is not None:
                dup["at_bar"] = op.at_bar
                dup["offset_beats"] = op.offset_beats or 0
            else:
                end = start + pass_len * clip.repeat
                dup["at_bar"] = int(end // BEATS_PER_BAR) + 1
                dup["offset_beats"] = round(end - (dup["at_bar"] - 1) * BEATS_PER_BAR, 6)
            if not dup.get("offset_beats"):
                dup.pop("offset_beats", None)
            return [InsertOp(path=path, index=op.clip + 1, value=dup)]
        cut = op.at_beat - 1 - start
        total = pass_len * clip.repeat
        if not 1e-6 < cut < total - 1e-6:
            raise MuestraError.one(
                "out_of_range",
                "Ese punto no está dentro del clip: no hay nada que cortar.",
                path=f"{path}[{op.clip}]",
                hint="at_beat must fall strictly inside the clip (1-indexed song beats).",
            )
        k = math.floor(cut / pass_len + 1e-9)
        inside = cut - k * pass_len
        left, right = copy.deepcopy(raw), copy.deepcopy(raw)
        inst = {int(n): v for n, v in (raw.get("instances") or {}).items()}
        if abs(inside) < 1e-6:
            # on a repeat boundary: k passes left, the rest right
            left["repeat"] = k
            right["repeat"] = clip.repeat - k
            rb = start + k * pass_len
            left["instances"] = {str(n): v for n, v in inst.items() if n <= k}
            right["instances"] = {str(n - k): v for n, v in inst.items() if n > k}
        else:
            # inside pass k+1: [k passes] [head of pass k+1] [tail of pass k+1] [rest]
            pieces = []
            if k:
                a = copy.deepcopy(raw)
                a["repeat"] = k
                a["instances"] = {str(n): v for n, v in inst.items() if n <= k}
                pieces.append((start, a))
            stretch = clip.stretch
            head = copy.deepcopy(raw)
            head.update(repeat=1, length_beats=round(inside, 6), instances={})
            if inst.get(k + 1):
                head["instances"] = {"1": inst[k + 1]}
            pieces.append((start + k * pass_len, head))
            tail = copy.deepcopy(raw)
            tail.update(
                repeat=1,
                length_beats=round(pass_len - inside, 6),
                trim_start_beats=round(clip.trim_start_beats + inside / stretch, 6),
                instances={"1": inst[k + 1]} if inst.get(k + 1) else {},
            )
            pieces.append((start + k * pass_len + inside, tail))
            if clip.repeat - k - 1 > 0:
                rest = copy.deepcopy(raw)
                rest["repeat"] = clip.repeat - k - 1
                rest["instances"] = {str(n - k - 1): v for n, v in inst.items() if n > k + 1}
                pieces.append((start + (k + 1) * pass_len, rest))
            out = []
            for i, (pos, piece) in enumerate(pieces):
                _place(piece, pos)
                _tidy(piece)
                if i == 0:
                    out.append(SetOp(path=f"{path}[{op.clip}]", value=piece, expect=raw))
                else:
                    out.append(InsertOp(path=path, index=op.clip + i, value=piece))
            return out
        _place(left, start)
        _place(right, rb)
        _tidy(left)
        _tidy(right)
        return [
            SetOp(path=f"{path}[{op.clip}]", value=left, expect=raw),
            InsertOp(path=path, index=op.clip + 1, value=right),
        ]
    if op.op == "add_point":
        existing = next((a for a in song.automation if a.target == op.target), None)
        if existing is None:
            value = {
                "target": op.target,
                "curve": op.curve,
                "points": [[op.bar, op.value], [op.bar, op.value]],
            }
            return [InsertOp(path="automation", value=value)]
        index = sum(1 for p in existing.points if p[0] <= op.bar)
        return [
            InsertOp(path=f"automation.{op.target}.points", index=index, value=[op.bar, op.value])
        ]
    if op.op == "revert_to":
        raise AssertionError("revert_to is resolved by the tool")
    raise AssertionError(op.op)


def _place(clip: dict, beat0: float) -> None:
    bar = int(math.floor(beat0 / BEATS_PER_BAR + 1e-9)) + 1
    off = round(beat0 - (bar - 1) * BEATS_PER_BAR, 6)
    clip["at_bar"] = bar
    if abs(off) > 1e-9:
        clip["offset_beats"] = off
    else:
        clip.pop("offset_beats", None)


def _tidy(clip: dict) -> None:
    """Drop fields at their default so the YAML stays as short as hand-written."""
    for k, default in (("repeat", 1), ("trim_start_beats", 0), ("instances", {})):
        if k in clip and clip[k] == default:
            del clip[k]


def apply_ops(doc, ops: list) -> tuple[list, list]:
    """Apply ``ops`` (semantic or primitive) in order on ``doc`` in place.
    Returns ``(applied_primitives, inverse_primitives)``; the inverse undoes the
    whole batch when applied in order."""
    applied: list = []
    inverse: list = []
    for op in ops:
        for prim in _compile(op, doc):
            inv = _apply_primitive(doc, prim)
            if prim.op in ("set", "unset", "remove") and inv:
                # the applied op records what it replaced: replaying it (redo)
                # conflicts instead of overwriting someone else's change.
                old = inv[0].value if inv[0].op in ("set", "insert") else None
                prim = type(prim)(**{**prim.model_dump(), "expect": old})
            applied.append(prim)
            inverse[0:0] = inv
    return applied, inverse
