"""Sample-library resolution seam (WP4 owns indexing/search).

Ruling (see docs/decisions.md, WP0.1):

* ``muestra.toml`` ``[library] roots`` lists directories searched by
  ``library://`` URIs, in order (see :mod:`.discovery` for the
  environment-variable and default-library fallbacks). The index file
  lives INSIDE each library root as ``.muestra-index.json`` — never in
  the project.
* ``lookup(project, uri)`` returns the :class:`LibraryHit` for a URI,
  or None when the file doesn't exist in any root. ``resolve(project,
  uri)`` returns the absolute :class:`Path`.
* A sample with a known ``bpm`` may be warped like a chop; a sample
  with no ``bpm`` (one-shots, plain project paths) plays at native
  speed — asking for a warp on it is a validation error (enforced by
  ``muestra.project.validate``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..schemas._base import LIBRARY_SCHEME
from ..schemas.config import LIBRARY_INDEX_FILE
from ..schemas.library import KitLicense, LibraryHit, LibraryIndex, LibraryIndexEntry

if TYPE_CHECKING:
    from ..project.core import Project

__all__ = [
    "LoadedIndex",
    "build_hit",
    "hit_from_entry",
    "index_path",
    "load_index",
    "loaded_index",
    "lookup",
    "resolve",
    "root_label",
]


def index_path(root: Path) -> Path:
    """The index file inside a library root."""
    return root / LIBRARY_INDEX_FILE


@dataclass
class LoadedIndex:
    """A parsed index plus lookup/search structures built lazily once."""

    root: Path
    index: LibraryIndex
    by_path: dict[str, LibraryIndexEntry] = field(default_factory=dict)
    #: scratch space for search.py (precomputed matrices etc.).
    cache: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_path = {e.path: e for e in self.index.entries}


_CACHE: dict[str, tuple[tuple[int, int], LoadedIndex | None]] = {}
_CACHE_MAX = 16


def loaded_index(root: Path) -> LoadedIndex | None:
    """Load (and cache by mtime+size) ``.muestra-index.json`` of a root.

    None when the file is absent or unparseable.
    """
    f = index_path(root)
    try:
        st = f.stat()
    except OSError:
        _CACHE.pop(str(f), None)
        return None
    sig = (st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(str(f))
    if hit is not None and hit[0] == sig:
        return hit[1]
    loaded: LoadedIndex | None
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        loaded = LoadedIndex(root=root, index=LibraryIndex.model_validate(raw))
    except (OSError, json.JSONDecodeError, ValidationError):
        loaded = None
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[str(f)] = (sig, loaded)
    return loaded


def load_index(root: Path) -> LibraryIndex | None:
    """Load ``.muestra-index.json`` from a library root; None if absent/bad."""
    loaded = loaded_index(root)
    return loaded.index if loaded is not None else None


def root_label(project: Project, root: Path) -> str:
    """The root as shown to agents: project-relative when it lives inside
    the project, otherwise absolute."""
    try:
        return root.resolve().relative_to(project.root).as_posix()
    except ValueError:
        return str(root)


def hit_from_entry(
    entry: LibraryIndexEntry,
    root: str,
    *,
    kits: dict[str, KitLicense] | None = None,
    detail: bool = False,
) -> LibraryHit:
    """A :class:`LibraryHit` for an index entry. ``detail`` adds the full
    feature block."""
    lic = kits.get(entry.kit) if (kits and entry.kit) else None
    return LibraryHit(
        uri=f"{LIBRARY_SCHEME}{entry.path}",
        root=root,
        path=entry.path,
        type=entry.type,
        subtype=entry.subtype,
        kind=entry.kind,
        kit=entry.kit,
        bpm=entry.bpm,
        key=entry.key,
        duration_s=entry.duration_s,
        loudness_lufs=entry.loudness_lufs,
        clipping=entry.clipping,
        tags=list(entry.tags),
        flags=list(entry.flags),
        features=entry.features if detail else None,
        license=lic,
    )


def build_hit(project: Project, root: Path, rel: str) -> LibraryHit | None:
    """The :class:`LibraryHit` for ``rel`` inside ``root``'s index."""
    loaded = loaded_index(root)
    if loaded is None:
        return None
    entry = loaded.by_path.get(rel)
    if entry is None:
        return None
    return hit_from_entry(entry, root_label(project, root), kits=loaded.index.kits)


def lookup(project: Project, uri: str) -> LibraryHit | None:
    """Index info for a ``library://`` URI; None when no root holds the file."""
    if not uri.startswith(LIBRARY_SCHEME):
        return None
    rel = uri[len(LIBRARY_SCHEME) :]
    for root in project.library_dirs():
        if not os.path.isfile(root / rel):
            continue
        hit = build_hit(project, root, rel)
        if hit is not None:
            return hit
        # file exists but isn't indexed: still reportable (no bpm)
        return LibraryHit(uri=uri, root=root_label(project, root), path=rel)
    return None


def resolve(project: Project, uri: str) -> Path:
    """Absolute path of a ``library://`` URI (delegates to
    :meth:`Project.resolve_library`)."""
    return project.resolve_library(uri, must_exist=True)
