"""Tool stubs owned by WP4 (sample library): library.index /
library.search.

Resolution seam: ``muestra.library.core.lookup``/``resolve`` read the
``.muestra-index.json`` inside each ``[library] roots`` dir.
"""

from __future__ import annotations

from pydantic import Field

from ..schemas._base import RelPath
from ..schemas.config import LIBRARY_INDEX_FILE
from ..schemas.library import LibraryHit
from ..toolbase import ProjectScoped, ToolOutput

__all__ = [
    "LibraryIndexParams",
    "LibraryIndexResult",
    "LibrarySearchParams",
    "LibrarySearchResult",
    "library_index",
    "library_search",
    "TOOLS",
]

_WP = "WP4"


class LibraryIndexParams(ProjectScoped):
    path: str = Field(description="Library root to index (added to muestra.toml [library] roots).")


class LibraryIndexResult(ToolOutput):
    indexed: int = Field(ge=0)
    index_path: RelPath = Field(
        description=f"Path of the index file ('{LIBRARY_INDEX_FILE}' inside the root)."
    )


def library_index(p: LibraryIndexParams) -> LibraryIndexResult:
    """Index a sample directory: BPM, key, type and tags per file, into
    ``<root>/.muestra-index.json``."""
    raise NotImplementedError(_WP)


class LibrarySearchParams(ProjectScoped):
    query: str = Field(default="", description="Free-text query; empty lists all.")
    bpm: float | None = Field(default=None, gt=0)
    key: str | None = Field(default=None, description="Key filter, e.g. 'F#m'.")
    type: str | None = None
    limit: int = Field(default=20, ge=1, le=200)


class LibrarySearchResult(ToolOutput):
    results: list[LibraryHit] = Field(default_factory=list)


def library_search(p: LibrarySearchParams) -> LibrarySearchResult:
    """Search the library roots' indexes: text query plus optional
    bpm/key/type filters."""
    raise NotImplementedError(_WP)


#: tools exported by this subpackage (collected by muestra.registry).
TOOLS = [
    (
        "library.index",
        library_index,
        LibraryIndexParams,
        LibraryIndexResult,
        {"long_running": True, "cli": ("library", "index")},
    ),
    (
        "library.search",
        library_search,
        LibrarySearchParams,
        LibrarySearchResult,
        {"readonly": True, "cli": ("library", "search")},
    ),
]
