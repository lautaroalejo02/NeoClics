"""muestra.toml contract (spec 7.1) — per-project configuration.

Written with tomli-w, read with tomllib. ``library`` entries are
directories resolved relative to the project root (``..`` allowed) or
absolute; ``library://`` URIs search them in order.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_core import PydanticCustomError

from ._base import ContractModel

__all__ = ["ProjectConfig", "SeparationConfig"]


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
    library: list[str] = Field(
        default_factory=list,
        description="Library directories searched by 'library://' URIs, in "
        "order. Relative paths resolve against the project dir ('..' "
        "allowed); absolute paths are allowed.",
    )
    separation: SeparationConfig = Field(default_factory=SeparationConfig)

    @field_validator("library", mode="before")
    @classmethod
    def _normalise_library(cls, v: object) -> object:
        if isinstance(v, str):
            v = [v]
        if isinstance(v, list):
            for p in v:
                if not isinstance(p, str) or not p.strip():
                    raise PydanticCustomError(
                        "invalid_path",
                        "Library entries must be non-empty path strings.",
                        {"hint": "Use paths like 'library' or '../library'."},
                    )
                if "\\" in p:
                    raise PydanticCustomError(
                        "invalid_path",
                        "Library path {p!r}: use '/' separators.",
                        {"p": p, "hint": "Use POSIX separators for portability."},
                    )
        return v
