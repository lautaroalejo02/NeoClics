"""Sample-library resolution seam (WP4 owns indexing/search).

Ruling (see docs/decisions.md, WP0.1):

* ``muestra.toml`` ``[library] roots`` lists directories searched by
  ``library://`` URIs, in order. The index file lives INSIDE each
  library root as ``.muestra-index.json`` — never in the project.
* ``lookup(project, uri)`` returns the :class:`LibraryHit` for a URI,
  or None when the file isn't indexed. ``resolve(project, uri)``
  returns the absolute :class:`Path`.
* A sample with a known ``bpm`` may be warped like a chop; a sample
  with no ``bpm`` (one-shots, plain project paths) plays at native
  speed — asking for a warp on it is a validation error (enforced by
  ``muestra.project.validate``).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..schemas._base import LIBRARY_SCHEME
from ..schemas.config import LIBRARY_INDEX_FILE
from ..schemas.library import LibraryHit, LibraryIndex

if TYPE_CHECKING:
    from ..project.core import Project

__all__ = [
    "index_path",
    "load_index",
    "lookup",
    "resolve",
    "build_hit",
]


def index_path(root: Path) -> Path:
    """The index file inside a library root."""
    return root / LIBRARY_INDEX_FILE


@lru_cache(maxsize=64)
def _load_index_cached(index_file: Path, mtime_ns: int) -> LibraryIndex | None:
    del mtime_ns  # cache-buster only
    try:
        raw = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return LibraryIndex.model_validate(raw)
    except ValidationError:
        return None


def load_index(root: Path) -> LibraryIndex | None:
    """Load ``.muestra-index.json`` from a library root; None if absent/bad."""
    index_file = index_path(root)
    try:
        mtime = index_file.stat().st_mtime_ns
    except OSError:
        return None
    return _load_index_cached(index_file, mtime)


def build_hit(project: Project, root: Path, rel: str) -> LibraryHit | None:
    """The :class:`LibraryHit` for ``rel`` inside ``root``'s index."""
    index = load_index(root)
    if index is None:
        return None
    root_cfg = str(root)
    # report the root as configured (project-relative when possible)
    try:
        root_cfg = str(root.relative_to(project.root))
    except ValueError:
        pass
    for entry in index.entries:
        if entry.path == rel:
            return LibraryHit(
                uri=f"{LIBRARY_SCHEME}{rel}",
                root=root_cfg,
                path=rel,
                bpm=entry.bpm,
                key=entry.key,
                type=entry.type,
                tags=list(entry.tags),
            )
    return None


def lookup(project: Project, uri: str) -> LibraryHit | None:
    """Index info for a ``library://`` URI; None when not indexed."""
    if not uri.startswith(LIBRARY_SCHEME):
        return None
    rel = uri[len(LIBRARY_SCHEME) :]
    for root in project.library_dirs():
        if not (root / rel).is_file():
            continue
        hit = build_hit(project, root, rel)
        if hit is not None:
            return hit
        # file exists but isn't indexed: still reportable (no bpm)
        return LibraryHit(uri=uri, root=str(root), path=rel)
    return None


def resolve(project: Project, uri: str) -> Path:
    """Absolute path of a ``library://`` URI (delegates to
    :meth:`Project.resolve_library`)."""
    return project.resolve_library(uri, must_exist=True)
