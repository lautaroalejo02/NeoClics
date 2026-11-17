"""Where are the sample libraries? (WP4, spec 7.3 ``library://`` rules.)

Precedence, highest first (earlier roots win when two hold the same
relative path):

1. ``[library] roots`` in the project's ``muestra.toml`` (relative paths
   resolve against the project dir, ``..`` allowed).
2. ``MUESTRA_LIBRARY`` environment variable: one or more directories
   separated by the OS path separator (``;`` on Windows, ``:`` elsewhere).
3. The default ``~/Documents/MuestraLibrary``, when that directory exists
   and ``MUESTRA_LIBRARY`` is unset.

Opt-outs: an explicitly empty ``roots = []`` in ``muestra.toml``, or
``MUESTRA_LIBRARY`` set to the empty string / ``none``, makes the project
authoritative — no environment or default roots are appended.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..schemas.config import LibraryConfig

__all__ = [
    "ENV_VAR",
    "default_library_path",
    "effective_roots",
    "env_roots",
    "fallback_disabled",
]

ENV_VAR = "MUESTRA_LIBRARY"


def default_library_path() -> Path:
    """``~/Documents/MuestraLibrary`` (may not exist)."""
    return Path.home() / "Documents" / "MuestraLibrary"


def fallback_disabled() -> bool:
    """True when ``MUESTRA_LIBRARY`` is set to '' or 'none'."""
    raw = os.environ.get(ENV_VAR)
    return raw is not None and raw.strip().lower() in ("", "none")


def env_roots() -> list[Path]:
    """Directories named in ``MUESTRA_LIBRARY`` (absolute, ``~`` expanded)."""
    raw = os.environ.get(ENV_VAR)
    if raw is None or raw.strip().lower() in ("", "none"):
        return []
    out: list[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if part:
            out.append(Path(part).expanduser().resolve())
    return out


def effective_roots(cfg: LibraryConfig, project_root: Path) -> list[Path]:
    """Library roots for a project, in precedence order (see module doc)."""
    roots: list[Path] = []
    for entry in cfg.roots:
        p = Path(entry).expanduser()
        roots.append(p if p.is_absolute() else (project_root / p).resolve())

    explicit_empty = "roots" in cfg.model_fields_set and not cfg.roots
    if not explicit_empty and not fallback_disabled():
        roots.extend(env_roots())
        default = default_library_path()
        if os.environ.get(ENV_VAR) is None and default.is_dir():
            roots.append(default.resolve())

    seen: set[str] = set()
    unique: list[Path] = []
    for r in roots:
        key = os.path.normcase(str(r.resolve())) if r.exists() else os.path.normcase(str(r))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique
