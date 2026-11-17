"""Shared building blocks for every muestra contract.

Every contract model forbids unknown fields (spec 7.3: "Campos
desconocidos: error, no se ignoran en silencio") and is JSON/YAML
serialisable.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_validators import AfterValidator
from pydantic_core import PydanticCustomError

__all__ = [
    "ContractModel",
    "IdStr",
    "VersionStr",
    "KeyStr",
    "LibraryURI",
    "RelPath",
    "FileRef",
    "LaneName",
    "LIBRARY_SCHEME",
    "is_library_uri",
]


class ContractModel(BaseModel):
    """Base for all contract models: unknown fields are errors."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# ids
# ---------------------------------------------------------------------------

#: ids are lowercase slugs; they appear in automation target paths, so '.'
#: is not allowed.
ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
IdStr = Annotated[
    str,
    Field(
        pattern=ID_PATTERN,
        description="Lowercase slug: ^[a-z][a-z0-9_-]{0,63}$",
    ),
]

#: lane names inside a drum pattern follow the same rule as ids.
LaneName = IdStr

#: render version ids: v001, v002, ...
VersionStr = Annotated[
    str,
    Field(pattern=r"^v\d{3,}$", description="Render version id like 'v001'."),
]


# ---------------------------------------------------------------------------
# musical key (song meta.key), e.g. "F#m", "A", "Bbmaj", "C#minor"
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"^([a-g])(#|b|)(m|min|maj|minor|major|)$", re.IGNORECASE)


def _normalise_key(v: str) -> str:
    m = _KEY_RE.match(v)
    if not m:
        raise PydanticCustomError(
            "invalid_key",
            "Invalid key {v!r}; use forms like 'F#m', 'A', 'Bbmaj', 'C#minor'.",
            {"v": v, "hint": "Tonic letter A-G, optional #/b, optional mode suffix."},
        )
    letter, acc, mode = m.groups()
    return f"{letter.upper()}{acc}{mode.lower()}"


KeyStr = Annotated[
    str,
    AfterValidator(_normalise_key),
    Field(description="Musical key, e.g. 'F#m', 'A', 'Bbmaj', 'C#minor'."),
]


# ---------------------------------------------------------------------------
# file references
# ---------------------------------------------------------------------------

LIBRARY_SCHEME = "library://"
_LIBRARY_URI_RE = re.compile(r"^library://[A-Za-z0-9][A-Za-z0-9._~/-]*$")


def is_library_uri(v: str) -> bool:
    return v.startswith(LIBRARY_SCHEME)


def _check_library_uri(v: str) -> str:
    if not _LIBRARY_URI_RE.match(v):
        raise PydanticCustomError(
            "invalid_uri",
            "Malformed library URI {v!r}.",
            {
                "v": v,
                "hint": "Use 'library://<path>' with a relative POSIX path, "
                "e.g. 'library://kits/disco-01/kick.wav'.",
            },
        )
    rest = v[len(LIBRARY_SCHEME) :]
    segments = rest.split("/")
    if (
        any(seg in ("", ".", "..") for seg in segments)
        or rest.startswith("/")
        or rest.endswith("/")
    ):
        raise PydanticCustomError(
            "invalid_uri",
            "Malformed library URI {v!r}: empty, '.' or '..' segments are not allowed.",
            {"v": v, "hint": "Use a plain relative path inside the library."},
        )
    return v


def _check_rel_path(v: str) -> str:
    if not v or v.strip() != v:
        raise PydanticCustomError(
            "invalid_path",
            "Invalid path {v!r}.",
            {"v": v, "hint": "Use a project-relative POSIX path."},
        )
    if "\\" in v:
        raise PydanticCustomError(
            "invalid_path",
            "Invalid path {v!r}: use '/' separators, not '\\\\'.",
            {"v": v, "hint": "Use POSIX separators so the song is portable."},
        )
    if v.startswith("/") or (len(v) > 1 and v[1] == ":"):
        raise PydanticCustomError(
            "invalid_path",
            "Invalid path {v!r}: absolute paths are not allowed.",
            {"v": v, "hint": "Use a project-relative path, or 'library://' for library files."},
        )
    segments = v.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        raise PydanticCustomError(
            "invalid_path",
            "Invalid path {v!r}: empty, '.' or '..' segments are not allowed.",
            {"v": v, "hint": "Keep paths inside the project; library files use 'library://'."},
        )
    return v


LibraryURI = Annotated[
    str,
    AfterValidator(_check_library_uri),
    Field(
        description="Library URI: 'library://<relative posix path>', resolved "
        "against the library dirs in muestra.toml."
    ),
]

RelPath = Annotated[
    str,
    AfterValidator(_check_rel_path),
    Field(description="Project-relative POSIX path ('/' separators, no '..', no absolute paths)."),
]


def _check_file_ref(v: str) -> str:
    if is_library_uri(v):
        return _check_library_uri(v)
    return _check_rel_path(v)


#: A reference to an audio (or other) file: either a project-relative path
#: or a 'library://' URI.
FileRef = Annotated[
    str,
    AfterValidator(_check_file_ref),
    Field(description="Project-relative POSIX path or 'library://<path>' URI."),
]
