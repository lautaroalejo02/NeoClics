"""Where pad banks and takes live (WP17): ``pads/`` beside song.yaml.

* ``pads/banks.json`` (PadsFile) and ``pads/takes/<id>.json`` (Take) are
  plain JSON, LF, written atomically (temp file + replace) under a lock.
* Versioned: version commits include ``pads/`` (``VERSIONED_PATHS``), but a
  restore never rewinds it — a take is never lost by going back to v003.
* Deleting a take moves it to ``pads/takes/deleted/`` (nothing he played is
  ever destroyed by one click).
* The browser saves a take while it is being recorded (``status:
  recording``) at every loop pass and every few seconds, so a reload or a
  crash mid-take loses at most that interval; the UI also keeps a local copy.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from ..errors import MuestraError, issues_from_validation_error
from ..project.core import Project
from ..schemas.pads import PadBank, PadsFile, Take

__all__ = [
    "PADS_DIR",
    "pads_lock",
    "load_banks",
    "save_banks",
    "default_banks",
    "list_takes",
    "load_take",
    "save_take",
    "delete_take",
    "next_take_id",
]

PADS_DIR = "pads"
BANKS_FILE = "pads/banks.json"
TAKES_DIR = "pads/takes"
DELETED_DIR = "pads/takes/deleted"
_TAKE_FILE_RE = re.compile(r"^(t[0-9]{3,})\.json$")


@contextmanager
def pads_lock(project: Project):
    import portalocker

    path = project.cache_dir / "locks" / "pads.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(path, timeout=30):
        yield


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def default_banks() -> PadsFile:
    return PadsFile(
        active_bank="a",
        banks=[PadBank(id=b, name=f"Banco {b.upper()}") for b in ("a", "b", "c", "d")],
    )


def load_banks(project: Project) -> PadsFile:
    path = project.root / BANKS_FILE
    if not path.is_file():
        return default_banks()
    try:
        return PadsFile.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise MuestraError(
            issues_from_validation_error(exc, path_prefix=BANKS_FILE),
            hint="Fix pads/banks.json (or save the banks again from the UI).",
        ) from exc


def save_banks(project: Project, banks: PadsFile) -> Path:
    path = project.root / BANKS_FILE
    with pads_lock(project):
        _write_json(path, banks.model_dump(mode="json", exclude_defaults=False))
    return path


def _take_path(project: Project, take_id: str) -> Path:
    return project.root / TAKES_DIR / f"{take_id}.json"


def list_takes(project: Project) -> tuple[list[Take], list[dict]]:
    """Every take (oldest first) and the files that could not be read."""
    d = project.root / TAKES_DIR
    takes, problems = [], []
    if not d.is_dir():
        return takes, problems
    for p in sorted(d.iterdir()):
        if not p.is_file() or not _TAKE_FILE_RE.match(p.name):
            continue
        try:
            takes.append(Take.model_validate_json(p.read_bytes()))
        except (ValidationError, OSError) as exc:
            problems.append({"path": f"{TAKES_DIR}/{p.name}", "message": str(exc)[:300]})
    takes.sort(key=lambda t: (int(t.id[1:]), t.id))
    return takes, problems


def load_take(project: Project, take_id: str) -> Take:
    path = _take_path(project, take_id)
    if not path.is_file():
        raise MuestraError.one(
            "ref_not_found",
            f"No existe la toma {take_id}.",
            path="take_id",
            value=take_id,
            hint="List the takes with pads.get.",
        )
    try:
        return Take.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise MuestraError(
            issues_from_validation_error(exc, path_prefix=f"{TAKES_DIR}/{take_id}.json"),
            hint="The take file is damaged; fix or delete it.",
        ) from exc


def save_take(project: Project, take: Take) -> Path:
    path = _take_path(project, take.id)
    with pads_lock(project):
        _write_json(path, take.model_dump(mode="json"))
    return path


def delete_take(project: Project, take_id: str) -> str:
    """Move the take to ``pads/takes/deleted/`` (returns the new relative path)."""
    path = _take_path(project, take_id)
    if not path.is_file():
        raise MuestraError.one(
            "ref_not_found", f"No existe la toma {take_id}.", path="take_id", value=take_id
        )
    with pads_lock(project):
        dest_dir = project.root / DELETED_DIR
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{take_id}-{uuid.uuid4().hex[:6]}.json"
        os.replace(path, dest)
    return dest.relative_to(project.root).as_posix()


def next_take_id(project: Project) -> str:
    """t001, t002, ... (max+1 over live and deleted takes; never reused)."""
    best = 0
    for sub in (TAKES_DIR, DELETED_DIR):
        d = project.root / sub
        if not d.is_dir():
            continue
        for p in d.iterdir():
            m = re.match(r"^t([0-9]{3,})", p.name)
            if m:
                best = max(best, int(m.group(1)))
    return f"t{best + 1:03d}"
