"""Transactional edits of song.yaml (WP16): lock -> apply -> validate -> write.

* **Atomic.** All ops of a call apply to a copy of the round-trip document; the
  result is validated with the normal validator and written in one replace, or
  nothing is written.
* **Only new problems block.** An edit is rejected if it introduces a
  validation issue the song did not already have (an agent may have left the
  song half-finished; unrelated edits still work).
* **Concurrency rule.** Every op is id-addressed and may carry ``expect``.
  ``base_revision`` is the revision the client edited; when the file changed
  since (agent edit, restore) the ops are re-applied on the current file and
  succeed iff every ``expect`` still holds (``rebased: true``); otherwise the
  whole call fails with ``edit_conflict`` and the client reloads.
* **Byte-identical undo.** Every write journals the before/after bytes under
  ``cache/edits/``. When the semantic result of a call equals the content
  right before/after a journaled edit adjacent to the current revision (the
  normal undo/redo case), those exact bytes are written: comments, quoting
  and layout come back exactly.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from ..errors import Issue, MuestraError, issues_from_validation_error
from ..project.core import Project
from ..project.validate import load_song_document, validate_song_model
from ..schemas.song import Song
from .ops import DocumentOp, apply_ops
from .yamlio import dump_bytes, load_bytes, revision_of, to_plain

__all__ = ["EditOutcome", "apply_edit", "current_revision", "song_lock", "journal_dir"]

#: journaled contents kept (each is one song.yaml; tiny).
JOURNAL_KEEP = 400


def journal_dir(project: Project) -> Path:
    return project.cache_dir / "edits"


def current_revision(project: Project) -> str:
    return revision_of(project.song_path.read_bytes())


@contextmanager
def song_lock(project: Project):
    import portalocker

    path = project.cache_dir / "locks" / "song.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(path, timeout=30):
        yield


def _store(project: Project, data: bytes) -> str:
    rev = revision_of(data)
    d = journal_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{rev}.yaml"
    if not path.is_file():
        tmp = d / f"{rev}.{uuid.uuid4().hex}.tmp"
        tmp.write_bytes(data)
        tmp.replace(path)
    return rev


def _journal(project: Project) -> list[dict]:
    path = journal_dir(project) / "journal.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-JOURNAL_KEEP:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append_journal(project: Project, before: str, after: str, summary: list[str]) -> None:
    d = journal_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "before": before,
        "after": after,
        "changes": summary,
    }
    with (d / "journal.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _prune(project)


def _prune(project: Project) -> None:
    d = journal_dir(project)
    entries = _journal(project)
    if len(entries) < JOURNAL_KEEP:
        return
    keep = {e["before"] for e in entries} | {e["after"] for e in entries}
    for p in d.glob("*.yaml"):
        if p.stem not in keep:
            p.unlink(missing_ok=True)
    (d / "journal.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries[-JOURNAL_KEEP // 2 :]),
        encoding="utf-8",
        newline="\n",
    )


def _snap_candidates(project: Project, rev: str, extra: list[bytes]) -> list[bytes]:
    """Contents adjacent to ``rev`` in the journal (undo/redo targets)."""
    out = list(extra)
    d = journal_dir(project)
    for e in reversed(_journal(project)):
        for other in ((e["before"],) if e["after"] == rev else ()) + (
            (e["after"],) if e["before"] == rev else ()
        ):
            p = d / f"{other}.yaml"
            if p.is_file():
                out.append(p.read_bytes())
        if len(out) > 8:
            break
    return out


def _semantic(data: bytes) -> str | None:
    try:
        song = Song.model_validate(to_plain(load_bytes(data)))
    except (ValidationError, Exception):  # noqa: BLE001 - a candidate that doesn't parse is skipped
        return None
    return json.dumps(song.model_dump(mode="json"), sort_keys=True)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: a reader holds the file for a moment
            import time

            time.sleep(0.02 * (attempt + 1))
    tmp.unlink(missing_ok=True)
    raise MuestraError.one(
        "file_busy",
        "No pude guardar song.yaml: otro programa lo tiene abierto.",
        path="song.yaml",
        hint="Close the program holding song.yaml and retry.",
    )


def _key(i: Issue) -> tuple:
    return (i.code, i.path, i.message)


class EditOutcome:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def apply_edit(
    project: Project,
    ops: list,
    *,
    base_revision: str | None = None,
    dry_run: bool = False,
    snapshot: bytes | None = None,
) -> EditOutcome:
    """Apply ``ops`` atomically; see the module docstring for the rules.
    ``snapshot`` (revert_to) is written byte-for-byte when it matches."""
    from ..versions.describe import describe_song

    with song_lock(project):
        before_bytes = project.song_path.read_bytes()
        before_rev = revision_of(before_bytes)
        doc = load_song_document(project)
        try:
            before_song = Song.model_validate(to_plain(doc))
            before_issues = {
                _key(i) for i in validate_song_model(project, before_song, check_plugins=False)
            }
        except ValidationError:
            before_song = None
            before_issues = set()
        applied, inverse = apply_ops(doc, ops)
        plain = to_plain(doc)
        try:
            after_song = Song.model_validate(plain)
        except ValidationError as exc:
            raise _rejected(issues_from_validation_error(exc)) from exc
        issues = validate_song_model(project, after_song, check_plugins=False)
        new = [i for i in issues if _key(i) not in before_issues]
        if new:
            raise _rejected(new)
        data = dump_bytes(doc)
        target = json.dumps(after_song.model_dump(mode="json"), sort_keys=True)
        if _semantic(data) != target:
            # never write a file that does not read back as the intended song
            raise MuestraError.one(
                "internal_error",
                "No pude escribir la edición sin romper el formato de song.yaml; no cambié nada.",
                path="song.yaml",
                hint="Report it; edit song.yaml by hand meanwhile.",
            )
        for cand in _snap_candidates(project, before_rev, [snapshot] if snapshot else []):
            if _semantic(cand) == target:
                data = cand
                break
        after_rev = revision_of(data)
        changes = describe_song(before_song, after_song) if before_song is not None else []
        if not dry_run and data != before_bytes:
            _store(project, before_bytes)
            _store(project, data)
            _write_atomic(project.song_path, data)
            _append_journal(project, before_rev, after_rev, changes)
        return EditOutcome(
            revision=after_rev if not dry_run else before_rev,
            base_revision=before_rev,
            rebased=bool(base_revision and base_revision != before_rev),
            applied=applied,
            inverse=inverse,
            song=after_song,
            changes=changes,
            changed=data != before_bytes,
        )


def _rejected(issues: list[Issue]) -> MuestraError:
    lead = Issue(
        path=issues[0].path if issues else "",
        code="edit_rejected",
        message="No apliqué la edición: dejaría la canción inválida ("
        + "; ".join(i.message for i in issues[:2])
        + (f"; y {len(issues) - 2} problemas más" if len(issues) > 2 else "")
        + ").",
        hint="Nothing was written. Fix the op (see the following issues) and retry.",
    )
    return MuestraError([lead, *issues], hint="Nothing was written; song.yaml is unchanged.")


def revert_ops(project: Project, version: str) -> tuple[list, bytes]:
    """``document`` op restoring ``version``'s song (and its exact bytes)."""
    from ..versions.core import snapshot_bytes

    data, _ = snapshot_bytes(project, version)
    return [DocumentOp(value=to_plain(load_bytes(data)))], data
