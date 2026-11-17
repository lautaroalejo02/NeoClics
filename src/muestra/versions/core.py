"""Git-backed versioning seam (WP5 owns the full implementation).

Rulings (see docs/decisions.md, WP0.1):

* A Muestra project folder is its own git repo; ``project.create`` runs
  ``git init`` here. ``renders/`` and ``cache/`` are entirely
  git-ignored (audio, meta.json and metrics.json are derived data);
  what git versions is ``song.yaml``, ``midi/``, ``muestra.toml``,
  ``feedback.jsonl`` and ``docs/``.
* One version = one git commit of the project + an annotated git tag
  ``vNNN``. The commit message carries the metadata so it survives
  deleting ``renders/``: subject ``vNNN: <summary>``, then trailers
  ``Muestra-Request:``, ``Muestra-Agent:``, ``Muestra-Parent:``.
  ``meta.git_commit`` is that commit (it contains that version's
  song.yaml); ``meta.json``/``metrics.json`` are written after the
  commit and can always be regenerated.
* ``renders/vNNN/song.yaml`` (a byte copy written by the renderer) is
  the source of truth for ears, diff and restore when present;
  ``git show vNNN:song.yaml`` is the fallback.
* Version ids are never reused: the next id is the highest ``vNNN``
  among git tags, ``renders/`` folders and versions mentioned in
  ``feedback.jsonl``, plus 1. Gaps are never filled.

Any commit Muestra makes passes an explicit fallback identity
``Muestra <muestra@localhost>`` — but only when no git user identity is
configured.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import MuestraError

if TYPE_CHECKING:
    from ..project.core import Project
    from ..schemas.song import Song

__all__ = [
    "GIT_IDENTITY",
    "commit_all",
    "init_repo",
    "next_version_id",
    "record_version",
    "song_at",
    "song_at_raw",
    "tag_version",
]

GIT_IDENTITY = ("Muestra", "muestra@localhost")

_VERSION_DIR_RE = re.compile(r"^v(\d+)$")
_VERSION_ANY_RE = re.compile(r"v\d{3,}")


# ---------------------------------------------------------------------------
# git plumbing (real; shared by project.create and WP5)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``git -C repo <args>``; MuestraError 'git_error' on failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise MuestraError.one(
            "git_error",
            "The 'git' executable was not found on PATH.",
            hint="Install git; a Muestra project is a git repo.",
        ) from e
    if check and proc.returncode != 0:
        raise MuestraError.one(
            "git_error",
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}",
            hint="Check the repository state inside the project folder.",
        )
    return proc


def _identity_args(repo: Path) -> list[str]:
    """``-c user.name=...``/``-c user.email=...`` for entries NOT configured."""
    args: list[str] = []
    if _git(repo, "config", "--get", "user.name", check=False).returncode != 0:
        args += ["-c", f"user.name={GIT_IDENTITY[0]}"]
    if _git(repo, "config", "--get", "user.email", check=False).returncode != 0:
        args += ["-c", f"user.email={GIT_IDENTITY[1]}"]
    return args


def init_repo(path: Path) -> None:
    """``git init`` + initial commit of the scaffold.

    Any Muestra-made commit uses the fallback identity only when no git
    user identity is configured.
    """
    if shutil.which("git") is None:
        raise MuestraError.one(
            "git_error",
            "Cannot create a Muestra project: 'git' is not on PATH.",
            hint="Install git; a Muestra project is a git repo.",
        )
    _git(path, "init")
    _git(path, "add", "-A")
    _git(
        path,
        *_identity_args(path),
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "Initial muestra project",
    )


def commit_all(repo: Path, message: str, *, trailers: dict[str, str] | None = None) -> str:
    """Stage every tracked/untracked change, commit, return the sha.

    ``message`` is the subject (``vNNN: <summary>``); trailers are
    appended as ``Key: value`` lines (e.g. ``Muestra-Request``).
    """
    _git(repo, "add", "-A")
    body = message
    if trailers:
        body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items() if v)
    _git(repo, *_identity_args(repo), "commit", "--quiet", "-m", body)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def tag_version(repo: Path, version: str, message: str | None = None) -> None:
    """Create the annotated tag ``vNNN`` on HEAD."""
    _git(repo, "tag", "-a", version, "-m", message or version)


# ---------------------------------------------------------------------------
# version-id allocation (real — ids are never reused)
# ---------------------------------------------------------------------------


def _version_numbers(project: Project) -> set[int]:
    """Every ``vNNN`` already in use: git tags, renders/ dirs, feedback."""
    nums: set[int] = set()
    # git tags (tolerate: not a repo / git missing)
    try:
        proc = _git(project.root, "tag", "--list", "v*", check=False)
        if proc.returncode == 0:
            for tag in proc.stdout.split():
                m = _VERSION_DIR_RE.match(tag)
                if m:
                    nums.add(int(m.group(1)))
    except MuestraError:
        pass
    # renders/ folders
    if project.renders_dir.is_dir():
        for p in project.renders_dir.iterdir():
            m = _VERSION_DIR_RE.match(p.name)
            if m and p.is_dir():
                nums.add(int(m.group(1)))
    # versions mentioned in feedback.jsonl (ratings, comments, A/B, ...)
    if project.feedback_path.is_file():
        text = project.feedback_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                raw = line
            found = _VERSION_ANY_RE.findall(json.dumps(raw) if not isinstance(raw, str) else raw)
            for tok in found:
                nums.add(int(tok[1:]))
    return nums


def next_version_id(project: Project) -> str:
    """Next render version id: max(vNNN) + 1, never reusing a gap."""
    nums = _version_numbers(project)
    return f"v{(max(nums) + 1) if nums else 1:03d}"


# ---------------------------------------------------------------------------
# WP5 seams (typed stubs — do not guess)
# ---------------------------------------------------------------------------


def record_version(
    project: Project,
    version: str,
    request: str | None,
    summary: str | None,
    agent: str | None,
    parent: str | None,
) -> str:
    """Commit the project and create the annotated tag ``version``.

    Commit message: subject ``vNNN: <summary>``, trailers
    ``Muestra-Request:``, ``Muestra-Agent:``, ``Muestra-Parent:``.
    Returns the new commit sha (to be stored in ``meta.git_commit``).
    Called by the ``render`` tool BEFORE meta.json/metrics.json are
    written (those live in git-ignored renders/ and are derived data).
    """
    raise NotImplementedError("WP5")


def song_at_raw(project: Project, version: str) -> str:
    """Raw song.yaml text at ``version``.

    Reads ``renders/<version>/song.yaml`` when present (byte copy made
    by the renderer); falls back to ``git show <version>:song.yaml``.
    """
    raise NotImplementedError("WP5")


def song_at(project: Project, version: str) -> Song:
    """The parsed :class:`Song` at ``version`` (see :func:`song_at_raw`)."""
    raise NotImplementedError("WP5")
