"""Git-backed versioning (WP5). Full design notes: ``docs/versions.md``.

Contract (docs/contracts.md, R1/R2):

* A Muestra project folder is its own git repo; ``project.create`` runs
  :func:`init_repo`. ``renders/`` and ``cache/`` are git-ignored (derived
  data). Version commits carry ``song.yaml``, ``midi/``, ``muestra.toml``,
  ``feedback.jsonl`` and ``docs/`` (:data:`VERSIONED_PATHS`) and nothing else:
  imported audio under ``sources/`` is never committed by Muestra.
* One version = one commit + one annotated tag ``vNNN``. The commit subject
  is ``vNNN: <summary>``; metadata lives in trailers (``Muestra-Version``,
  ``Muestra-Request``, ``Muestra-Agent``, ``Muestra-Parent``, plus
  ``Muestra-Summary`` when the subject cannot hold the summary verbatim) so
  it survives deleting ``renders/``.
* ``renders/vNNN/song.yaml`` (byte copy) is the preferred snapshot;
  ``git show vNNN:song.yaml`` is the fallback.
* Version ids are never reused (max over tags, ``renders/`` dirs, version
  commits in history and ``feedback.jsonl``, plus one).

Safety rules enforced here:

* every git call is an argument list (never a shell string); commit/tag
  messages travel on stdin, so no text can be read as an option;
* every git call pins ``core.autocrlf=false`` so ``song.yaml`` bytes (CRLF
  on Windows) are stored and restored verbatim whatever the user's config;
* repo-redirecting environment variables (``GIT_DIR`` ...) are dropped, and
  versioning refuses to run unless the project folder is the TOP LEVEL of
  its own repo (never commit into a parent repo);
* version commits are built with a private temporary index (plumbing), so
  the user's own index/staging area is never touched, hooks and signing
  never prompt, and the branch moves with a compare-and-swap.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError as RuamelError

from ..errors import MuestraError, issues_from_validation_error

if TYPE_CHECKING:
    from ..project.core import Project
    from ..schemas.song import Song

__all__ = [
    "GIT_IDENTITY",
    "VERSIONED_PATHS",
    "CommitInfo",
    "RestoreOutcome",
    "build_commit_message",
    "commit_all",
    "commit_info",
    "commit_infos",
    "git_toplevel",
    "init_repo",
    "is_repo_locked",
    "known_versions",
    "next_version_id",
    "parse_commit_message",
    "record_version",
    "require_own_repo",
    "require_repo_readable",
    "restore_files",
    "snapshot_bytes",
    "song_at",
    "song_at_raw",
    "suggest_parent",
    "tag_version",
    "version_exists",
    "version_tags",
]

GIT_IDENTITY = ("Muestra", "muestra@localhost")

#: song.yaml filename (kept local: importing it from muestra.project.core
#: would be a circular import, project.core imports this module).
SONG_FILE = "song.yaml"

#: what a version commit versions (contract R1). Anything else in the
#: project folder (sources/ audio, library/, renders/, cache/) is not
#: committed by Muestra.
VERSIONED_PATHS = (
    SONG_FILE,
    "midi",
    "muestra.toml",
    "ears.toml",  # optional ears thresholds/reference (WP6): part of the version
    "feedback.jsonl",
    "docs",
    ".gitignore",
    ".gitattributes",
)

_GITATTRIBUTES = "# muestra: store every file byte-for-byte (no CRLF conversion)\n* -text\n"

_VERSION_ID_RE = re.compile(r"^v(\d{3,})$")
_VERSION_IN_TEXT_RE = re.compile(r"(?<![A-Za-z0-9])v(\d{3,})(?![A-Za-z0-9])")
_TRAILER_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*): ?(.*)$")
_SUBJECT_VERSION_RE = re.compile(r"^(v\d{3,})(?:: ?(.*))?$")

#: environment variables that would redirect git to some other repository.
_REPO_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_NAMESPACE",
    "GIT_QUARANTINE_PATH",
)

#: config pinned on EVERY git call (see module docstring).
_PINNED_CONFIG = (
    "core.autocrlf=false",
    "core.safecrlf=false",
    "core.quotepath=false",
    "i18n.logOutputEncoding=UTF-8",
)

_SUBJECT_MAX = 200

_YAML = YAML(typ="rt")
_YAML.preserve_quotes = True


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------


def _git_missing() -> MuestraError:
    return MuestraError.one(
        "git_not_installed",
        "The 'git' executable was not found on PATH.",
        hint="Install git (https://git-scm.com) and make sure 'git' is on PATH; "
        "a Muestra project is a git repo and versions are git commits.",
    )


def _git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _REPO_ENV_VARS}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["LC_ALL"] = "C"
    if extra:
        env.update(extra)
    return env


def _git(
    root: Path,
    *args: str,
    input: bytes | None = None,  # noqa: A002 - mirrors subprocess
    env: dict[str, str] | None = None,
    config: tuple[str, ...] = (),
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run ``git -C root <args>``; raw bytes in and out.

    ``config`` entries become extra ``-c key=value`` options for this call
    only. Failures raise :class:`MuestraError` with a mapped code unless
    ``check=False``.
    """
    exe = shutil.which("git")
    if exe is None:
        raise _git_missing()
    cmd = [exe, "-C", str(root)]
    for c in (*_PINNED_CONFIG, *config):
        cmd += ["-c", c]
    cmd += list(args)
    try:
        proc = subprocess.run(
            cmd,
            input=input,
            capture_output=True,
            env=_git_env(env),
            check=False,
        )
    except FileNotFoundError as e:
        raise _git_missing() from e
    if check and proc.returncode != 0:
        raise _git_failure(args, proc)
    return proc


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _git_out(root: Path, *args: str, **kw) -> str:
    """Like :func:`_git` but returns stripped stdout text."""
    return _text(_git(root, *args, **kw).stdout).strip()


