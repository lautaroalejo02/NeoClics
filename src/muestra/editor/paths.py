"""ID-based paths into song.yaml (the ``versions.diff`` grammar, WP16).

A path addresses one node of the song document::

    meta.bpm                                chops.c1.warp.pitch_semitones
    tracks.sample.clips[2].at_bar           tracks.sample.clips[0].instances.3.mute
    tracks.drums.pattern.lanes.kick.steps   tracks.drums.pattern.lanes.kick.overrides.7
    tracks.drums.pattern.hits[4].velocity   tracks.bass.notes[12].pitch
    tracks.sample.fx.lp.cutoff_hz           tracks.sample.fx[1].ratio    master.fx.lim.ceiling_db
    automation.tracks.sample.fx.lp.cutoff_hz.points[1]               phrases.riff.notes[0]

Rules: a ``.name`` step on a LIST selects the element whose ``id`` is ``name``
(sources, chops, phrases, tracks, fx); on the ``automation`` list it selects by
``target`` (the longest dotted run that matches an existing target, so the
trailing ``curve``/``points`` is the field); ``[i]`` indexes a list (0-based);
digit keys of ``overrides``/``instances`` are integers. The resolver works on
plain JSON data and on ruamel round-trip documents alike.
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping, MutableSequence
from dataclasses import dataclass
from typing import Any

from ..errors import MuestraError

__all__ = ["Step", "parse_path", "resolve", "get_value", "PathError"]

_SEGMENT = re.compile(r"^([^.\[\]]*)((?:\[\d+\])*)$")
_INDEX = re.compile(r"\[(\d+)\]")
#: mapping keys whose digit keys are integers in the schema
INT_KEYED = {"overrides", "instances"}


@dataclass(frozen=True)
class Step:
    kind: str  # "key" | "index"
    value: Any


class PathError(MuestraError):
    pass


def _bad(path: str, why: str) -> MuestraError:
    return MuestraError.one(
        "invalid_path",
        f"La ruta «{path}» no apunta a nada de la canción ({why}).",
        path=path,
        value=path,
        hint="Usá rutas por id como tracks.drums.pattern.lanes.kick.overrides.7 o "
        "tracks.sample.clips[0].at_bar (índices desde 0).",
    )


def parse_path(path: str) -> list[Step]:
    """``"tracks.sample.clips[2].at_bar"`` -> key tracks, key sample, key clips,
    index 2, key at_bar. Empty path = the whole document."""
    if path == "":
        return []
    steps: list[Step] = []
    for seg in path.split("."):
        m = _SEGMENT.match(seg)
        if not m or (not m.group(1) and not m.group(2)):
            raise _bad(path, f"segmento «{seg}» mal formado")
        if m.group(1):
            steps.append(Step("key", m.group(1)))
        for idx in _INDEX.findall(m.group(2)):
            steps.append(Step("index", int(idx)))
    return steps


def _id_of(item: Any) -> Any:
    return item.get("id") if isinstance(item, MutableMapping) else None


def _map_key(node: MutableMapping, name: str, parent_key: Any) -> Any:
    if name in node:
        return name
    if name.isdigit() and int(name) in node:
        return int(name)
    if parent_key in INT_KEYED and name.isdigit():
        return int(name)
    return name


def resolve(doc: Any, path: str, *, create: bool = False) -> tuple[Any, Any]:
    """``(container, key)`` of the node at ``path``: ``container[key]`` is the
    node (key = int index for lists). With ``create`` missing intermediate
    MAPPING keys are created (the leaf itself need not exist)."""
    steps = parse_path(path)
    if not steps:
        raise _bad(path, "ruta vacía")
    node: Any = doc
    parent_key: Any = None
    i = 0
    while i < len(steps) - 1 or (
        i == len(steps) - 1 and isinstance(node, MutableSequence) and steps[i].kind == "key"
    ):
        step = steps[i]
        if isinstance(node, MutableSequence):
            if step.kind == "index":
                if not 0 <= step.value < len(node):
                    raise _bad(path, f"no hay elemento {step.value}")
                nxt, key = node[step.value], step.value
                i += 1
            elif parent_key == "automation":
                # longest run of key steps that is an existing target
                keys = []
                j = i
                while j < len(steps) and steps[j].kind == "key":
                    keys.append(steps[j].value)
                    j += 1
                found = None
                for n in range(len(keys), 0, -1):
                    target = ".".join(keys[:n])
                    hit = [
                        k
                        for k, a in enumerate(node)
                        if isinstance(a, MutableMapping) and a.get("target") == target
                    ]
                    if hit:
                        found = (hit[0], n)
                        break
                if found is None:
                    raise _bad(path, "no existe esa automatización")
                key = found[0]
                nxt = node[key]
                i += found[1]
                if i == len(steps):
                    return node, key
            else:
                hit = [k for k, item in enumerate(node) if _id_of(item) == step.value]
                if not hit:
                    raise _bad(path, f"no hay ningún elemento con id «{step.value}»")
                key = hit[0]
                nxt = node[key]
                i += 1
                if i == len(steps):
                    return node, key
        elif isinstance(node, MutableMapping):
            if step.kind != "key":
                raise _bad(path, "un índice sobre un mapeo")
            key = _map_key(node, step.value, parent_key)
            if key not in node or node[key] is None:
                if not create:
                    raise _bad(path, f"falta «{step.value}»")
                from .yamlio import new_map

                node[key] = new_map()
            nxt = node[key]
            i += 1
        else:
            raise _bad(path, "atraviesa un valor que no es lista ni mapeo")
        parent_key = (
            key if not isinstance(key, int) or isinstance(node, MutableMapping) else parent_key
        )
        node = nxt
    last = steps[-1]
    if isinstance(node, MutableSequence):
        if last.kind != "index":
            raise _bad(path, "falta el índice")
        return node, last.value
    if isinstance(node, MutableMapping):
        if last.kind != "key":
            raise _bad(path, "un índice sobre un mapeo")
        return node, _map_key(node, last.value, parent_key)
    raise _bad(path, "atraviesa un valor que no es lista ni mapeo")


_MISSING = object()


def get_value(doc: Any, path: str, default: Any = _MISSING) -> Any:
    try:
        container, key = resolve(doc, path)
        if isinstance(container, MutableSequence):
            if not 0 <= key < len(container):
                raise _bad(path, f"no hay elemento {key}")
            return container[key]
        if key not in container:
            raise _bad(path, "no existe")
        return container[key]
    except MuestraError:
        if default is _MISSING:
            raise
        return default
