"""Atomic versions: render into a hidden staging folder, then publish.

A version must never exist without its audio (WP13 finding 1). The ``render``
tool therefore:

1. renders mix + stems + ``song.yaml`` into ``renders/.staging/<token>/``
   while holding ``renders/.staging/<token>.lock`` (proof the owner is alive);
2. under the project-wide ``renders/.staging/version.lock``: allocates the id,
   writes ``pending.json`` (``{"version"}``), records the version (commit +
   tag), adds the commit to ``pending.json``, writes ``meta.json`` and moves
   the folder to ``renders/vNNN`` in one rename;
3. runs ears on the published folder (metrics are optional and can always be
   regenerated).

Any exception (including ``KeyboardInterrupt``) before the move undoes the
commit and tag and deletes the staging folder. A hard kill leaves a staging
folder whose lock nobody holds; :func:`recover` (run at the start of every
render and when a server starts) rolls it back with this rule:

* a staging folder whose lock can be taken belongs to a dead process;
* if its ``pending.json`` names a version whose ``renders/vNNN`` does not
  exist, that version never had audio: its tag is deleted (when it still
  points at the recorded commit, or at a version commit when the commit was
  not recorded yet) and the branch is moved back when that commit is still
  its tip; the folder is deleted.

Ids are allocated max+1 and a rolled-back id can be allocated again: no render
of it ever existed, so no human can have heard, rated or commented it.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import portalocker

from ..project.core import Project

STAGING = ".staging"
NOT_CREATED = "No version was created (no commit, no tag, no renders/ folder)."


def staging_root(project: Project) -> Path:
    return project.renders_dir / STAGING


def _write_pending(directory: Path, data: dict) -> None:
    path = directory / "pending.json"
    temporary = directory / "pending.json.tmp"
    temporary.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _retry(action, attempts: int = 50, delay: float = 0.1):
    """Windows: antivirus/indexers briefly hold new files; renames then fail."""
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
    return None


class Stage:
    """One render in progress: its folder and the lock that proves we're alive."""

    def __init__(self, project: Project):
        self.project = project
        self.token = uuid.uuid4().hex[:16]
        root = staging_root(project)
        root.mkdir(parents=True, exist_ok=True)
        self.lock_path = root / f"{self.token}.lock"
        self.lock = portalocker.Lock(self.lock_path, timeout=0, fail_when_locked=True)
        self.lock.acquire()
        self.dir = root / self.token
        self.dir.mkdir()
        self.version: str | None = None
        self.commit: str | None = None
        self.rerender = False
        self.published = False

    def mark(self, version: str, commit: str | None = None, *, rerender: bool = False) -> None:
        self.version, self.commit, self.rerender = version, commit, rerender
        _write_pending(self.dir, {"version": version, "commit": commit, "rerender": rerender})

    def publish_new(self, target: Path) -> None:
        """Move the finished folder to ``renders/vNNN`` (one rename)."""
        _retry(lambda: os.replace(self.dir, target))
        self.published = True
        (target / "pending.json").unlink(missing_ok=True)

    def publish_over(self, target: Path) -> None:
        """Re-render: replace an existing version's files one by one.

        The UI may be streaming the old audio (Windows cannot rename a folder
        with open files); files are replaced individually, the old render stays
        intact until everything new exists."""
        target.mkdir(parents=True, exist_ok=True)
        (self.dir / "pending.json").unlink(missing_ok=True)
        new_stems = self.dir / "stems"
        old_stems = target / "stems"
        if old_stems.is_dir():
            keep = {p.name for p in new_stems.glob("*.wav")} if new_stems.is_dir() else set()
            for old in old_stems.glob("*.wav"):
                if old.name not in keep:
                    with contextlib.suppress(OSError):
                        old.unlink()
        for path in sorted(self.dir.rglob("*")):
            if not path.is_file():
                continue
            destination = target / path.relative_to(self.dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                _retry(lambda p=path, d=destination: os.replace(p, d), attempts=10)
            except PermissionError:
                shutil.copyfile(path, destination)  # open for reading elsewhere
        self.published = True

    def close(self) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)
        with contextlib.suppress(Exception):
            self.lock.release()
        with contextlib.suppress(OSError):
            self.lock_path.unlink()


@contextlib.contextmanager
def stage(project: Project) -> Iterator[Stage]:
    current = Stage(project)
    try:
        yield current
    except BaseException:
        if current.version and not current.published and not current.rerender:
            from ..versions import core as versions

            rollback_version(project, current.version, current.commit, versions)
        raise
    finally:
        current.close()


@contextlib.contextmanager
def version_lock(project: Project, timeout: float = 120) -> Iterator[None]:
    """Serialises id allocation + commit + publish across processes."""
    root = staging_root(project)
    root.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(root / "version.lock", timeout=timeout):
        yield


def rollback_version(project: Project, version: str, commit: str | None, versions) -> bool:
    """Undo a version that never got its audio. Returns True when a tag was removed."""
    from ..versions.core import _branch_ref, _git, _head_sha

    root = project.root
    tag = versions.version_tags(root).get(version)
    if tag is None:
        return False
    if commit is None:
        info = versions.commit_info(root, tag)
        if info is None or info.version != version:
            return False  # not ours: leave a hand-made tag alone
        commit = tag
    if tag != commit:
        return False
    _git(root, "tag", "-d", version, check=False)
    ref = _branch_ref(root)
    if ref and _head_sha(root) == commit:
        proc = _git(root, "rev-parse", "-q", "--verify", f"{commit}^", check=False)
        previous = proc.stdout.decode().strip() if proc.returncode == 0 else ""
        if previous:
            _git(root, "update-ref", ref, previous, commit, check=False)
    return True


def recover(project: Project) -> list[str]:
    """Clean leftovers of killed renders; returns the rolled-back version ids."""
    root = staging_root(project)
    if not root.is_dir():
        return []
    tokens = {
        p.stem if p.suffix == ".lock" else p.name
        for p in root.iterdir()
        if p.name != "version.lock" and (p.is_dir() or p.suffix == ".lock")
    }
    if not tokens:
        return []
    rolled = []
    # Under the version lock: a live render can't be between tag and publish.
    with version_lock(project):
        for token in sorted(tokens):
            directory, lock_path = root / token, root / f"{token}.lock"
            try:
                probe = portalocker.Lock(lock_path, timeout=0, fail_when_locked=True)
                probe.acquire()
            except (portalocker.exceptions.LockException, OSError):
                continue  # a live render owns it
            try:
                version = _rollback_pending(project, directory)
                if version:
                    rolled.append(version)
                if directory.is_dir():
                    shutil.rmtree(directory, ignore_errors=True)
            finally:
                with contextlib.suppress(Exception):
                    probe.release()
                with contextlib.suppress(OSError):
                    lock_path.unlink()
    return rolled


def _rollback_pending(project: Project, directory: Path) -> str | None:
    pending = directory / "pending.json"
    if not pending.is_file():
        return None
    try:
        data = json.loads(pending.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version")
    if (
        not isinstance(version, str)
        or data.get("rerender")
        or (project.renders_dir / version).exists()
    ):
        return None
    from ..versions import core as versions

    return version if rollback_version(project, version, data.get("commit"), versions) else None