def _git_failure(args: tuple[str, ...], proc: subprocess.CompletedProcess[bytes]) -> MuestraError:
    err = _text(proc.stderr).strip() or _text(proc.stdout).strip()
    low = err.lower()
    cmd = "git " + " ".join(args[:3])
    if "dubious ownership" in low or "safe.directory" in low:
        return MuestraError.one(
            "git_unsafe_repo",
            f"git refuses to use this repository (dubious ownership): {err}",
            hint="Run 'git config --global --add safe.directory <project path>' once, then retry.",
        )
    if "index.lock" in low or ("unable to create" in low and ".lock" in low):
        return MuestraError.one(
            "repo_locked",
            f"The git index is locked: {err}",
            hint="Another git process is running or one crashed. Wait for it, or "
            "delete .git/index.lock if no git process is alive, then retry.",
        )
    if "not a git repository" in low:
        return MuestraError.one(
            "not_git_repo",
            f"Not a git repository: {err}",
            hint="A Muestra project is its own git repo; run 'git init' inside the "
            "project folder (or re-create it with project.create).",
        )
    return MuestraError.one(
        "git_error",
        f"{cmd} ... failed (exit {proc.returncode}): {err}",
        hint="Check the repository state with 'git status' inside the project folder.",
    )


def _same_path(a: Path | str, b: Path | str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


@dataclass(frozen=True)
class _Repo:
    """Where a project's repo lives (one ``rev-parse`` call)."""

    top: Path
    git_dir: Path
    branch: str | None = None  # refs/heads/<name>; None when detached / not asked


#: rev-parse answers per project root, keyed on the .git entry's identity: one
#: versions.list used to spawn ~20 identical `git rev-parse` processes (WP12).
_REPO_CACHE: dict[tuple[str, int, int], _Repo | None] = {}


def _repo_info(root: Path) -> _Repo | None:
    try:
        st = (Path(root) / ".git").stat()
        key: tuple[str, int, int] | None = (
            os.path.normcase(os.path.realpath(root)),
            st.st_mtime_ns,
            st.st_ino,
        )
    except OSError:
        key = None  # no .git here: ask git every time (it may be a parent's repo)
    if key is not None and key in _REPO_CACHE:
        return _REPO_CACHE[key]
    proc = _git(root, "rev-parse", "--show-toplevel", "--absolute-git-dir", check=False)
    if proc.returncode != 0:
        return None
    lines = _text(proc.stdout).splitlines()
    info = _Repo(Path(lines[0]), Path(lines[1])) if len(lines) >= 2 else None
    if key is not None and info is not None:
        if len(_REPO_CACHE) > 64:
            _REPO_CACHE.clear()
        _REPO_CACHE[key] = info
    return info


def git_toplevel(root: Path) -> Path | None:
    """The top level of the repo containing ``root``; None if not in a repo
    (or git is missing)."""
    if shutil.which("git") is None:
        return None
    info = _repo_info(root)
    return info.top if info else None


def _git_dir(root: Path) -> Path:
    return Path(_git_out(root, "rev-parse", "--absolute-git-dir"))


def is_repo_locked(root: Path) -> bool:
    """True when ``.git/index.lock`` exists (another git is running)."""
    return (_git_dir(root) / "index.lock").exists()


_BUSY_MARKERS = (
    ("rebase-merge", "a rebase"),
    ("rebase-apply", "a rebase/am"),
    ("MERGE_HEAD", "a merge"),
    ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ("REVERT_HEAD", "a revert"),
)


def require_repo_readable(root: Path) -> _Repo:
    """Guard reads: git installed and the project folder is the top level of
    its own repo (never a parent's)."""
    if shutil.which("git") is None:
        raise _git_missing()
    info = _repo_info(root)
    if info is None:
        raise MuestraError.one(
            "not_git_repo",
            f"{root} is not a git repository.",
            hint="A Muestra project is its own git repo. Run 'git init' inside the "
            "project folder (project.create does this for new projects).",
        )
    if not _same_path(info.top, root):
        raise MuestraError.one(
            "not_git_repo",
            f"{root} is inside the repository {info.top}, not at its top level.",
            hint="Versioning only runs when the project folder is the top level of its "
            "OWN git repo; Muestra will not commit into a parent repository. Run "
            "'git init' inside the project folder.",
        )
    return info


def require_own_repo(root: Path, *, need_branch: bool = True) -> _Repo:
    """Guard every write: the project folder must be the top level of its
    own healthy git repo.

    Refuses (structured errors with fix hints): git not installed, folder
    not a repo or only *inside* another repo, locked index, a rebase/merge/
    cherry-pick in progress, and (``need_branch``) a detached HEAD.
    Returns the repo info (including the branch ref).
    """
    info = require_repo_readable(root)
    gd = info.git_dir
    if (gd / "index.lock").exists():
        raise MuestraError.one(
            "repo_locked",
            f"The git index of {root} is locked (index.lock exists).",
            hint="Another git process is running or one crashed. Wait for it, or "
            "delete .git/index.lock if no git process is alive, then retry.",
        )
    for marker, what in _BUSY_MARKERS:
        if (gd / marker).exists():
            raise MuestraError.one(
                "repo_busy",
                f"The project repo is in the middle of {what}.",
                hint="Finish or abort it first (git rebase --abort / git merge --abort / "
                "git cherry-pick --abort), then retry.",
            )
    branch = _branch_ref(root)
    if need_branch and branch is None:
        raise MuestraError.one(
            "detached_head",
            "The project repo is on a detached HEAD.",
            hint="Check out a branch first (e.g. 'git switch main' or 'git switch -c work'); "
            "commits made on a detached HEAD are orphaned by the next checkout.",
        )
    return _Repo(info.top, info.git_dir, branch)


def _branch_ref(root: Path) -> str | None:
    """``refs/heads/<branch>`` HEAD points at, or None when detached."""
    proc = _git(root, "symbolic-ref", "-q", "HEAD", check=False)
    return _text(proc.stdout).strip() if proc.returncode == 0 else None


def _head_sha(root: Path) -> str | None:
    proc = _git(root, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False)
    return _text(proc.stdout).strip() if proc.returncode == 0 else None


def _identity_config(root: Path) -> tuple[str, ...]:
    """``user.name``/``user.email`` fallbacks, ONLY for what isn't configured
    (config or env); never mutates the repo's config."""
    have: set[str] = set()
    if os.environ.get("GIT_AUTHOR_NAME") and os.environ.get("GIT_COMMITTER_NAME"):
        have.add("user.name")
    if os.environ.get("GIT_AUTHOR_EMAIL") and os.environ.get("GIT_COMMITTER_EMAIL"):
        have.add("user.email")
    if have != {"user.name", "user.email"}:
        proc = _git(root, "config", "--get-regexp", r"^user\.(name|email)$", check=False)
        for line in _text(proc.stdout).splitlines():
            key, _, value = line.partition(" ")
            if value.strip():
                have.add(key.lower())
    cfg: list[str] = []
    if "user.name" not in have:
        cfg.append(f"user.name={GIT_IDENTITY[0]}")
    if "user.email" not in have:
        cfg.append(f"user.email={GIT_IDENTITY[1]}")
    return tuple(cfg)


def _signing_off() -> tuple[str, ...]:
    return ("commit.gpgsign=false", "tag.gpgSign=false", "tag.forceSignAnnotated=false")


# ---------------------------------------------------------------------------
# commit messages (subject + trailers)
# ---------------------------------------------------------------------------


def _one_line(text: str) -> str:
    """Collapse all whitespace/newlines to single spaces."""
    return " ".join(text.split())


def _escape_trailer(value: str) -> str:
    """One-line, reversible encoding of free text for a trailer value.

    ``\\`` -> ``\\\\``, newline -> ``\\n``, CR -> ``\\r``; NUL and other
    control characters (except tab) are dropped. Quotes, emoji, accents and
    leading dashes need no escaping (the message never touches a shell or
    argv). Leading/trailing whitespace is trimmed.
    """
    cleaned = "".join(c for c in value if c in "\n\r\t" or ord(c) >= 0x20)
    return cleaned.strip().replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def _unescape_trailer(value: str) -> str:
    def sub(m: re.Match[str]) -> str:
        return {"n": "\n", "r": "\r", "\\": "\\"}.get(m.group(1), m.group(0))

    return re.sub(r"\\(.)", sub, value)


def build_commit_message(
    version: str,
    *,
    summary: str | None = None,
    request: str | None = None,
    agent: str | None = None,
    parent: str | None = None,
) -> str:
    """The commit message of a version: subject ``vNNN: <summary>`` + trailers."""
    subject = version
    summary_trailer: str | None = None
    if summary and summary.strip():
        flat = _one_line("".join(c for c in summary if c in "\n\r\t" or ord(c) >= 0x20))
        if len(flat) > _SUBJECT_MAX:
            flat = flat[: _SUBJECT_MAX - 1].rstrip() + "…"
        subject = f"{version}: {flat}"
        if flat != summary.strip():
            summary_trailer = _escape_trailer(summary)
    trailers = [("Muestra-Version", version)]
    if request and request.strip():
        trailers.append(("Muestra-Request", _escape_trailer(request)))
    if summary_trailer:
        trailers.append(("Muestra-Summary", summary_trailer))
    if agent and agent.strip():
        trailers.append(("Muestra-Agent", _escape_trailer(agent)))
    if parent:
        trailers.append(("Muestra-Parent", parent))
    return subject + "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers) + "\n"


def parse_commit_message(body: str) -> tuple[str, dict[str, str]]:
    """``(subject, trailers)`` of a commit message; trailer values unescaped.

    Trailers are the final paragraph when every line in it is ``Key: value``.
    """
    text = body.replace("\r\n", "\n").strip("\n")
    paras = text.split("\n\n")
    subject = paras[0].split("\n", 1)[0].strip()
    trailers: dict[str, str] = {}
    if len(paras) >= 2:
        lines = [ln for ln in paras[-1].split("\n") if ln.strip()]
        matches = [_TRAILER_LINE_RE.match(ln) for ln in lines]
        if lines and all(matches):
            for m in matches:
                assert m is not None
                trailers[m.group(1)] = _unescape_trailer(m.group(2).strip())
    return subject, trailers


@dataclass(frozen=True)
class CommitInfo:
    """What git knows about one commit (a version commit, normally)."""

    sha: str
    committed_at: str  # ISO-8601 with offset (git %cI)
    subject: str
    trailers: dict[str, str] = field(default_factory=dict)

    @property
    def version(self) -> str | None:
        v = self.trailers.get("Muestra-Version")
        if v and _VERSION_ID_RE.match(v):
            return v
        m = _SUBJECT_VERSION_RE.match(self.subject)
        return m.group(1) if m and "Muestra-Version" in self.trailers else None

    @property
    def summary(self) -> str | None:
        if "Muestra-Summary" in self.trailers:
            return self.trailers["Muestra-Summary"]
        m = _SUBJECT_VERSION_RE.match(self.subject)
        if m and m.group(2):
            return m.group(2).strip() or None
        return None

    @property
    def request(self) -> str | None:
        return self.trailers.get("Muestra-Request") or None

    @property
    def agent(self) -> str | None:
        return self.trailers.get("Muestra-Agent") or None

    @property
    def parent(self) -> str | None:
        p = self.trailers.get("Muestra-Parent")
        return p if p and _VERSION_ID_RE.match(p) else None

    @property
    def restored_from(self) -> str | None:
        p = self.trailers.get("Muestra-Restored-From")
        return p if p and _VERSION_ID_RE.match(p) else None


_LOG_FMT = "%H%x1f%cI%x1f%B%x1e"


def _parse_log(raw: str) -> list[CommitInfo]:
    out: list[CommitInfo] = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        parts = rec.split("\x1f", 2)
        if len(parts) < 3:
            continue
        subject, trailers = parse_commit_message(parts[2])
        out.append(CommitInfo(parts[0].strip(), parts[1].strip(), subject, trailers))
    return out


def commit_info(root: Path, ref: str) -> CommitInfo | None:
    """Info for one commit-ish; None when it doesn't resolve."""
    proc = _git(root, "log", "-1", f"--format={_LOG_FMT}", ref, "--", check=False)
    if proc.returncode != 0:
        return None
    infos = _parse_log(_text(proc.stdout))
    return infos[0] if infos else None


def commit_infos(root: Path, shas: list[str]) -> dict[str, CommitInfo]:
    """Batch :func:`commit_info`; unresolvable shas are simply absent."""
    if not shas:
        return {}
    uniq = list(dict.fromkeys(shas))
    proc = _git(root, "log", "--no-walk=unsorted", f"--format={_LOG_FMT}", *uniq, "--", check=False)
    if proc.returncode == 0:
        return {i.sha: i for i in _parse_log(_text(proc.stdout))}
    found: dict[str, CommitInfo] = {}
    for sha in uniq:  # one bad object must not hide the others
        info = commit_info(root, sha)
        if info is not None:
            found[info.sha] = info
    return found


# ---------------------------------------------------------------------------
# tags + version ids
# ---------------------------------------------------------------------------


def version_tags(root: Path) -> dict[str, str]:
    """``{vNNN: commit sha}`` for every ``vNNN`` tag (annotated or not).
    Empty when not a repo / git missing."""
    if shutil.which("git") is None:
        return {}
    proc = _git(
        root,
        "for-each-ref",
        "--format=%(refname:strip=2)%1f%(objecttype)%1f%(objectname)%1f%(*objectname)",
        "refs/tags/v*",
        check=False,
    )
    out: dict[str, str] = {}
    if proc.returncode != 0:
        return out
    for line in _text(proc.stdout).splitlines():
        name, otype, obj, peeled = (line.split("\x1f") + ["", "", "", ""])[:4]
        if not _VERSION_ID_RE.match(name):
            continue
        sha = peeled if otype == "tag" else obj if otype == "commit" else ""
        if sha:
            out[name] = sha
    return out


def _tag_exists(root: Path, version: str) -> bool:
    proc = _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{version}", check=False)
    return proc.returncode == 0


def _history_versions(root: Path) -> set[int]:
    """Version numbers of Muestra version commits anywhere in history (survives
    deleted tags)."""
    nums: set[int] = set()
    proc = _git(
        root,
        "log",
        "--all",
        "-E",
        "--grep=^Muestra-Version: v[0-9]{3,}$",
        "--format=%B",
        check=False,
    )
    if proc.returncode != 0:
        return nums
    for line in _text(proc.stdout).splitlines():
        m = re.match(r"^Muestra-Version: (v\d{3,})$", line.strip())
        if m:
            nums.add(int(m.group(1)[1:]))
    return nums


def _feedback_versions(project: Project) -> set[int]:
    """Versions named by feedback events: only the ``version``/``a``/``b``/
    ``winner`` fields count (a comment saying 'v2026' must not bump ids)."""
    nums: set[int] = set()
    path = project.feedback_path
    if not path.is_file():
        return nums
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        for key in ("version", "a", "b", "winner"):
            val = raw.get(key)
            if isinstance(val, str) and (m := _VERSION_ID_RE.match(val)):
                nums.add(int(m.group(1)))
    return nums


def _version_numbers(project: Project) -> set[int]:
    """Every version number already used: git tags, version commits in
    history, ``renders/`` folders and versions named in feedback.jsonl."""
    nums = {int(v[1:]) for v in version_tags(project.root)}
    if shutil.which("git") is not None:
        nums |= _history_versions(project.root)
    if project.renders_dir.is_dir():
        for p in project.renders_dir.iterdir():
            m = _VERSION_ID_RE.match(p.name)
            if m and p.is_dir():
                nums.add(int(m.group(1)))
    nums |= _feedback_versions(project)
    return nums


def next_version_id(project: Project) -> str:
    """Next render version id: ``max(vNNN) + 1``; gaps are never reused."""
    nums = _version_numbers(project)
    return f"v{(max(nums) + 1) if nums else 1:03d}"


def known_versions(project: Project) -> list[str]:
    """Versions that really exist (git tag or ``renders/`` folder), oldest first."""
    nums = {int(v[1:]) for v in version_tags(project.root)}
    if project.renders_dir.is_dir():
        for p in project.renders_dir.iterdir():
            m = _VERSION_ID_RE.match(p.name)
            if m and p.is_dir():
                nums.add(int(m.group(1)))
    return [f"v{n:03d}" for n in sorted(nums)]


def version_exists(project: Project, version: str) -> bool:
    return version in known_versions(project)


def _unknown_version(project: Project, version: str, why: str | None = None) -> MuestraError:
    known = ", ".join(known_versions(project)) or "(none yet)"
    msg = f"Version {version!r} does not exist in this project."
    if why:
        msg = f"Version {version!r} is not usable: {why}"
    return MuestraError.one(
        "unknown_version",
        msg,
        value=version,
        hint=f"Known versions: {known}. List them with versions.list.",
    )


# ---------------------------------------------------------------------------
# snapshots (song.yaml / midi at a version)
# ---------------------------------------------------------------------------


def _git_song_bytes(root: Path, version: str) -> bytes | None:
    proc = _git(
        root, "cat-file", "blob", f"refs/tags/{version}^{{commit}}:{SONG_FILE}", check=False
    )
    return proc.stdout if proc.returncode == 0 else None


def snapshot_bytes(project: Project, version: str) -> tuple[bytes, str]:
    """Raw ``song.yaml`` bytes at ``version`` and where they came from.

    ``(content, "renders")`` for the byte copy ``renders/vNNN/song.yaml``
    (preferred), ``(content, "git")`` for the tag's blob. Raises
    ``unknown_version`` when neither exists.
    """
    if not _VERSION_ID_RE.match(version):
        raise MuestraError.one(
            "invalid_value",
            f"Invalid version id {version!r}; expected 'vNNN'.",
            value=version,
            hint="Use ids like 'v001' (see versions.list).",
        )
    snap = project.renders_dir / version / SONG_FILE
    if snap.is_file():
        data = snap.read_bytes()
        if data.strip():
            return data, "renders"
    require_repo_readable(project.root)
    blob = _git_song_bytes(project.root, version)
    if blob is not None:
        return blob, "git"
    if _tag_exists(project.root, version):
        raise _unknown_version(
            project, version, "its tag exists but song.yaml cannot be read from that commit."
        )
    raise _unknown_version(project, version)


def song_at_raw(project: Project, version: str) -> str:
    """Raw ``song.yaml`` text at ``version``: ``renders/<v>/song.yaml`` when
    present, else ``git show <v>:song.yaml``. Line endings are preserved."""
    data, _ = snapshot_bytes(project, version)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise MuestraError.one(
            "parse_error",
            f"song.yaml at {version} is not valid UTF-8.",
            value=version,
            hint="The snapshot is corrupted; compare renders/<v>/song.yaml with "
            "'git show <v>:song.yaml'.",
        ) from e


def _yaml_doc_text(text: str, label: str) -> CommentedMap:
    try:
        doc = _YAML.load(text)
    except DuplicateKeyError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise MuestraError.one(
            "duplicate_key",
            f"Duplicate YAML key in {label}{where}.",
            path=label,
            hint="That version's song.yaml has a duplicated key; fix it in a new version.",
        ) from e
    except RuamelError as e:
        raise MuestraError.one(
            "parse_error",
            f"Invalid YAML in {label}: {e}",
            path=label,
            hint="That version's song.yaml is not valid YAML.",
        ) from e
    if doc is None:
        doc = CommentedMap()
    if not isinstance(doc, CommentedMap):
        raise MuestraError.one(
            "invalid_type",
            f"{label} must contain a YAML mapping at the top level.",
            path=label,
            hint="song.yaml is a mapping of meta/sources/chops/tracks/...",
        )
    return doc


def song_at(project: Project, version: str) -> Song:
    """The parsed :class:`Song` at ``version`` (see :func:`song_at_raw`)."""
    from ..schemas.song import Song

    label = f"{version}:{SONG_FILE}"
    doc = _yaml_doc_text(song_at_raw(project, version), label)
    try:
        return Song.model_validate(doc)
    except ValidationError as e:
        raise MuestraError(issues_from_validation_error(e, path_prefix=label)) from e


# ---------------------------------------------------------------------------
# building commits without touching the user's index
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _temp_index(root: Path, head: str | None) -> Iterator[dict[str, str]]:
    """A private index primed with HEAD's tree; yields the env selecting it."""
    td = tempfile.mkdtemp(prefix="muestra-index-")
    try:
        env = {"GIT_INDEX_FILE": os.path.join(td, "index")}
        if head:
            _git(root, "read-tree", head, env=env)
        yield env
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _has_files(path: Path) -> bool:
    if path.is_file():
        return True
    return path.is_dir() and any(f.is_file() for f in path.rglob("*"))


def _stage_versioned(root: Path, env: dict[str, str] | None, head: str | None) -> None:
    """Stage the working-tree state of every versioned path (additions,
    edits AND deletions) into the index selected by ``env``."""
    present = [p for p in VERSIONED_PATHS if _has_files(root / p)]
    absent = [p for p in VERSIONED_PATHS if p not in present]
    if present:
        proc = _git(root, "add", "-A", "--", *present, env=env, check=False)
        if proc.returncode != 0:
            err = _text(proc.stderr).lower()
            if "ignored by one of your .gitignore" not in err:
                raise _git_failure(("add", "-A", "--", *present), proc)
            for path in present:  # an ignored path must not block the others
                one = _git(root, "add", "-A", "--", path, env=env, check=False)
                if one.returncode != 0 and "ignored by" not in _text(one.stderr).lower():
                    raise _git_failure(("add", "-A", "--", path), one)
    if absent and head:  # recorded deletions of paths that exist in HEAD
        _git(root, "rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", *absent, env=env)


def _commit_versioned(
    root: Path,
    message: str,
    *,
    allow_empty: bool,
    ref: str | None = None,
    ident: tuple[str, ...] | None = None,
) -> tuple[str | None, str | None]:
    """Commit the working-tree state of :data:`VERSIONED_PATHS` on the current
    branch, using a private index.

    Returns ``(new_sha, previous_head)``; ``new_sha`` is None when nothing
    changed and ``allow_empty`` is false. The branch is advanced with a
    compare-and-swap, so a concurrent commit makes this fail instead of
    being overwritten. ``ref``/``ident`` may be passed in to save git calls.
    """
    ref = ref or _branch_ref(root)
    if ref is None:
        raise MuestraError.one(
            "detached_head",
            "The project repo is on a detached HEAD.",
            hint="Check out a branch first (e.g. 'git switch main').",
        )
    head = _head_sha(root)
    ident = _identity_config(root) if ident is None else ident
    with _temp_index(root, head) as env:
        _stage_versioned(root, env, head)
        tree = _git_out(root, "write-tree", env=env)
        if head and not allow_empty:
            head_tree = _git_out(root, "rev-parse", f"{head}^{{tree}}")
            if head_tree == tree:
                return None, head
        args = ["commit-tree", tree, "-F", "-"]
        if head:
            args[2:2] = ["-p", head]
        sha = _git_out(root, *args, input=message.encode("utf-8"), config=ident)
    old = head or "0" * 40
    upd = _git(
        root,
        "update-ref",
        "-m",
        "muestra: version commit",
        ref,
        sha,
        old,
        config=ident,  # the reflog entry needs an identity too (user.useConfigOnly)
        check=False,
    )
    if upd.returncode != 0:
        raise MuestraError.one(
            "git_error",
            f"The branch moved while committing: {_text(upd.stderr).strip()}",
            hint="Another git process changed the branch; retry.",
        )
    # bring the real index in line with the new HEAD for our paths (no
    # worktree change), so 'git status' shows nothing staged.
    _git(root, "reset", "-q", "--", *VERSIONED_PATHS, check=False)
    return sha, head


def commit_all(
    repo: Path,
    message: str,
    *,
    trailers: dict[str, str] | None = None,
    allow_empty: bool = False,
) -> str:
    """Commit the versioned paths with ``message`` (+ ``Key: value`` trailers).

    Kept for callers of the original seam; returns the new sha, or HEAD when
    nothing changed and ``allow_empty`` is false.
    """
    body = message.rstrip("\n")
    if trailers:
        body += "\n\n" + "\n".join(f"{k}: {_escape_trailer(v)}" for k, v in trailers.items() if v)
    sha, head = _commit_versioned(repo, body + "\n", allow_empty=allow_empty)
    return sha or head or ""


def tag_version(
    repo: Path,
    version: str,
    message: str | None = None,
    commit: str = "HEAD",
    ident: tuple[str, ...] | None = None,
) -> None:
    """Create the annotated tag ``vNNN`` on ``commit`` (default HEAD)."""
    ident = _identity_config(repo) if ident is None else ident
    _git(
        repo,
        "tag",
        "-a",
        "-F",
        "-",
        version,
        commit,
        input=(message or version).encode("utf-8"),
        config=(*ident, *_signing_off()),
    )


def init_repo(path: Path) -> None:
    """``git init`` + the scaffold commit (HEAD must exist so versions can start).

    Also writes ``.gitattributes`` (``* -text``) so no later git operation,
    whatever the user's ``core.autocrlf``, rewrites line endings.
    """
    if shutil.which("git") is None:
        raise MuestraError.one(
            "git_not_installed",
            "Cannot create a Muestra project: 'git' is not on PATH.",
            hint="Install git (https://git-scm.com); a Muestra project is a git repo.",
        )
    _git(path, "init", "--quiet")
    ga = path / ".gitattributes"
    if not ga.exists():
        ga.write_bytes(_GITATTRIBUTES.encode("utf-8"))
    _stage_versioned(path, None, None)
    _git(
        path,
        "commit",
        "-q",
        "--allow-empty",
        "--no-verify",
        "-F",
        "-",
        input=b"Initial muestra project\n",
        config=(*_identity_config(path), *_signing_off()),
    )


# ---------------------------------------------------------------------------
# parent tracking
# ---------------------------------------------------------------------------


def suggest_parent(project: Project, tags: dict[str, str] | None = None) -> str | None:
    """The version the next render descends from.

    Walks HEAD's first-parent history for the most recent *version commit*
    (``Muestra-Version``) or *restore commit* (``Muestra-Restored-From``):
    after ``versions.restore v001`` that is v001, so the next render
    records ``Muestra-Parent: v001`` and history forms a tree. Falls back to
    the newest ``vNNN`` tag; None when there is no version yet.
    """
    root = project.root
    if shutil.which("git") is None:
        return None
    proc = _git(
        root, "log", "--first-parent", "--max-count=500", f"--format={_LOG_FMT}", check=False
    )
    if proc.returncode == 0:
        for info in _parse_log(_text(proc.stdout)):
            if info.restored_from:
                return info.restored_from
            if info.version:
                return info.version
    names = sorted(version_tags(root) if tags is None else tags)
    return names[-1] if names else None


# ---------------------------------------------------------------------------
# record_version
# ---------------------------------------------------------------------------


def record_version(
    project: Project,
    version: str,
    request: str | None,
    summary: str | None,
    agent: str | None,
    parent: str | None,
) -> str:
    """Commit the project's versioned files and tag the commit ``version``.

    * Subject ``vNNN: <summary>``; trailers ``Muestra-Version``,
      ``Muestra-Request``, ``Muestra-Agent``, ``Muestra-Parent``
      (+ ``Muestra-Summary`` when the one-line subject isn't verbatim).
    * Always creates a commit (``allow_empty`` semantics): re-rendering an
      unchanged song is still a distinct version.
    * ``parent=None`` means "derive it" (:func:`suggest_parent`), which
      honours ``versions.restore``; pass a version id only to override.
    * Returns the new commit sha (store it in ``meta.git_commit``).

    Called by the ``render`` tool BEFORE meta.json/metrics.json exist.
    """
    root = project.root
    if not _VERSION_ID_RE.match(version):
        raise MuestraError.one(
            "invalid_value",
            f"Invalid version id {version!r}; expected 'vNNN'.",
            value=version,
            hint="Allocate ids with next_version_id (e.g. 'v001').",
        )
    if parent is not None and not _VERSION_ID_RE.match(parent):
        raise MuestraError.one(
            "invalid_value",
            f"Invalid parent version {parent!r}; expected 'vNNN' or None.",
            value=parent,
            hint="Pass None to derive the parent automatically.",
        )
    repo = require_own_repo(root)
    if not project.song_path.is_file():
        raise MuestraError.one(
            "file_not_found",
            f"{SONG_FILE} does not exist in {root}; nothing to version.",
            path=SONG_FILE,
            hint="Create song.yaml before rendering.",
        )
    tags = version_tags(root)
    if version in tags:
        raise MuestraError.one(
            "version_exists",
            f"Tag {version} already exists.",
            value=version,
            hint=f"Version ids are never reused; allocate a fresh one with next_version_id. "
            f"If the tag was made by hand and is wrong, delete it with 'git tag -d {version}'.",
        )
    if parent is None:
        parent = suggest_parent(project, tags)
    message = build_commit_message(
        version, summary=summary, request=request, agent=agent, parent=parent
    )
    ident = _identity_config(root)
    sha, previous = _commit_versioned(root, message, allow_empty=True, ref=repo.branch, ident=ident)
    assert sha is not None
    subject = message.split("\n", 1)[0]
    try:
        tag_version(root, version, subject, commit=sha, ident=ident)
    except MuestraError:
        ref = _branch_ref(root)
        if ref and previous:  # undo our commit so a failed tag leaves no stray version
            _git(root, "update-ref", ref, previous, sha, config=ident, check=False)
            _git(root, "reset", "-q", "--", *VERSIONED_PATHS, check=False)
        raise
    return sha


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RestoreOutcome:
    """What :func:`restore_files` did."""

    version: str
    snapshot_source: str  # "renders" | "git"
    previous_commit: str | None  # HEAD before anything happened
    snapshot_commit: str | None  # commit holding the user's uncommitted work, if any
    restore_commit: str  # the "muestra: restore vNNN" commit
    pre_restore_commit: str | None  # where to go back to (snapshot or previous HEAD)
    changed: bool  # working files differed from the version
    files: list[str]  # project-relative files written/removed
    warnings: list[str]
    undo_paths: list[str] = field(default_factory=lambda: [SONG_FILE])


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".muestra-tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        path.write_bytes(data)


def restore_files(project: Project, version: str) -> RestoreOutcome:
    """Put ``song.yaml`` (and ``midi/``) back to ``version``, byte-for-byte,
    without losing anything.

    1. Anything uncommitted in the versioned files is first committed as an
       untagged snapshot commit (``muestra: snapshot before restoring vNNN``).
    2. ``song.yaml`` is written from ``renders/vNNN/song.yaml`` (else the
       tag's blob); ``midi/`` is made to match the tag's ``midi/``.
    3. The result is committed as ``muestra: restore vNNN`` (trailer
       ``Muestra-Restored-From``), which also tells :func:`suggest_parent`
       that the next render descends from ``version``.

    Nothing is deleted without a commit holding it, so undoing is
    ``git restore --source=<pre_restore_commit> -- song.yaml midi``.
    """
    root = project.root
    if not _VERSION_ID_RE.match(version):
        raise MuestraError.one(
            "invalid_value",
            f"Invalid version id {version!r}; expected 'vNNN'.",
            value=version,
            hint="Use ids like 'v001' (see versions.list).",
        )
    repo = require_own_repo(root)
    data, source = snapshot_bytes(project, version)
    commit = version_tags(root).get(version)
    ident = _identity_config(root)
    warnings: list[str] = []
    before_song = project.song_path.read_bytes() if project.song_path.is_file() else None

    previous = _head_sha(root)
    snap_sha, _ = _commit_versioned(
        root,
        f"muestra: snapshot before restoring {version}\n\n"
        f"Muestra-Agent: muestra\nMuestra-Snapshot-For: {version}\n",
        allow_empty=False,
        ref=repo.branch,
        ident=ident,
    )
    pre_restore = snap_sha or previous

    written: list[str] = []
    _write_atomic(project.song_path, data)
    written.append(SONG_FILE)
    changed = before_song != data

    if commit is not None:
        listing = _git(root, "ls-tree", "-r", "-z", "--name-only", commit, "--", "midi")
        wanted = [p for p in _text(listing.stdout).split("\0") if p]
        for rel in wanted:
            blob = _git(root, "cat-file", "blob", f"{commit}:{rel}").stdout
            dest = root / rel
            if not dest.is_file() or dest.read_bytes() != blob:
                _write_atomic(dest, blob)
                written.append(rel)
                changed = True
        midi_dir = root / "midi"
        if midi_dir.is_dir():
            keep = set(wanted)
            # only delete what a commit can bring back (never ignored/untracked-ignored files)
            held = _git(
                root, "ls-tree", "-r", "-z", "--name-only", pre_restore or "HEAD", "--", "midi"
            )
            recoverable = {p for p in _text(held.stdout).split("\0") if p} if pre_restore else set()
            for p in sorted(midi_dir.rglob("*")):
                rel = p.relative_to(root).as_posix()
                if p.is_file() and rel not in keep and rel in recoverable:
                    p.unlink()
                    written.append(rel)
                    changed = True
    else:
        warnings.append(
            f"No git tag {version}: song.yaml was restored from renders/, but midi/ files "
            "were left as they are."
        )

    body = (
        f"muestra: restore {version}\n\nMuestra-Restored-From: {version}\nMuestra-Agent: muestra\n"
    )
    undo_paths = [SONG_FILE]
    if pre_restore and _git_out(root, "ls-tree", "--name-only", pre_restore, "--", "midi"):
        undo_paths.append("midi")  # 'git restore' rejects pathspecs that match nothing
    restore_sha, _ = _commit_versioned(root, body, allow_empty=True, ref=repo.branch, ident=ident)
    assert restore_sha is not None
    return RestoreOutcome(
        version=version,
        snapshot_source=source,
        previous_commit=previous,
        snapshot_commit=snap_sha,
        restore_commit=restore_sha,
        pre_restore_commit=pre_restore,
        changed=changed,
        files=written,
        warnings=warnings,
        undo_paths=undo_paths,
    )
