"""Tool stubs owned by WP4 (sample library): library.index / library.search."""

from __future__ import annotations

from pydantic import Field

from ..schemas._base import ContractModel, KeyStr, RelPath
from ..toolbase import ProjectScoped, ToolOutput

__all__ = [
    "LibraryHit",
    "LibraryIndexParams",
    "LibraryIndexResult",
    "LibrarySearchParams",
    "LibrarySearchResult",
    "library_index",
    "library_search",
]

_WP = "WP4"


class LibraryIndexParams(ProjectScoped):
    path: str = Field(description="Directory to index (added to muestra.toml library).")


class LibraryIndexResult(ToolOutput):
    indexed: int = Field(ge=0)
    index_path: RelPath = Field(description="Project-relative path of the index file.")


def library_index(p: LibraryIndexParams) -> LibraryIndexResult:
    """Index a sample directory: BPM, key, type and tags per file."""
    raise NotImplementedError(_WP)


class LibraryHit(ContractModel):
    uri: str = Field(description="library:// URI usable in song.yaml.")
    path: RelPath = Field(description="Path relative to its library dir.")
    bpm: float | None = None
    key: KeyStr | None = None
    type: str | None = Field(default=None, description="e.g. kick/snare/clap/hat/loop/oneshot.")
    tags: list[str] = Field(default_factory=list)


class LibrarySearchParams(ProjectScoped):
    query: str = Field(default="", description="Free-text query; empty lists all.")
    bpm: float | None = Field(default=None, gt=0)
    key: KeyStr | None = None
    type: str | None = None
    limit: int = Field(default=20, ge=1, le=200)


class LibrarySearchResult(ToolOutput):
    results: list[LibraryHit] = Field(default_factory=list)


def library_search(p: LibrarySearchParams) -> LibrarySearchResult:
    """Search the index: text query plus optional bpm/key/type filters."""
    raise NotImplementedError(_WP)
