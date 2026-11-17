"""Project folder: the 7.1 layout, muestra.toml, path resolution, id allocation.

A Muestra project folder is its own git repo (created by
:func:`create_project` via ``muestra.versions.core.init_repo``).
``renders/`` and ``cache/`` are entirely git-ignored — what git versions
is ``song.yaml``, ``midi/``, ``muestra.toml``, ``feedback.jsonl`` and
``docs/`` (plus ``sources/`` and ``library/``, which stay tracked).

No global state: every function takes a project path or :class:`Project`.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w
import yaml
from pydantic import ValidationError

from ..errors import MuestraError, issues_from_validation_error
from ..schemas._base import is_library_uri
from ..schemas.config import LibraryConfig, ProjectConfig
from ..schemas.song import Song
from ..versions.core import init_repo
from ..versions.core import next_version_id as _next_version_id

__all__ = [
    "Project",
    "CONFIG_FILE",
    "SONG_FILE",
    "FEEDBACK_FILE",
    "PROJECT_DIRS",
    "create_project",
    "open_project",
    "load_config",
    "save_config",
    "is_project_dir",
    "allocate_id",
    "next_source_id",
    "next_version_id",
    "next_set_id",
]

CONFIG_FILE = "muestra.toml"
SONG_FILE = "song.yaml"
FEEDBACK_FILE = "feedback.jsonl"

#: spec 7.1 layout; cache/chops is a WP0 addition (see docs/decisions.md).
PROJECT_DIRS = (
    "sources",
    "cache",
    "cache/stems",
    "cache/analysis",
    "cache/previews",
    "cache/chops",
    "midi",
    "renders",
    "library",
    "docs",
)

#: renders/ and cache/ are entirely git-ignored: audio, meta.json and
#: metrics.json are derived data. The version commit carries song.yaml,
#: midi/, muestra.toml, feedback.jsonl and docs/.
_GITIGNORE = """\
# muestra project
renders/
cache/
.venv/
__pycache__/
"""

_DECISIONS_MD = """\
# Project decisions

