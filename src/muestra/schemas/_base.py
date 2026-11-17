"""Shared building blocks for every muestra contract.

Every contract model forbids unknown fields (spec 7.3: "Campos
desconocidos: error, no se ignoran en silencio"), rejects NaN/inf
numbers, and is JSON/YAML serialisable. Every timestamp is a
timezone-aware ``datetime`` (UTC); naive input is a validation error.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.functional_validators import AfterValidator
from pydantic_core import PydanticCustomError

from ..music import KeyParseError, canonical_key

__all__ = [
    "ContractModel",
    "IdStr",
    "VersionStr",
    "KeyStr",
    "CandidateIdStr",
    "LibraryURI",
    "RelPath",
    "FileRef",
    "LaneName",
    "LIBRARY_SCHEME",
    "WINDOWS_RESERVED_NAMES",
    "is_library_uri",
]


class ContractModel(BaseModel):
    """Base for all contract models: unknown fields are errors,
    NaN/inf numbers are errors."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        allow_inf_nan=False,
    )


# ---------------------------------------------------------------------------
# ids
# ---------------------------------------------------------------------------

#: ids are lowercase slugs; they appear in automation target paths, so '.'
#: is not allowed.
ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"

#: Names Windows reserves as file/directory names (with or without an
#: extension). Rejected in relative path segments and in ids so a project
#: authored on macOS also validates on Windows, and vice versa.
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "AUX", "NUL", "PRN"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _check_id(v: str) -> str:
    if v.upper() in WINDOWS_RESERVED_NAMES:
        raise PydanticCustomError(
            "invalid_id",
            "{v} is a reserved name.",
            {
                "v": v,
                "hint": "Windows reserves CON, AUX, NUL, PRN, COM1-9, LPT1-9; pick another id.",
            },
        )
    return v


IdStr = Annotated[
    str,
    Field(
        pattern=ID_PATTERN,
        description="Lowercase slug: ^[a-z][a-z0-9_-]{0,63}$",
    ),
    AfterValidator(_check_id),
]

#: lane names inside a drum pattern follow the same rule as ids.
LaneName = IdStr

#: render version ids: v001, v002, ...
VersionStr = Annotated[
    str,
    Field(pattern=r"^v\d{3,}$", description="Render version id like 'v001'."),
]

#: global chop-candidate id: '<set_id>/<candidate>', e.g. 'cs001/cand2'.
CandidateIdStr = Annotated[
    str,
    Field(
        pattern=r"^cs\d{3,}/cand\d+$",
        description="Global chop candidate id '<csNNN>/<candN>', e.g. 'cs001/cand2'.",
    ),
]


# ---------------------------------------------------------------------------
# musical key (song meta.key): canonical 'F#m' minor / 'F#' major
# ---------------------------------------------------------------------------


def _normalise_key(v: str) -> str:
    try:
        return canonical_key(v)
    except KeyParseError as e:
        raise PydanticCustomError(
            "invalid_key",
            "Invalid key {v}; use forms like 'F#m', 'A', 'Bbmaj', 'a'.",
            {
                "v": v,
                "hint": "Tonic letter A-G, optional #/b, optional mode "
                "suffix ('m'/'min'/'minor' = minor, 'M'/'maj'/'major' = "
                "major; a bare lowercase letter is minor).",
            },
        ) from e


KeyStr = Annotated[
    str,
    AfterValidator(_normalise_key),
    Field(
        description="Musical key, canonical form 'F#m' (minor) or 'F#' "
        "(major). Accepted: 'F#m', 'F#min', 'F# minor', 'F#', 'F#M', "
        "'F#maj', bare lowercase 'a' = A minor."
    ),
]


# ---------------------------------------------------------------------------
# file references
# ---------------------------------------------------------------------------

LIBRARY_SCHEME = "library://"
_LIBRARY_URI_RE = re.compile(r"^library://[A-Za-z0-9][A-Za-z0-9._~/-]*$")


def is_library_uri(v: str) -> bool:
    return v.startswith(LIBRARY_SCHEME)


def _check_portable_segment(seg: str, v: str, code: str) -> None:
    """Reject path segments that break on some OS (R18).

    ':' anywhere (Windows drive/ADS), a trailing dot or space (Windows
    strips them), and Windows reserved names (with or without extension).
    """
    if ":" in seg:
        raise PydanticCustomError(
            code,
            "Invalid path {v}: ':' is not allowed in path segments.",
            {"v": v, "hint": "Remove the colon; it is not portable."},
        )
    if seg.endswith(".") or seg.endswith(" "):
        raise PydanticCustomError(
            code,
            "Invalid path {v}: segments may not end with '.' or ' '.",
            {"v": v, "hint": "Rename the file; Windows strips trailing dots/spaces."},
        )
    base = seg.split(".", 1)[0]
    if base.upper() in WINDOWS_RESERVED_NAMES:
        raise PydanticCustomError(
            code,
            "Invalid path {v}: {seg} is a reserved name.",
            {
                "v": v,
                "seg": seg,
                "hint": "Windows reserves CON, AUX, NUL, PRN, COM1-9, "
                "LPT1-9 (any extension); rename the file.",
            },
        )


def _check_segments(segments: list[str], v: str, code: str = "invalid_path") -> None:
    if any(seg in ("", ".", "..") for seg in segments):
        raise PydanticCustomError(
            code,
            "Invalid path {v}: empty, '.' or '..' segments are not allowed.",
            {"v": v, "hint": "Keep paths inside the project; library files use 'library://'."},
        )
    for seg in segments:
        _check_portable_segment(seg, v, code)


def _check_library_uri(v: str) -> str:
    if not _LIBRARY_URI_RE.match(v):
        raise PydanticCustomError(
            "invalid_uri",
            "Malformed library URI {v}.",
            {
                "v": v,
                "hint": "Use 'library://<path>' with a relative POSIX path, "
                "e.g. 'library://kits/disco-01/kick.wav'.",
            },
        )
    rest = v[len(LIBRARY_SCHEME) :]
    segments = rest.split("/")
    _check_segments(segments, v, "invalid_uri")
    return v


def _check_rel_path(v: str) -> str:
    if not v or v.strip() != v:
        raise PydanticCustomError(
            "invalid_path",
            "Invalid path {v}.",
            {"v": v, "hint": "Use a project-relative POSIX path."},
        )
    if "\\" in v:
        raise PydanticCustomError(
            "invalid_path",
            "Invalid path {v}: use '/' separators, not '\\\\'.",
            {"v": v, "hint": "Use POSIX separators so the song is portable."},
        )
    if v.startswith("/"):
        raise PydanticCustomError(
            "invalid_path",
            "Invalid path {v}: absolute paths are not allowed.",
            {"v": v, "hint": "Use a project-relative path, or 'library://' for library files."},
        )
    _check_segments(v.split("/"), v)
    return v


LibraryURI = Annotated[
    str,
    AfterValidator(_check_library_uri),
    Field(
        description="Library URI: 'library://<relative posix path>', resolved "
        "against the library roots in muestra.toml."
    ),
]

RelPath = Annotated[
    str,
    AfterValidator(_check_rel_path),
    Field(
        description="Project-relative POSIX path ('/' separators, no '..', "
        "no absolute paths, no ':' / trailing dots/spaces / reserved names)."
    ),
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
