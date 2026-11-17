"""Tools owned by WP0: project.create / project.open / song.get / song.validate."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_core import PydanticCustomError

from ..errors import Issue
from ..schemas._base import ContractModel
from ..schemas.config import ProjectConfig
from ..schemas.song import Song
from ..toolbase import ProjectScoped, ToolOutput
from .core import create_project, open_project
from .validate import load_song, validate_song

__all__ = [
    "ProjectCreateParams",
    "ProjectCreateResult",
    "ProjectOpenParams",
    "ProjectOpenResult",
    "SongGetParams",
    "SongGetResult",
    "SongValidateParams",
    "SongValidateResult",
    "project_create",
    "project_open",
    "song_get",
    "song_validate",
]


class ProjectCreateParams(ContractModel):
    name: str = Field(min_length=1, description="Name of the project directory to create.")
    dir: str = Field(
        default=".",
        description="Parent directory for the new project (default: cwd).",
    )

    @field_validator("name")
    @classmethod
    def _name_is_a_plain_dir(cls, v: str) -> str:
        if v != v.strip() or any(c in v for c in ("/", "\\", ":")) or v in (".", ".."):
            raise PydanticCustomError(
                "invalid_name",
                "Invalid project name {v!r}.",
                {"v": v, "hint": "Use a plain directory name (no path separators)."},
            )
        return v


class ProjectCreateResult(ToolOutput):
    project_path: str
    created: list[str] = Field(
        default_factory=list,
        description="Top-level files/dirs created (project-relative).",
    )


def project_create(p: ProjectCreateParams) -> ProjectCreateResult:
    """Create a new muestra project directory with the 7.1 structure."""
    target = Path(p.dir).expanduser() / p.name
    project = create_project(target)
    created = sorted(x.name for x in project.root.iterdir())
    return ProjectCreateResult(
        project_path=str(project.root),
        created=created,
        files=["muestra.toml", "song.yaml", "feedback.jsonl"],
    )


class ProjectOpenParams(ContractModel):
    path: str = Field(min_length=1, description="Path to the project directory.")


class ProjectOpenResult(ToolOutput):
    project_path: str
    config: ProjectConfig
    sources: list[str] = Field(default_factory=list, description="Source ids from song.yaml.")
    versions: list[str] = Field(default_factory=list, description="Existing renders/vNNN dirs.")
    has_song: bool


def project_open(p: ProjectOpenParams) -> ProjectOpenResult:
    """Open an existing project: loads muestra.toml and lists state."""
    project = open_project(p.path)
    versions = (
        sorted(x.name for x in project.renders_dir.iterdir() if x.is_dir())
        if project.renders_dir.is_dir()
        else []
    )
    sources: list[str] = []
    try:
        sources = [s.id for s in load_song(project).sources]
    except Exception:  # noqa: BLE001 - song may be absent/invalid; open still reports state
        pass
    return ProjectOpenResult(
        project_path=str(project.root),
        config=project.config,
        sources=sources,
        versions=versions,
        has_song=project.song_path.is_file(),
    )


class SongGetParams(ProjectScoped):
    pass


class SongGetResult(ToolOutput):
    song: Song
    path: str = "song.yaml"


def song_get(p: SongGetParams) -> SongGetResult:
    """Read and parse the project's song.yaml."""
    project = open_project(p.project)
    return SongGetResult(song=load_song(project))


class SongValidateParams(ProjectScoped):
    check_files: bool = Field(
        default=True,
        description="Also verify referenced files exist and chops fit the "
        "analyzed grid. Set false to validate a song without its audio.",
    )


class SongValidateResult(ToolOutput):
    valid: bool
    issues: list[Issue] = Field(default_factory=list)


def song_validate(p: SongValidateParams) -> SongValidateResult:
    """Validate song.yaml: structure, references, ids, automation targets,
    clip/pattern lengths, and (with check_files) file existence plus
    chop-grid consistency against cache/analysis/."""
    project = open_project(p.project)
    issues = validate_song(project, check_files=p.check_files)
    return SongValidateResult(valid=not issues, issues=issues)