Design decisions and contract changes for this project live here.
"""


def is_project_dir(path: Path) -> bool:
    return (path / CONFIG_FILE).is_file()


def load_config(project_root: Path) -> ProjectConfig:
    """Load and validate muestra.toml."""
    cfg_path = project_root / CONFIG_FILE
    if not cfg_path.is_file():
        raise MuestraError.one(
            "file_not_found",
            f"Not a muestra project: {cfg_path} does not exist.",
            path=CONFIG_FILE,
            hint="Run project.create, or pass the project directory "
            "(the one containing muestra.toml).",
        )
    try:
        raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise MuestraError.one(
            "parse_error",
            f"Invalid TOML in {CONFIG_FILE}: {e}",
            path=CONFIG_FILE,
            hint="Fix the TOML syntax.",
        ) from e
    try:
        return ProjectConfig.model_validate(raw)
    except ValidationError as e:
        raise MuestraError(issues_from_validation_error(e, path_prefix=CONFIG_FILE)) from e


def save_config(project: Project, config: ProjectConfig | None = None) -> Path:
    """Write the project's muestra.toml (full explicit dump)."""
    cfg = config or project.config
    path = project.root / CONFIG_FILE
    # TOML has no null: drop None values
    path.write_text(
        tomli_w.dumps(cfg.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )
    return path


@dataclass(frozen=True)
class Project:
    """An open project. ``root`` is absolute."""

    root: Path
    config: ProjectConfig

    def path(self, rel: str | Path = "") -> Path:
        """Project-absolute path for a project-relative POSIX path."""
        return self.root / Path(str(rel))

    @property
    def song_path(self) -> Path:
        return self.root / SONG_FILE

    @property
    def feedback_path(self) -> Path:
        return self.root / FEEDBACK_FILE

    @property
    def renders_dir(self) -> Path:
        return self.root / "renders"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    def library_dirs(self) -> list[Path]:
        """Configured library roots, absolute, in search order."""
        dirs: list[Path] = []
        for entry in self.config.library.roots:
            p = Path(entry)
            dirs.append(p if p.is_absolute() else (self.root / p).resolve())
        return dirs

    def resolve(self, ref: str, *, must_exist: bool = True) -> Path:
        """Resolve a FileRef (project-relative path or library:// URI).

        When the file exists, each path component's case is compared
        exactly against the directory listing, so a project authored on a
        case-insensitive filesystem (Windows, default macOS) still
        validates on a case-sensitive one.
        """
        if is_library_uri(ref):
            return self.resolve_library(ref, must_exist=must_exist)
        p = self.root / Path(ref)
        if must_exist:
            _require_exists(self.root, ref, p)
        return p

    def resolve_library(self, uri: str, *, must_exist: bool = True) -> Path:
        """Resolve 'library://<path>' against configured library roots, in order."""
        rest = uri[len("library://") :]
        dirs = self.library_dirs()
        if not dirs:
            raise MuestraError.one(
                "invalid_uri",
                f"Cannot resolve {uri!r}: no library roots are configured.",
                value=uri,
                hint="Add a 'roots' entry under [library] in muestra.toml.",
            )
        for d in dirs:
            candidate = d / rest
            if candidate.exists():
                _check_case(d, rest, uri)
                return candidate
        if must_exist:
            raise MuestraError.one(
                "file_not_found",
                f"{uri!r} was not found in any configured library root.",
                value=uri,
                hint="Check the URI, or add the file to a library root: "
                + ", ".join(str(d) for d in dirs),
            )
        return dirs[0] / rest


def _first_case_mismatch(root: Path, rel: str) -> tuple[str, str] | None:
    """First path component whose case differs from the directory listing.

    Returns ``(written, actual)`` or None. Used so a project authored on
    a case-insensitive filesystem still validates on a case-sensitive
    one — the written name must match the listing exactly.
    """
    cur = root
    for seg in rel.replace("\\", "/").split("/"):
        if not cur.is_dir():
            return None
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        if seg in names:
            cur = cur / seg
            continue
        lowered = {n.lower(): n for n in names}
        if seg.lower() in lowered:
            return seg, lowered[seg.lower()]
        return None
    return None


def _check_case(root: Path, rel: str, ref: str) -> None:
    mismatch = _first_case_mismatch(root, rel)
    if mismatch is not None:
        written, actual = mismatch
        raise MuestraError.one(
            "case_mismatch",
            f"Path {ref!r}: component {written!r} does not match {actual!r} on disk.",
            value=ref,
            hint="Match the exact case of each path component; this keeps "
            "the project portable to case-sensitive filesystems.",
        )


def _require_exists(root: Path, ref: str, p: Path) -> None:
    if not p.exists():
        raise MuestraError.one(
            "file_not_found",
            f"File {ref!r} does not exist in the project.",
            value=ref,
            hint=f"Expected it at {p}. Check the path or import the file.",
        )
    _check_case(root, ref.replace("\\", "/"), ref)


def create_project(path: Path, config: ProjectConfig | None = None) -> Project:
    """Create the 7.1 folder structure at ``path`` (must not exist or be empty),
    then ``git init`` it — a Muestra project is its own git repo."""
    path = path.resolve()
    if path.exists() and any(path.iterdir()):
        raise MuestraError.one(
            "already_exists",
            f"Directory {path} is not empty.",
            hint="Pick a new name, or open the existing project with project.open.",
        )
    path.mkdir(parents=True, exist_ok=True)
    for d in PROJECT_DIRS:
        (path / d).mkdir(parents=True, exist_ok=True)

    cfg = config or ProjectConfig(library=LibraryConfig(roots=["library"]))
    project = Project(root=path, config=cfg)
    save_config(project, cfg)

    song = Song.model_validate(
        {
            "schema_version": 0,
            "meta": {
                "title": path.name,
                "bpm": 120,
                "key": "Am",
                "length_bars": 16,
                "seed": 0,
                "time_signature": "4/4",
            },
            "sources": [],
            "chops": [],
            "tracks": [],
            "automation": [],
            "master": {"fx": [], "gain_db": 0.0},
        }
    )
    (path / SONG_FILE).write_text(
        yaml.safe_dump(song.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    (path / FEEDBACK_FILE).write_text("", encoding="utf-8")
    (path / "docs" / "decisions.md").write_text(_DECISIONS_MD, encoding="utf-8")
    (path / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")
    init_repo(path)
    return project


def open_project(path: str | Path) -> Project:
    """Open an existing project directory."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise MuestraError.one(
            "file_not_found",
            f"Project directory {root} does not exist.",
            hint="Check the path, or create the project with project.create.",
        )
    config = load_config(root)
    return Project(root=root, config=config)


# ---------------------------------------------------------------------------
# id allocation
# ---------------------------------------------------------------------------


def allocate_id(prefix: str, taken: set[str], *, width: int = 0) -> str:
    """First free id: prefix+n zero-padded to ``width`` (0 = no padding).

    ``allocate_id('src', {...})`` -> 'src1'; ``width=3`` -> 'src001'.
    Used for source ids only — version and candidate-set ids follow the
    max+1 rule (ids are never reused; gaps are never filled).
    """
    n = 1
    while f"{prefix}{n:0{width}d}" in taken:
        n += 1
    return f"{prefix}{n:0{width}d}"


def next_source_id(song: Song) -> str:
    """Next source id: 'src1', 'src2', ..."""
    return allocate_id("src", {s.id for s in song.sources})


def next_version_id(project: Project) -> str:
    """Next render version id: max(vNNN) + 1 across git tags, renders/
    and feedback.jsonl — never reused. Delegates to versions.core."""
    return _next_version_id(project)


def next_set_id(project: Project, source_id: str) -> str:
    """Next chop-candidate set id: 'cs001', ... as max(csNNN) + 1 over
    cache/chops/<source_id>/ — ids are never reused, gaps never filled."""
    d = project.cache_dir / "chops" / source_id
    nums = [
        int(m.group(1))
        for p in (d.glob("*.json") if d.is_dir() else [])
        if (m := re.match(r"cs(\d+)$", p.stem))
    ]
    return f"cs{(max(nums) + 1) if nums else 1:03d}"
