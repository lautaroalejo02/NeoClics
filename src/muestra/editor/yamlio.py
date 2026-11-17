"""ruamel round-trip helpers for programmatic edits that read like hand edits."""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from ..project.validate import _YAML

__all__ = ["new_map", "to_node", "to_plain", "dump_bytes", "load_bytes", "revision_of"]

#: string fields written quoted (step strings start with '.', URIs have ':').
_STEPS = re.compile(r"^[xXoO1-9.\-_ ]+$")
_QUOTED = {"steps", "sample", "file", "midi", "preset"}


def new_map() -> CommentedMap:
    return CommentedMap()


def _is_flow(node: Any) -> bool:
    fa = getattr(node, "fa", None)
    return bool(fa is not None and fa.flow_style())


def to_node(value: Any, like: Any = None, key: Any = None) -> Any:
    """Plain JSON -> ruamel node. ``like`` is a sibling/previous node whose
    flow/block style the new node copies (a clip added next to flow-style
    clips is written ``{chop: c1, at_bar: 3}``)."""
    if isinstance(value, Mapping):
        m = CommentedMap()
        for k, v in value.items():
            kk = int(k) if key in _INT_KEYED and str(k).isdigit() else k
            m[kk] = to_node(v, None, kk)
        if isinstance(like, CommentedMap):
            flow = _is_flow(like)
        else:
            flow = key in _INT_KEYED or (
                key not in _BLOCK and all(_small(v) for v in value.values())
            )
        if flow:
            m.fa.set_flow_style()
        else:
            m.fa.set_block_style()
        return m
    if isinstance(value, (list, tuple)):
        s = CommentedSeq()
        for v in value:
            s.append(to_node(v, None, key))
        if key == "points" or all(not isinstance(v, (Mapping, list, tuple)) for v in value):
            s.fa.set_flow_style()
        return s
    if isinstance(value, str) and (key in _QUOTED or _STEPS.match(value) or "://" in value):
        return DoubleQuotedScalarString(value)
    return value


_INT_KEYED = ("overrides", "instances")
_BLOCK = ("tracks", "meta", "master", "pattern", "lanes")


def _small(v: Any) -> bool:
    if isinstance(v, Mapping):
        return all(not isinstance(x, (Mapping, list, tuple)) for x in v.values())
    if isinstance(v, (list, tuple)):
        return all(not isinstance(x, Mapping) for x in v) and len(v) <= 8
    return True


def to_plain(node: Any) -> Any:
    if isinstance(node, Mapping):
        return {(str(k) if isinstance(k, int) else k): to_plain(v) for k, v in node.items()}
    if isinstance(node, Sequence) and not isinstance(node, str):
        return [to_plain(v) for v in node]
    if isinstance(node, str):
        return str(node)
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    return node


def dump_bytes(doc: CommentedMap) -> bytes:
    buf = io.StringIO()
    _YAML.dump(doc, buf)
    return buf.getvalue().encode("utf-8")


def load_bytes(data: bytes) -> CommentedMap:
    doc = _YAML.load(data.decode("utf-8"))
    return doc if doc is not None else CommentedMap()


def revision_of(data: bytes) -> str:
    """Revision id of a song.yaml content: first 16 hex of its sha256."""
    return hashlib.sha256(data).hexdigest()[:16]
