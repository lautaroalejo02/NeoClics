"""muestra.toml contract (spec 7.1) — per-project configuration.

Written with tomli-w, read with tomllib.

* ``[library]`` — ``roots``: directories searched by ``library://`` URIs,
  resolved relative to the project root (``..`` allowed) or absolute.
  The sample index lives INSIDE each root as ``.muestra-index.json`` —
  never in the project.
* ``[plugins]`` — ``search_dirs`` (extra directories scanned for
  VST3/AU plugins) plus per-plugin alias tables:
  ``surge-xt = {windows = "...", macos = "..."}`` maps an alias usable
  in song.yaml to an OS-specific install path.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from ._base import ID_PATTERN, ContractModel

__all__ = [
    "LibraryConfig",
    "PluginPaths",
    "PluginsConfig",
    "ProjectConfig",
    "SeparationConfig",
]

#: name of the index file inside each library root.
LIBRARY_INDEX_FILE = ".muestra-index.json"


class LibraryConfig(ContractModel):
    """``[library]`` section: the sample-library roots."""

    roots: list[str] = Field(
        default_factory=list,
        description="Library roots searched by 'library://' URIs, in "
        "order. Relative paths resolve against the project dir ('..' "
        "allowed); absolute paths are allowed. Each root's index lives "
        "inside the root as '.muestra-index.json'.",
    )

    @field_validator("roots", mode="before")
    @classmethod
    def _normalise_roots(cls, v: object) -> object:
        if isinstance(v, str):
            v = [v]
        if isinstance(v, list):
            for p in v:
                if not isinstance(p, str) or not p.strip():
                    raise PydanticCustomError(
                        "invalid_path",
                        "Library roots must be non-empty path strings.",
                        {"hint": "Use paths like 'library' or '../library'."},
                    )
                if "\\" in p:
                    raise PydanticCustomError(
                        "invalid_path",
                        "Library path {p}: use '/' separators.",
                        {"p": p, "hint": "Use POSIX separators for portability."},
                    )
        return v


class PluginPaths(ContractModel):
    """OS-specific install paths for one plugin alias."""

    windows: str | None = None
    macos: str | None = None
    linux: str | None = None

    @model_validator(mode="after")
    def _at_least_one_os(self) -> PluginPaths:
        if not any([self.windows, self.macos, self.linux]):
            raise PydanticCustomError(
                "invalid_value",
                "A plugin alias needs at least one OS path.",
                {"hint": "Set 'windows', 'macos' and/or 'linux'."},
            )
        return self


class PluginsConfig(ContractModel):
    """``[plugins]`` section: plugin aliases and extra search dirs.

    In muestra.toml the aliases sit directly inside ``[plugins]``::

        [plugins]
        search_dirs = ["C:/VST3"]
        surge-xt = {windows = "C:/VST3/Surge XT.vst3", macos = "/Library/...vst3"}
    """

    search_dirs: list[str] = Field(
        default_factory=list,
        description="Extra directories scanned for VST3/AU plugins.",
    )
    aliases: dict[str, PluginPaths] = Field(
        default_factory=dict,
        description="alias -> per-OS path table (populated from the extra keys of [plugins]).",
    )

    @model_validator(mode="before")
    @classmethod
    def _collect_aliases(cls, v: object) -> object:
        if isinstance(v, dict):
            known = {"search_dirs", "aliases"}
            aliases = dict(v.get("aliases") or {})
            for k, val in v.items():
                if k in known:
                    continue
                if not isinstance(val, dict):
                    raise PydanticCustomError(
                        "invalid_value",
                        "Invalid [plugins] entry {k}.",
                        {
                            "k": k,
                            "hint": "Plugin aliases map a name to a per-OS "
                            'path table, e.g. surge-xt = {windows = "..."}.',
                        },
                    )
                if not re.match(ID_PATTERN, k):
                    raise PydanticCustomError(
                        "invalid_id",
                        "Invalid plugin alias {k}.",
                        {"k": k, "hint": f"Aliases must match {ID_PATTERN}."},
                    )
                aliases[k] = val
            return {"search_dirs": v.get("search_dirs", []), "aliases": aliases}
        return v


class SeparationConfig(ContractModel):
    backend: str = Field(
        default="audio-separator",
        min_length=1,
        description="Separation backend name, e.g. 'audio-separator'.",
    )
    model: str | None = Field(
        default=None, description="Model name/version understood by the backend."
    )


class ProjectConfig(ContractModel):
    schema_version: Literal[0] = 0
    sample_rate: int = Field(default=48000, gt=0, description="Internal render sample rate (Hz).")
    export_bit_depth: Literal[16, 24, 32] = 24
    library: LibraryConfig = Field(default_factory=LibraryConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    separation: SeparationConfig = Field(default_factory=SeparationConfig)

    @field_validator("library", mode="before")
    @classmethod
    def _bare_list_means_roots(cls, v: object) -> object:
        """Tolerate the pre-WP0.1 shape ``library = ["dir"]``."""
        if isinstance(v, (str, list)):
            return {"roots": [v] if isinstance(v, str) else v}
        return v
