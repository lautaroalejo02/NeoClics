"""Sample-library contracts: ``.muestra-index.json`` and search hits.

The index file lives INSIDE each library root as
``.muestra-index.json`` (never in the project). ``library.index``
(WP4) writes it; ``muestra.library.core.lookup`` and the validator's
sample-warp check read it.

A sample with a known ``bpm`` may be warped like a chop; a sample with
no ``bpm`` (one-shots, plain project paths) plays at native speed — and
asking for a warp on it is a validation error.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ._base import ContractModel, KeyStr, RelPath

__all__ = ["LibraryHit", "LibraryIndex", "LibraryIndexEntry"]


class LibraryIndexEntry(ContractModel):
    """One indexed file inside a library root."""

    path: RelPath = Field(description="Path relative to the library root.")
    bpm: float | None = Field(
        default=None,
        gt=0,
        description="Detected/tagged source BPM; null = no warp possible.",
    )
    key: KeyStr | None = None
    type: str | None = Field(default=None, description="e.g. kick/snare/clap/hat/loop/oneshot.")
    tags: list[str] = Field(default_factory=list)


class LibraryIndex(ContractModel):
    """``.muestra-index.json`` inside a library root."""

    schema_version: Literal[0] = 0
    entries: list[LibraryIndexEntry] = Field(default_factory=list)


class LibraryHit(ContractModel):
    """A search/lookup result, usable directly in song.yaml."""

    uri: str = Field(description="library:// URI usable in song.yaml.")
    root: str = Field(
        description="The library root this hit lives in, as configured (may be absolute)."
    )
    path: RelPath = Field(description="Path relative to its library root.")
    bpm: float | None = None
    key: KeyStr | None = None
    type: str | None = Field(default=None, description="e.g. kick/snare/clap/hat/loop/oneshot.")
    tags: list[str] = Field(default_factory=list)
