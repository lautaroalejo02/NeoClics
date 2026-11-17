"""Merged version records: git + ``renders/vNNN/`` + ``feedback.jsonl`` (WP5).

One coherent :class:`VersionInfo` per version for the agent and the UI.
Sources, in order of trust:

* **git** (tag, commit date, trailers) — survives deleting ``renders/``;
* **meta.json** — the render's own record (preferred for the timestamp and
  text when present);
* **feedback.jsonl** — ratings, comments and A/B results.

Versions are the union of ``vNNN`` tags and ``renders/vNNN`` folders. A
version whose renders were deleted is still listed and marked
``rerenderable``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..errors import MuestraError
from ..schemas.render_meta import RenderMeta
from .core import (
    CommitInfo,
    commit_infos,
    require_repo_readable,
    snapshot_bytes,
    suggest_parent,
    version_tags,
)
from .models import VersionFeedback, VersionInfo

if TYPE_CHECKING:
    from ..project.core import Project

__all__ = ["build_record", "build_records", "load_meta", "stems_on_disk"]

_VERSION_DIR_RE = re.compile(r"^v\d{3,}$")


def load_meta(project: Project, version: str) -> tuple[RenderMeta | None, str | None]:
    """``(meta, problem)``: meta.json parsed, or why it couldn't be."""
    path = project.renders_dir / version / "meta.json"
    if not path.is_file():
        return None, None
    try:
        return RenderMeta.model_validate(json.loads(path.read_text(encoding="utf-8"))), None
    except (OSError, ValueError, ValidationError) as e:
        return None, f"renders/{version}/meta.json is unreadable ({type(e).__name__}); ignoring it"


def stems_on_disk(project: Project, version: str) -> list[str]:
    """Project-relative paths of the stem wavs present for a version."""
    d = project.renders_dir / version / "stems"
    if not d.is_dir():
        return []
    return sorted(p.relative_to(project.root).as_posix() for p in d.glob("*.wav"))


def _feedback(project: Project) -> dict[str, VersionFeedback]:
    """Per-version feedback summary. Never raises: feedback is optional."""
    from ..feedback.store import read_events

    try:
        events = read_events(project).events
    except (MuestraError, OSError):
        return {}
    acc: dict[str, dict] = {}

    def slot(v: str) -> dict:
        return acc.setdefault(v, {"rating": None, "at": None, "c": 0, "w": 0, "l": 0})

    for e in events:
        kind = getattr(e, "type", None)
        if kind == "rating":
            s = slot(e.version)
            if s["at"] is None or e.ts >= s["at"]:
                s["rating"], s["at"] = e.value, e.ts
        elif kind == "comment" and e.version:
            slot(e.version)["c"] += 1
        elif kind == "ab_choice":
            slot(e.winner)["w"] += 1
            slot(e.b if e.winner == e.a else e.a)["l"] += 1
    return {
        v: VersionFeedback(
            rating=s["rating"], rated_at=s["at"], comments=s["c"], ab_wins=s["w"], ab_losses=s["l"]
        )
        for v, s in acc.items()
    }


def _parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def _render_dirs(project: Project) -> set[str]:
    d = project.renders_dir
    if not d.is_dir():
        return set()
    return {p.name for p in d.iterdir() if p.is_dir() and _VERSION_DIR_RE.match(p.name)}


def _info(
    project: Project,
    version: str,
    commit: CommitInfo | None,
    sha: str | None,
    fb: dict[str, VersionFeedback],
) -> VersionInfo:
    warnings: list[str] = []
    meta, problem = load_meta(project, version)
    if problem:
        warnings.append(problem)
    rdir = project.renders_dir / version
    rendered = (rdir / "mix.wav").is_file()
    in_git = sha is not None and commit is not None
    if sha is not None and commit is None:
        warnings.append(f"tag {version} points to a commit that cannot be read")
    if (
        meta is not None
        and commit is not None
        and meta.git_commit
        and meta.git_commit != commit.sha
    ):
        if not commit.sha.startswith(meta.git_commit):
            warnings.append(
                f"meta.json says commit {meta.git_commit[:10]} but tag {version} is at "
                f"{commit.sha[:10]} (history edited by hand?)"
            )
    rerenderable = False
    if not rendered and in_git:
        try:
            snapshot_bytes(project, version)
            rerenderable = True
        except MuestraError:
            rerenderable = False
    if not in_git and not (rdir / "song.yaml").is_file():
        warnings.append(f"{version} has neither a git tag nor a song.yaml snapshot")

    parent = (meta.parent if meta else None) or (commit.parent if commit else None)
    created = (meta.created_at if meta else None) or _parse_dt(
        commit.committed_at if commit else None
    )
    feedback = fb.get(version, VersionFeedback())
    return VersionInfo(
        version=version,
        parent=parent,
        created_at=created,
        request=(meta.request if meta else None) or (commit.request if commit else None),
        summary=(meta.summary if meta else None) or (commit.summary if commit else None),
        agent=(meta.agent if meta else None) or (commit.agent if commit else None),
        git_commit=(commit.sha if commit else None) or (meta.git_commit if meta else None),
        liked=None if feedback.rating is None else feedback.rating == "like",
        feedback=feedback,
        rendered=rendered,
        rerenderable=rerenderable,
        has_meta=meta is not None,
        has_metrics=(rdir / "metrics.json").is_file(),
        has_stems=bool(stems_on_disk(project, version)),
        warnings=warnings,
    )


def build_records(project: Project) -> tuple[list[VersionInfo], list[str], str | None]:
    """``(records newest first, roots oldest first, base)`` for every version."""
    root = project.root
    require_repo_readable(root)
    tags = version_tags(root)
    commits = commit_infos(root, list(tags.values()))
    names = sorted(set(tags) | _render_dirs(project))
    fb = _feedback(project)
    infos = {v: _info(project, v, commits.get(tags.get(v, "")), tags.get(v), fb) for v in names}
    base = suggest_parent(project)
    for v, info in infos.items():
        info.is_base = v == base
        if info.parent == v:
            info.parent = None
        elif info.parent is not None and info.parent not in infos:
            info.warnings.append(f"parent {info.parent} is not a known version")
    for v in names:  # oldest first, so children come out ordered
        p = infos[v].parent
        if p is not None and p in infos:
            infos[p].children.append(v)
    roots = [v for v in names if infos[v].parent is None or infos[v].parent not in infos]
    return [infos[v] for v in reversed(names)], roots, base


def build_record(project: Project, version: str) -> VersionInfo:
    """The merged record of one version (``unknown_version`` if it doesn't exist)."""
    records, _, _ = build_records(project)
    for r in records:
        if r.version == version:
            return r
    known = ", ".join(r.version for r in reversed(records)) or "(none yet)"
    raise MuestraError.one(
        "unknown_version",
        f"Version {version!r} does not exist in this project.",
        value=version,
        hint=f"Known versions: {known}. List them with versions.list.",
    )
